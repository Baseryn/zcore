import uuid
from typing import Any, Type
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String

from zcore.db.setup import Base
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
    def __init__(self, model: Type[ServiceTestModel], repository: Any) -> None:
        super().__init__(model, repository)
        self.hooks_called: list[str] = []

    async def pre_create(self, schema: ServiceTestCreateSchema) -> None:
        self.hooks_called.append("pre_create")
        schema.name = "HOOKED_" + schema.name

    async def post_create(self, model: ServiceTestModel) -> None:
        self.hooks_called.append("post_create")
        model.name = model.name + "_AUDITED"

    async def pre_update(self, id: Any, schema: ServiceTestUpdateSchema, partial: bool) -> None:
        self.hooks_called.append("pre_update")
        schema.name = "UPDATED_HOOK_" + schema.name

    async def post_update(self, model: ServiceTestModel) -> None:
        self.hooks_called.append("post_update")
        model.name = model.name + "_UPDATED"

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
    
    async def fake_create(schema: ServiceTestCreateSchema) -> ServiceTestModel:
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