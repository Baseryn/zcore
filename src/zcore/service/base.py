"""ZCore Core Business Service Layer.

This module provides the structural base service abstractions and mixins for orchestration 
of domain logic in the ZCore framework. It coordinates transactional operations, integrates 
lifecycle hooks (pre- and post-execution stages), and facilitates safe unit-of-work 
commits to prevent premature transactional boundaries from breaking transaction atomicity.
"""

from pydantic import BaseModel
from typing import Generic, TypeVar, Type, Any, Sequence, Optional, List, Dict, TYPE_CHECKING
from sqlalchemy.orm.interfaces import ExecutableOption

from zcore.exceptions.base import EntityNotFound
from zcore.db.setup import Base
from zcore.db.search import SearchRequest

if TYPE_CHECKING:
    from zcore.db.repository import BaseRepository

ModelType = TypeVar("ModelType", bound=Base)


class AbstractService(Generic[ModelType]):
    """Abstract base service interface enforcing a standard structure for domain services.

    Attributes:
        repository: The bound data persistence repository.
        model: The declarative SQLAlchemy model class matching this service domain.
    """

    repository: "BaseRepository"
    model: Type[ModelType]

    async def get(
        self, 
        *criterion: Any, 
        fields: Optional[List[Any]] = None, 
        options: Optional[List[ExecutableOption]] = None,
        **filters: Any
    ) -> ModelType:
        """Fetch a single domain entity by dynamic filters.

        Args:
            *criterion: Dynamic positional conditions.
            fields: Selective list of model fields to return. Defaults to None.
            options: Execution configurations such as load options. Defaults to None.
            **filters: Dynamic keyword filters.

        Returns:
            The retrieved model instance.
        """
        raise NotImplementedError

    async def get_by_ids(
        self, 
        ids: List[Any], 
        fields: Optional[List[Any]] = None, 
        options: Optional[List[ExecutableOption]] = None
    ) -> Sequence[ModelType]:
        """Fetch a sequence of domain entities matching the list of keys.

        Args:
            ids: List of target entity keys to retrieve.
            fields: Selective list of model fields to return. Defaults to None.
            options: Execution configurations such as load options. Defaults to None.

        Returns:
            A sequence of retrieved database model instances.
        """
        raise NotImplementedError


class ReadServiceMixin(AbstractService[ModelType]):
    """Mixin implementing standardized read-only orchestration routines."""

    async def post_get(self, model: ModelType) -> ModelType:
        """Extension interceptor executed after a single entity is retrieved."""
        return model

    async def post_get_multi(self, models: Sequence[ModelType]) -> Sequence[ModelType]:
        """Extension interceptor executed after a batch sequence is retrieved."""
        return models

    async def exist(self, *criterion: Any, **filters: Any) -> bool:
        """Verify the existence of a target entity in the database."""
        return await self.repository.exist(*criterion, **filters)

    async def get(
        self, 
        *criterion: Any, 
        fields: Optional[List[Any]] = None, 
        options: Optional[List[ExecutableOption]] = None,
        **filters: Any
    ) -> ModelType:
        """Fetch a single domain entity, applying post-retrieval hooks.

        Args:
            *criterion: Dynamic positional conditions.
            fields: Selective list of model fields to load. Defaults to None.
            options: Additional execution options. Defaults to None.
            **filters: Dynamic keyword filters.

        Returns:
            The processed database model instance.

        Raises:
            EntityNotFound: If the target entity identifier is not found in the database.
        """
        result = await self.repository.get(*criterion, fields=fields, options=options, **filters)
        if not result:
            raise EntityNotFound(message=f"{self.model.__name__} not found.")
        return await self.post_get(result)

    async def get_by_ids(
        self, 
        ids: List[Any], 
        fields: Optional[List[Any]] = None, 
        options: Optional[List[ExecutableOption]] = None
    ) -> Sequence[ModelType]:
        """Fetch multiple domain entities by their identifiers, applying post-retrieval hooks."""
        result = await self.repository.get_by_ids(ids, fields, options)
        return await self.post_get_multi(result)

    async def get_list(
        self, 
        pagination: Any = None, 
        fields: Optional[List[Any]] = None, 
        options: Optional[List[ExecutableOption]] = None,
        *criterion: Any,
        **filters: Any
    ) -> Any:
        """Fetch a paginated or complete listing of entities, applying post-retrieval hooks."""
        result = await self.repository.get_list(pagination, fields, options, *criterion, **filters)
        if pagination is None:
            return await self.post_get_multi(result)
        result.data = await self.post_get_multi(result.data)
        return result

    async def count(self, *criterion: Any, **filters: Any) -> int:
        """Count the volume of entities matching dynamic criteria."""
        return await self.repository.count(*criterion, **filters)


class WriteServiceMixin(Generic[ModelType], AbstractService[ModelType]):
    """Mixin implementing standardized mutation and persistence orchestration routines."""

    async def pre_create(self, schema: BaseModel) -> None:
        """Hook triggered prior to single-record database insertion."""
        pass

    async def post_create(self, model: ModelType) -> None:
        """Hook triggered after single-record database insertion."""
        pass
    
    async def pre_create_multi(self, schemas: List[BaseModel]) -> None:
        """Hook triggered prior to batch database insertions."""
        pass

    async def post_create_multi(self, models: Sequence[ModelType]) -> None:
        """Hook triggered after batch database insertions."""
        pass

    async def pre_update(self, id: Any, schema: BaseModel, partial: bool) -> None:
        """Hook triggered prior to modifying a single record."""
        pass

    async def post_update(self, model: ModelType) -> None:
        """Hook triggered after modifying a single record."""
        pass
    
    async def pre_update_multi(self, data: Dict[Any, BaseModel], partial: bool) -> None:
        """Hook triggered prior to updating a batch of records."""
        pass

    async def post_update_multi(self, models: Sequence[ModelType]) -> None:
        """Hook triggered after updating a batch of records."""
        pass

    async def pre_delete(self, id: Any) -> None:
        """Hook triggered prior to deleting a single record."""
        pass

    async def post_delete(self, model: ModelType) -> None:
        """Hook triggered after deleting a single record."""
        pass
    
    async def pre_delete_multi(self, ids: List[Any]) -> None:
        """Hook triggered prior to deleting a batch of records."""
        pass

    async def post_delete_multi(self, models: Sequence[ModelType]) -> None:
        """Hook triggered after deleting a batch of records."""
        pass

    async def _safe_commit(self) -> None:
        """Execute a session commit if transaction state is not managed externally."""
        session_info = self.repository.db.info
        if not session_info.get("uow_managed", False):
            try:
                await self.repository.db.commit()
            except Exception:
                await self.repository.db.rollback()
                raise

    async def on_create(self, schema: BaseModel) -> ModelType:
        """Execute core single-record creation in database."""
        return await self.repository.create(schema)

    async def on_create_multi(self, schemas: List[BaseModel], refresh: bool = False) -> Sequence[ModelType]:
        """Execute core batch-record creation in database."""
        return await self.repository.create_multi(schemas, refresh=refresh)

    async def on_update(self, id: Any, schema: BaseModel, partial: bool = False) -> Optional[ModelType]:
        """Execute core single-record update in database."""
        return await self.repository.update(id, schema, partial)

    async def on_update_multi(
        self, 
        data: Dict[Any, BaseModel], 
        partial: bool = False, 
        refresh: bool = False
    ) -> Sequence[ModelType]:
        """Execute core batch-record updates in database."""
        return await self.repository.update_multi(data, partial, refresh=refresh)

    async def on_delete(self, id: Any) -> Optional[ModelType]:
        """Execute core single-record deletion in database."""
        return await self.repository.delete(id)

    async def on_delete_multi(self, ids: List[Any]) -> Sequence[ModelType]:
        """Execute core batch-record deletions in database."""
        return await self.repository.delete_multi(ids)

    async def create(self, schema: BaseModel) -> ModelType:
        """Orchestrate the creation and persistence of a new domain entity."""
        await self.pre_create(schema)
        result = await self.on_create(schema)
        await self.post_create(result)
        await self._safe_commit()
        return result

    async def create_multi(self, schemas: List[BaseModel], refresh: bool = False) -> Sequence[ModelType]:
        """Orchestrate the batch creation and persistence of multiple domain entities."""
        await self.pre_create_multi(schemas)
        result = await self.on_create_multi(schemas, refresh=refresh)
        await self.post_create_multi(result)
        await self._safe_commit()
        return result

    async def update(self, id: Any, schema: BaseModel, partial: bool = False) -> ModelType:
        """Orchestrate modifications to an existing domain entity."""
        await self.pre_update(id, schema, partial)
        result = await self.on_update(id, schema, partial)
        if not result:
            raise EntityNotFound(message=f"{self.model.__name__} not found.")
        await self.post_update(result)
        await self._safe_commit()
        return result

    async def update_multi(
        self, 
        data: Dict[Any, BaseModel], 
        partial: bool = False, 
        refresh: bool = False
    ) -> Sequence[ModelType]:
        """Orchestrate batch modifications to multiple existing domain entities."""
        await self.pre_update_multi(data, partial)
        result = await self.on_update_multi(data, partial, refresh=refresh)
        await self.post_update_multi(result)
        await self._safe_commit()
        return result

    async def delete(self, id: Any) -> ModelType:
        """Orchestrate the deletion and cleanup of a single domain entity."""
        await self.pre_delete(id)
        result = await self.on_delete(id)
        if not result:
            raise EntityNotFound(message=f"{self.model.__name__} not found.")
        await self.post_delete(result)
        await self._safe_commit()
        return result

    async def delete_multi(self, ids: List[Any]) -> Sequence[ModelType]:
        """Orchestrate batch deletions of multiple database records."""
        await self.pre_delete_multi(ids)
        result = await self.on_delete_multi(ids)
        await self.post_delete_multi(result)
        await self._safe_commit()
        return result


class SearchServiceMixin(AbstractService[ModelType]):
    """Mixin implementing dynamic, policy-validated search operations."""

    async def pre_search(self, search_in: SearchRequest) -> None:
        """Hook triggered prior to dynamic search query compilation."""
        pass

    async def post_search(self, models: Sequence[ModelType]) -> None:
        """Hook triggered after processing dynamic search queries."""
        pass

    async def on_search(self, search_in: SearchRequest, pagination: Any = None):
        """Execute core search queries in database."""
        return await self.repository.search(search_in, pagination)

    async def search(self, search_in: SearchRequest, pagination: Any = None) -> Any:
        """Build and execute dynamic filters, pre-loading patterns, and sorting paths."""
        await self.pre_search(search_in)
        result = await self.on_search(search_in, pagination)
        if pagination is None:
            await self.post_search(result)
            return result
        await self.post_search(result.data)
        return result


class BaseService(
    Generic[ModelType],
    ReadServiceMixin[ModelType],
    WriteServiceMixin[ModelType],
    SearchServiceMixin[ModelType]
):
    """The default business service implementation."""

    def __init__(self, model: Type[ModelType], repository: "BaseRepository") -> None:
        """Initialize the BaseService.

        Args:
            model: The declarative database model linked to this service.
            repository: The data persistence layer coordinating model access.
        """
        self.model = model
        self.repository = repository