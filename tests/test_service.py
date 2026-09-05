import uuid
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String

from zcore.db.pagination import PaginatedResult
from zcore.db.search import SearchRequest
from zcore.db.setup import Base
from zcore.exceptions.base import EntityNotFound
from zcore.service.base import BaseService


class ServiceTestModel(Base):
    __tablename__ = f"service_test_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    name = Column(String)


class ServiceTestCreateSchema(BaseModel):
    name: str


class ServiceTestUpdateSchema(BaseModel):
    name: str


class HookTrackingService(BaseService[ServiceTestModel]):
    def __init__(self, model: type[ServiceTestModel], repository: Any) -> None:
        super().__init__(model, repository)
        self.hooks_called: list[str] = []

    async def post_get(self, model: ServiceTestModel) -> ServiceTestModel:
        self.hooks_called.append("post_get")
        model.name = model.name + "_GETHOOKED"
        return model

    async def post_get_multi(self, models: Sequence[ServiceTestModel]) -> Sequence[ServiceTestModel]:
        self.hooks_called.append("post_get_multi")
        for m in models:
            m.name = m.name + "_GETMULTIHOCKED"
        return models

    async def pre_create(self, schema: ServiceTestCreateSchema) -> dict[str, Any] | None:
        self.hooks_called.append("pre_create")
        schema.name = "HOOKED_" + schema.name
        if schema.name == "HOOKED_Base":
            return {"audit_flag": True}
        return None

    async def post_create(self, model: ServiceTestModel) -> None:
        self.hooks_called.append("post_create")
        model.name = model.name + "_AUDITED"

    async def pre_create_multi(self, schemas: list[BaseModel]) -> None:
        self.hooks_called.append("pre_create_multi")
        for s in schemas:
            s.name = "BULK_" + s.name

    async def post_create_multi(self, models: Sequence[ServiceTestModel]) -> None:
        self.hooks_called.append("post_create_multi")
        for m in models:
            m.name = m.name + "_BULKAUDITED"

    async def pre_update(self, target: ServiceTestModel | Any, schema: ServiceTestUpdateSchema, partial: bool) -> dict[str, Any] | None:
        self.hooks_called.append("pre_update")
        schema.name = "UPDATED_HOOK_" + schema.name
        return None

    async def post_update(self, model: ServiceTestModel) -> None:
        self.hooks_called.append("post_update")
        model.name = model.name + "_UPDATED"

    async def pre_update_multi(self, data: dict[ServiceTestModel | Any, BaseModel], partial: bool) -> None:
        self.hooks_called.append("pre_update_multi")
        for _k, v in data.items():
            v.name = "BULK_UPD_" + v.name

    async def post_update_multi(self, models: Sequence[ServiceTestModel]) -> None:
        self.hooks_called.append("post_update_multi")
        for m in models:
            m.name = m.name + "_BULKUPDATED"

    async def pre_delete(self, target: Any) -> None:
        self.hooks_called.append("pre_delete")

    async def post_delete(self, model: ServiceTestModel) -> None:
        self.hooks_called.append("post_delete")
        model.name = model.name + "_DELETED"

    async def pre_delete_multi(self, ids: list[Any]) -> None:
        self.hooks_called.append("pre_delete_multi")

    async def post_delete_multi(self, models: Sequence[ServiceTestModel]) -> None:
        self.hooks_called.append("post_delete_multi")
        for m in models:
            m.name = m.name + "_BULKDELETED"

    async def pre_search(self, search_in: SearchRequest) -> None:
        self.hooks_called.append("pre_search")

    async def post_search(self, models: Sequence[ServiceTestModel]) -> None:
        self.hooks_called.append("post_search")
        for m in models:
            m.name = m.name + "_SEARCHED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "initial_name, expected_repo_name, expected_final_name",
    [
        ("sample", "HOOKED_sample", "HOOKED_sample_AUDITED"),
        ("another", "HOOKED_another", "HOOKED_another_AUDITED"),
    ]
)
async def test_service_pre_post_hooks(
    initial_name: str,
    expected_repo_name: str,
    expected_final_name: str
) -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    async def fake_create(schema: ServiceTestCreateSchema, **extra: Any) -> ServiceTestModel:
        return ServiceTestModel(id=1, name=schema.name)
        
    mock_repo.create.side_effect = fake_create

    service = HookTrackingService(ServiceTestModel, mock_repo)
    schema = ServiceTestCreateSchema(name=initial_name)
    
    result = await service.create(schema)
    
    assert "pre_create" in service.hooks_called
    assert "post_create" in service.hooks_called
    assert result.name == expected_final_name
    mock_repo.create.assert_called_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "uow_managed, should_commit, raise_commit_error",
    [
        (True, False, False),
        (False, True, False),
        (None, True, False),
        (False, True, True),
    ]
)
async def test_service_safe_commit_scenarios(
    uow_managed: bool | None,
    should_commit: bool,
    raise_commit_error: bool
) -> None:
    mock_db = AsyncMock()
    mock_db.info = {}
    if uow_managed is not None:
        mock_db.info["uow_managed"] = uow_managed

    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    record = ServiceTestModel(id=1, name="Original")
    mock_repo.create.return_value = record

    if raise_commit_error:
        mock_db.commit.side_effect = Exception("Database disk full")

    service = HookTrackingService(ServiceTestModel, mock_repo)
    schema = ServiceTestCreateSchema(name="Fresh")

    if raise_commit_error:
        with pytest.raises(Exception) as exc_info:
            await service.create(schema)
        assert "Database disk full" in str(exc_info.value)
        mock_db.rollback.assert_called_once()
    else:
        await service.create(schema)
        if should_commit:
            mock_db.commit.assert_called_once()
            mock_db.rollback.assert_not_called()
        else:
            mock_db.commit.assert_not_called()
            mock_db.rollback.assert_not_called()


@pytest.mark.anyio
async def test_service_post_get_hook() -> None:
    mock_repo = AsyncMock()
    record = ServiceTestModel(id=1, name="Database")
    mock_repo.get.return_value = record
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    result = await service.get(id=1)
    
    assert "post_get" in service.hooks_called
    assert result.name == "Database_GETHOOKED"


@pytest.mark.anyio
async def test_service_post_get_multi_hook() -> None:
    mock_repo = AsyncMock()
    records = [ServiceTestModel(id=1, name="R1"), ServiceTestModel(id=2, name="R2")]
    mock_repo.get_by_ids.return_value = records
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    results = await service.get_by_ids(ids=[1, 2])
    
    assert "post_get_multi" in service.hooks_called
    assert results[0].name == "R1_GETMULTIHOCKED"
    assert results[1].name == "R2_GETMULTIHOCKED"


@pytest.mark.anyio
async def test_service_get_entity_not_found() -> None:
    mock_repo = AsyncMock()
    mock_repo.get.return_value = None
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    with pytest.raises(EntityNotFound):
        await service.get(id=999)
        
    assert "post_get" not in service.hooks_called


@pytest.mark.anyio
async def test_service_get_list_pagination() -> None:
    mock_repo = AsyncMock()
    records = [ServiceTestModel(id=1, name="R1"), ServiceTestModel(id=2, name="R2")]
    paginated = PaginatedResult(data=records, meta={"page": 1})
    mock_repo.get_list.return_value = paginated
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    result = await service.get_list(pagination=MagicMock())
    
    assert "post_get_multi" in service.hooks_called
    assert result.data[0].name == "R1_GETMULTIHOCKED"
    assert result.data[1].name == "R2_GETMULTIHOCKED"
    assert result.meta["page"] == 1


@pytest.mark.anyio
async def test_service_pre_create_extra_data_merge() -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    async def fake_create(schema: ServiceTestCreateSchema, **extra: Any) -> ServiceTestModel:
        assert extra.get("audit_flag") is True
        assert extra.get("extra_field") == "yes"
        return ServiceTestModel(id=1, name=schema.name)
        
    mock_repo.create.side_effect = fake_create
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    schema = ServiceTestCreateSchema(name="Base")
    await service.create(schema, extra_field="yes")
    
    assert "pre_create" in service.hooks_called


@pytest.mark.anyio
async def test_service_create_multi_hooks() -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    async def fake_create_multi(schemas: list[BaseModel], refresh: bool = False) -> Sequence[ServiceTestModel]:
        return [ServiceTestModel(id=i, name=s.name) for i, s in enumerate(schemas, 1)]
        
    mock_repo.create_multi.side_effect = fake_create_multi
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    schemas = [ServiceTestCreateSchema(name="S1"), ServiceTestCreateSchema(name="S2")]
    results = await service.create_multi(schemas)
    
    assert "pre_create_multi" in service.hooks_called
    assert "post_create_multi" in service.hooks_called
    assert results[0].name == "BULK_S1_BULKAUDITED"
    assert results[1].name == "BULK_S2_BULKAUDITED"


@pytest.mark.anyio
async def test_service_update_multi_hooks() -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    async def fake_update_multi(data: dict[ServiceTestModel | Any, BaseModel], partial: bool = False, refresh: bool = False) -> Sequence[ServiceTestModel]:
        return [ServiceTestModel(id=getattr(k, "id", k), name=v.name) for k, v in data.items()]
        
    mock_repo.update_multi.side_effect = fake_update_multi
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    updates = {
        1: ServiceTestUpdateSchema(name="U1"),
        2: ServiceTestUpdateSchema(name="U2")
    }
    results = await service.update_multi(updates)
    
    assert "pre_update_multi" in service.hooks_called
    assert "post_update_multi" in service.hooks_called
    assert results[0].name == "BULK_UPD_U1_BULKUPDATED"
    assert results[1].name == "BULK_UPD_U2_BULKUPDATED"


@pytest.mark.anyio
async def test_service_update_entity_not_found() -> None:
    mock_repo = AsyncMock()
    mock_repo.update.return_value = None
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    schema = ServiceTestUpdateSchema(name="Fresh")
    
    with pytest.raises(EntityNotFound):
        await service.update(target=999, schema=schema)
        
    assert "post_update" not in service.hooks_called


@pytest.mark.anyio
async def test_service_delete_hooks() -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    record = ServiceTestModel(id=1, name="ToKill")
    mock_repo.delete.return_value = record
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    result = await service.delete(target=1)
    
    assert "pre_delete" in service.hooks_called
    assert "post_delete" in service.hooks_called
    assert result.name == "ToKill_DELETED"


@pytest.mark.anyio
async def test_service_delete_multi_hooks() -> None:
    mock_db = MagicMock()
    mock_db.info = {"uow_managed": True}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    records = [ServiceTestModel(id=1, name="K1"), ServiceTestModel(id=2, name="K2")]
    mock_repo.delete_multi.return_value = records
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    results = await service.delete_multi(ids=[1, 2])
    
    assert "pre_delete_multi" in service.hooks_called
    assert "post_delete_multi" in service.hooks_called
    assert results[0].name == "K1_BULKDELETED"
    assert results[1].name == "K2_BULKDELETED"


@pytest.mark.anyio
async def test_service_delete_entity_not_found() -> None:
    mock_repo = AsyncMock()
    mock_repo.delete.return_value = None
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    
    with pytest.raises(EntityNotFound):
        await service.delete(target=999)
        
    assert "post_delete" not in service.hooks_called


@pytest.mark.anyio
async def test_service_search_hooks() -> None:
    mock_repo = AsyncMock()
    records = [ServiceTestModel(id=1, name="Found1"), ServiceTestModel(id=2, name="Found2")]
    mock_repo.search.return_value = records
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    search_request = SearchRequest(filters=[])
    results = await service.search(search_in=search_request)
    
    assert "pre_search" in service.hooks_called
    assert "post_search" in service.hooks_called
    assert results[0].name == "Found1_SEARCHED"
    assert results[1].name == "Found2_SEARCHED"


@pytest.mark.anyio
async def test_service_search_pagination() -> None:
    mock_repo = AsyncMock()
    records = [ServiceTestModel(id=1, name="Found1"), ServiceTestModel(id=2, name="Found2")]
    paginated = PaginatedResult(data=records, meta={"page": 1})
    mock_repo.search.return_value = paginated
    
    service = HookTrackingService(ServiceTestModel, mock_repo)
    search_request = SearchRequest(filters=[])
    result = await service.search(search_in=search_request, pagination=MagicMock())
    
    assert "pre_search" in service.hooks_called
    assert "post_search" in service.hooks_called
    assert result.data[0].name == "Found1_SEARCHED"
    assert result.data[1].name == "Found2_SEARCHED"
    assert result.meta["page"] == 1


@pytest.mark.anyio
async def test_service_post_create_error_rollback() -> None:
    mock_db = AsyncMock()
    mock_db.info = {"uow_managed": False}
    mock_repo = AsyncMock()
    mock_repo.db = mock_db
    
    record = ServiceTestModel(id=1, name="Partial")
    mock_repo.create.return_value = record
    
    class ErrorInPostCreateService(HookTrackingService):
        async def post_create(self, model: ServiceTestModel) -> None:
            raise RuntimeError("Failed after db write")
            
    service = ErrorInPostCreateService(ServiceTestModel, mock_repo)
    schema = ServiceTestCreateSchema(name="Fresh")
    
    with pytest.raises(RuntimeError):
        await service.create(schema)
        
    mock_db.commit.assert_not_called()
    mock_db.rollback.assert_not_called()