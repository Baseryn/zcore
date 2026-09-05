"""ZCore Database Repository Pattern Layer.

This module provides a Repository pattern implementation for SQLAlchemy
2.0 and Pydantic V2. It decouples business logic from database interactions by exposing
highly specialized, reusable interfaces divided into read, write, and search capabilities.
It supports dynamic pagination, eager load optimization, and field pruning.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, delete, func, insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.interfaces import ExecutableOption

from zcore.db.pagination import CursorPagination, CursorParams, PageNumberPagination
from zcore.db.setup import Base

if TYPE_CHECKING:
    from zcore.db.search import SearchRequest

ModelType = TypeVar("ModelType", bound=Base)


class AbstractRepository(Generic[ModelType]):
    """Abstract base repository class defining the fundamental schema interface.

    Any custom repository class implementation in ZCore must inherit from this abstraction or
    its mixins.

    Attributes:
        db: The active asynchronous database session.
        model: The SQLAlchemy model class bound to the repository instance.
        pk: The SQLAlchemy primary key column representation of the bound model.
        pk_name: The string identifier name of the primary key field.
        cursor_field: The field name to utilize during keyset pagination lookups.
    """

    db: AsyncSession
    model: type[ModelType]
    pk: Any
    pk_name: str
    cursor_field: str

    def _get_base_query(self) -> Select:
        """Construct the initial select statement for query operations.

        Enforces context security by dynamically executing 'scope_query'
        if defined on the model layer.

        Returns:
            The base Select statement.
        """
        query = select(self.model)
        scoper = getattr(self.model, "scope_query", None)
        if scoper:
            return scoper(query)
        return query

    def _apply_filters(self, query: Select, *criterion: Any, **filters: Any) -> Select:
        """Apply dynamic criteria and keyword filters onto the Select query.

        Args:
            query: The active SQLAlchemy Select query.
            *criterion: Positional binary SQLAlchemy filter expressions.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            The configured Select query.
        """
        if criterion:
            query = query.where(*criterion)
        if filters:
            query = query.filter_by(**filters)
        return query


class ReadRepositoryMixin(AbstractRepository[ModelType]):
    """Mixin implementing core query and retrieval operations for repositories.

    Provides common read capabilities including existence checks, single record fetches,
    multi-key batch retrievals, and paginated listings.
    """

    async def exist(self, *criterion: Any, **filters: Any) -> bool:
        """Check if records matching the filters exist in the database.

        Args:
            *criterion: Positional binary SQLAlchemy filter expressions.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            True if matching records are found, False otherwise.
        """
        query = select(self.pk)
        scoper = getattr(self.model, "scope_query", None)
        if scoper:
            query = scoper(query)
        query = self._apply_filters(query, *criterion, **filters).limit(1)
        result = await self.db.execute(query)
        return result.first() is not None

    async def get(
        self,
        *criterion: Any,
        fields: list[Any] | None = None,
        options: list[ExecutableOption] | None = None,
        **filters: Any,
    ) -> ModelType | None:
        """Fetch a single record by dynamic filters.

        Args:
            *criterion: Positional binary SQLAlchemy filter expressions.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy executable options. Defaults to None.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            The retrieved model instance, or None if no matching record is found.
        """
        query = self._get_base_query()
        query = self._apply_filters(query, *criterion, **filters)
        if fields:
            query = query.options(load_only(*fields))
        if options:
            query = (
                query.options(*options)
                if isinstance(options, list)
                else query.options(options)
            )
        result = await self.db.execute(query)
        return result.scalars().first()

    async def get_by_ids(
        self,
        ids: list[Any],
        fields: list[Any] | None = None,
        options: list[ExecutableOption] | None = None,
    ) -> Sequence[ModelType]:
        """Fetch a sequence of records matching a list of primary keys.

        Args:
            ids: A list of primary key values to look up.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy executable options. Defaults to None.

        Returns:
            A sequence of retrieved database model instances.
        """
        if not ids:
            return []

        query = self._get_base_query().where(self.pk.in_(ids))
        if fields:
            query = query.options(load_only(*fields))
        if options:
            query = (
                query.options(*options)
                if isinstance(options, list)
                else query.options(options)
            )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_list(
        self,
        pagination: Any = None,
        fields: list[Any] | None = None,
        options: list[ExecutableOption] | None = None,
        *criterion: Any,
        **filters: Any,
    ) -> Any:
        """Fetch a paginated or complete list of records matching filters.

        Args:
            pagination: Pagination parameters. Defaults to None.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy execution options. Defaults to None.
            *criterion: Positional binary SQLAlchemy filter expressions.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            A list of matching records, or a paginated response container.
        """
        query = self._get_base_query()
        query = self._apply_filters(query, *criterion, **filters)
        if fields:
            query = query.options(load_only(*fields))
        if options:
            query = (
                query.options(*options)
                if isinstance(options, list)
                else query.options(options)
            )

        if pagination is None:
            result = await self.db.execute(query)
            return result.scalars().all()

        paginator = (
            CursorPagination(self.cursor_field)
            if isinstance(pagination, CursorParams)
            else PageNumberPagination()
        )
        return await paginator.paginate(self.db, query, pagination, self.model)

    async def count(self, *criterion: Any, **filters: Any) -> int:
        """Count records matching dynamic criteria.

        Args:
            *criterion: Positional binary SQLAlchemy filter expressions.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            The total volume of matching records.
        """
        base_query = self._get_base_query()
        query = self._apply_filters(base_query, *criterion, **filters)
        query = query.order_by(None)
        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        return result.scalar_one()


class WriteRepositoryMixin(Generic[ModelType], AbstractRepository[ModelType]):
    """Mixin implementing data modification and persistence operations."""

    async def create(self, schema: BaseModel, **extra_data: Any) -> ModelType:
        """Create a new database record from a validated creation schema and dynamic fields.

        Args:
            schema: The validated Pydantic model containing properties for the new record.
            **extra_data: Dynamic fields to merge with the primary payload.

        Returns:
            The created and refreshed database model instance.
        """
        data = schema.model_dump()
        data.update(extra_data)
        record = self.model(**data)
        self.db.add(record)
        await self.db.flush()
        await self.db.refresh(record)
        return record

    async def create_multi(
        self, schemas: list[BaseModel], refresh: bool = False
    ) -> Sequence[ModelType]:
        """Create multiple database records with dialect-aware fallback for returning support.

        Args:
            schemas: A list of Pydantic schemas representing the new database objects.
            refresh: If True, executes an explicit database refresh on each record individually.
                Defaults to False.

        Returns:
            A sequence of created database model instances.
        """
        if not schemas:
            return []

        payloads = [schema.model_dump() for schema in schemas]
        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        supports_returning = bool(getattr(dialect, "insert_returning", False))

        if supports_returning:
            stmt = insert(self.model).values(payloads).returning(self.model)
            result = await self.db.execute(stmt)
            await self.db.flush()
            records = list(result.scalars().all())
            if refresh:
                for r in records:
                    await self.db.refresh(r)
            return records
        else:
            records = [self.model(**p) for p in payloads]
            self.db.add_all(records)
            await self.db.flush()
            if refresh:
                for r in records:
                    await self.db.refresh(r)
            return records

    async def update(
        self, target: ModelType | Any, schema: BaseModel, partial: bool = False, **extra_data: Any,
    ) -> ModelType | None:
        """Update an existing database record from a model instance or primary key.

        Args:
            target: The model instance or primary key identifier of the record to update.
            schema: The Pydantic update schema containing modified parameters.
            partial: If True, applies modifications as a partial patch (ignoring unset fields).
                If False, updates the record using all fields. Defaults to False.
            **extra_data: Dynamic fields to merge with the primary payload.

        Returns:
            The updated and refreshed database model instance, or None if the record was not found.
        """
        if isinstance(target, self.model):
            record = target
        else:
            record = await self.get(**{self.pk_name: target})
            if not record:
                return None

        update_data = schema.model_dump(exclude_unset=partial)
        update_data.update(extra_data)
        for field, value in update_data.items():
            setattr(record, field, value)
        await self.db.flush()
        await self.db.refresh(record)
        return record

    async def update_multi(
        self, data: dict[Any, BaseModel], partial: bool = False, refresh: bool = False
    ) -> Sequence[ModelType]:
        """Bulk update multiple database records using DBAPI executemany.

        Executes the updates without hydrating ORM entities beforehand, then fetches
        and returns the updated model instances in a single batch query.

        Args:
            data: A mapping of primary keys to their update schemas.
            partial: If True, ignores unset schema fields. Defaults to False.
            refresh: Parameter maintained for interface consistency. Defaults to False.

        Returns:
            A sequence containing the updated database model instances.
        """
        if not data:
            return []

        payloads = [
            {
                **schema.model_dump(exclude_unset=partial),
                self.pk_name: pk_val,
            }
            for pk_val, schema in data.items()
        ]

        await self.db.execute(
            update(self.model),
            payloads,
        )
        await self.db.flush()

        return await self.get_by_ids(ids=list(data.keys()))

    async def delete(self, id: Any) -> ModelType | None:
        """Delete a single record by its primary key identifier.

        Args:
            id: The primary key value of the target record to delete.

        Returns:
            The deleted database model instance, or None if the record was not found.
        """
        record = await self.get(**{self.pk_name: id})
        if not record:
            return None
        await self.db.delete(record)
        await self.db.flush()
        return record

    async def delete_multi(self, ids: list[Any]) -> Sequence[ModelType]:
        """Delete multiple records matching the provided list of primary keys with dialect-aware fallback.

        Args:
            ids: A list of primary key values of records to delete.

        Returns:
            A sequence containing the deleted database model instances.
        """
        if not ids:
            return []

        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        supports_returning = bool(getattr(dialect, "delete_returning", False))

        if supports_returning:
            stmt = delete(self.model).where(self.pk.in_(ids)).returning(self.model)
            result = await self.db.scalars(stmt)
            await self.db.flush()
            return list(result.all())
        else:
            records = list(await self.get_by_ids(ids=ids))
            if records:
                stmt = delete(self.model).where(self.pk.in_(ids))
                await self.db.execute(stmt)
                await self.db.flush()
            return records


class SearchRepositoryMixin(AbstractRepository[ModelType]):
    """Mixin coordinating structured application search operations."""

    async def search(self, search_in: "SearchRequest", pagination: Any = None) -> Any:
        """Search and filter database models dynamically.

        Args:
            search_in: A SearchRequest parameter configuration representing constraints.
            pagination: Pagination settings. Defaults to None.

        Returns:
            A paginated response object containing matches, or a complete list of
            unpaginated models.
        """
        from zcore.db.search import SearchEngine

        engine = SearchEngine(self.model)
        base_query = self._get_base_query()
        query = engine.build_base_query(search_in, base_query=base_query)
        query = self._apply_filters(query)
        if pagination is None:
            result = await self.db.execute(query)
            return result.scalars().all()

        paginator = (
            CursorPagination(self.cursor_field)
            if isinstance(pagination, CursorParams)
            else PageNumberPagination()
        )
        return await paginator.paginate(self.db, query, pagination, self.model)


class BaseRepository(
    Generic[ModelType],
    ReadRepositoryMixin[ModelType],
    WriteRepositoryMixin[ModelType],
    SearchRepositoryMixin[ModelType],
):
    """The default implementation combining Read, Write, and Search capabilities."""

    def __init__(self, model: type[ModelType], db: AsyncSession):
        """Initialize the BaseRepository.

        Args:
            model: The SQLAlchemy declarative model class linked to this repository.
            db: The active asynchronous SQLAlchemy session.
        """
        self.model = model
        self.db = db
        self.pk = inspect(self.model).primary_key[0]
        self.pk_name = self.pk.name
        self.cursor_field = self.pk_name