"""ZCore Database Repository Pattern Layer.

This module provides a Repository pattern implementation for SQLAlchemy
2.0 and Pydantic V2. It decouples business logic from database interactions by exposing
highly specialized, reusable interfaces divided into read, write, and search capabilities.
It supports dynamic pagination, eager load optimization, field pruning, composite primary keys,
and full soft-delete lifecycles.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, and_, delete, func, insert, inspect, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.interfaces import ExecutableOption

from zcore.db.pagination import CursorPagination, CursorParams, PageNumberPagination
from zcore.db.setup import Base
from zcore.utils.timezone import now

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
        pks: Tuple of primary key column representations of the bound model.
        pk_names: Tuple of primary key column names.
        is_composite_pk: Flag indicating if the model has a composite primary key.
        cursor_field: The field name to utilize during keyset pagination lookups.
    """

    db: AsyncSession
    model: type[ModelType]
    pks: tuple[Any, ...]
    pk_names: tuple[str, ...]
    is_composite_pk: bool
    cursor_field: str

    @property
    def pk(self) -> Any:
        """Convenience property for single primary key models.

        Raises:
            AttributeError: If accessed on a composite primary key model.
        """
        if self.is_composite_pk:
            raise AttributeError(
                f"Model '{self.model.__name__}' has a composite primary key {self.pk_names}. "
                "Access 'self.pks' instead of 'self.pk'."
            )
        return self.pks[0]

    @property
    def pk_name(self) -> str:
        """Convenience property for single primary key column name.

        Raises:
            AttributeError: If accessed on a composite primary key model.
        """
        if self.is_composite_pk:
            raise AttributeError(
                f"Model '{self.model.__name__}' has a composite primary key {self.pk_names}. "
                "Access 'self.pk_names' instead of 'self.pk_name'."
            )
        return self.pk_names[0]

    def _get_base_query(self) -> Select:
        """Construct the initial select statement for query operations.

        Enforces context security and soft-delete scoping by dynamically executing 'scope_query'
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

    def _build_pk_condition(self, target: Any) -> Any:
        """Build equality filter condition for single or composite primary key target.

        Args:
            target: Model instance, dict mapping column names to values, tuple/list of values, or scalar.

        Returns:
            SQLAlchemy binary expression or compound `and_` expression matching target primary key.
        """
        if isinstance(target, self.model):
            return and_(*[col == getattr(target, col.key) for col in self.pks])

        if isinstance(target, dict):
            return and_(*[col == target[col.key] for col in self.pks if col.key in target])

        if isinstance(target, (tuple, list)):
            if self.is_composite_pk:
                return and_(*[col == val for col, val in zip(self.pks, target, strict=False)])
            return self.pks[0] == target[0]

        return self.pks[0] == target

    def _build_ids_condition(self, ids: Sequence[Any]) -> Any:
        """Construct matching expression for sequences of scalar or composite primary keys.

        Args:
            ids: Sequence of scalars, tuples, dicts, or model instances.

        Returns:
            SQLAlchemy filter expression resolving scalar `in_` or composite `tuple_.in_`.
        """
        if not self.is_composite_pk:
            normalized_scalars = [
                getattr(item, self.pk_names[0])
                if isinstance(item, self.model)
                else item.get(self.pk_names[0])
                if isinstance(item, dict)
                else item[0]
                if isinstance(item, (tuple, list))
                else item
                for item in ids
            ]
            return self.pks[0].in_(normalized_scalars)

        normalized_tuples = []
        for item in ids:
            if isinstance(item, self.model):
                normalized_tuples.append(tuple(getattr(item, col.key) for col in self.pks))
            elif isinstance(item, dict):
                normalized_tuples.append(tuple(item[col.key] for col in self.pks))
            elif isinstance(item, (tuple, list)):
                normalized_tuples.append(tuple(item))
            else:
                normalized_tuples.append((item,))

        return tuple_(*self.pks).in_(normalized_tuples)

    def _supports_soft_delete(self) -> bool:
        """Determine whether the underlying model supports soft deletion.

        Returns:
            True if the model implements soft-delete capabilities, False otherwise.
        """
        return hasattr(self.model, "deleted_at") and hasattr(self.model, "soft_delete")


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
        query = select(*self.pks)
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
            query = query.options(*options) if isinstance(options, list) else query.options(options)
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
            ids: A list of scalar primary keys or composite key tuples/mappings.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy executable options. Defaults to None.

        Returns:
            A sequence of retrieved database model instances.
        """
        if not ids:
            return []

        query = self._get_base_query().where(self._build_ids_condition(ids))
        if fields:
            query = query.options(load_only(*fields))
        if options:
            query = query.options(*options) if isinstance(options, list) else query.options(options)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_list(
        self,
        *criterion: Any,
        pagination: Any = None,
        fields: list[Any] | None = None,
        options: list[ExecutableOption] | None = None,
        **filters: Any,
    ) -> Any:
        """Fetch a paginated or complete list of records matching filters.

        Args:
            *criterion: Positional binary SQLAlchemy filter expressions.
            pagination: Pagination parameters. Defaults to None.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy execution options. Defaults to None.
            **filters: Keyword key-value arguments for standard equality filters.

        Returns:
            A list of matching records, or a paginated response container.
        """
        query = self._get_base_query()
        query = self._apply_filters(query, *criterion, **filters)
        if fields:
            query = query.options(load_only(*fields))
        if options:
            query = query.options(*options) if isinstance(options, list) else query.options(options)

        if pagination is None:
            result = await self.db.execute(query)
            return result.scalars().all()

        paginator = (
            CursorPagination(
                cursor_field=getattr(pagination, "cursor_field", None) or self.cursor_field,
                order=getattr(pagination, "order", None) or "desc",
            )
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
    """Mixin implementing data modification, persistence, and deletion lifecycles."""

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
        self,
        target: ModelType | Any,
        schema: BaseModel,
        partial: bool = False,
        **extra_data: Any,
    ) -> ModelType | None:
        """Update an existing database record from a model instance, primary key, or composite mapping.

        Args:
            target: Model instance, primary key identifier, composite tuple, or composite mapping.
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
            condition = self._build_pk_condition(target)
            record = await self.get(condition)
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
        self,
        data: dict[ModelType | Any, BaseModel],
        partial: bool = False,
        refresh: bool = False,
    ) -> Sequence[ModelType]:
        """Bulk update multiple database records supporting single or composite keys.

        Args:
            data: A mapping of model instances, scalar keys, tuples, or dicts to their update schemas.
            partial: If True, ignores unset schema fields. Defaults to False.
            refresh: Parameter maintained for interface consistency. Defaults to False.

        Returns:
            A sequence containing the updated database model instances.
        """
        if not data:
            return []

        payloads = []
        target_ids = []
        for key, schema in data.items():
            row_payload = schema.model_dump(exclude_unset=partial)
            if isinstance(key, self.model):
                target_id = (
                    tuple(getattr(key, col.key) for col in self.pks)
                    if self.is_composite_pk
                    else getattr(key, self.pk_names[0])
                )
                for col in self.pks:
                    row_payload[col.key] = getattr(key, col.key)
            elif isinstance(key, dict):
                target_id = (
                    tuple(key[col.key] for col in self.pks)
                    if self.is_composite_pk
                    else key.get(self.pk_names[0])
                )
                for col in self.pks:
                    if col.key in key:
                        row_payload[col.key] = key[col.key]
            elif isinstance(key, (tuple, list)) and self.is_composite_pk:
                target_id = tuple(key)
                for col, val in zip(self.pks, key, strict=False):
                    row_payload[col.key] = val
            else:
                target_id = key
                row_payload[self.pk_names[0]] = key

            target_ids.append(target_id)
            payloads.append(row_payload)

        await self.db.execute(
            update(self.model),
            payloads,
        )
        await self.db.flush()

        return await self.get_by_ids(ids=target_ids)

    async def delete(self, target: ModelType | Any, force: bool = False) -> ModelType | None:
        """Delete a single record supporting single and composite primary keys.

        Args:
            target: Model instance, primary key identifier, composite tuple, or composite mapping.
            force: If True, executes a permanent physical hard deletion. Defaults to False.

        Returns:
            The deleted or soft-deleted database model instance, or None if not found.
        """
        if isinstance(target, self.model):
            record = target
        else:
            condition = self._build_pk_condition(target)
            if force:
                query = select(self.model).where(condition)
                result = await self.db.execute(query)
                record = result.scalars().first()
            else:
                record = await self.get(condition)

            if not record:
                return None

        if not force and self._supports_soft_delete():
            record.soft_delete()
            await self.db.flush()
            await self.db.refresh(record)
            return record

        await self.db.delete(record)
        await self.db.flush()
        return record

    async def delete_multi(self, ids: list[Any], force: bool = False) -> Sequence[ModelType]:
        """Delete multiple records by primary keys supporting single and composite entities.

        Args:
            ids: List of primary keys, composite tuples, mappings, or model instances.
            force: If True, executes permanent physical hard deletions. Defaults to False.

        Returns:
            A sequence containing the deleted or soft-deleted database model instances.
        """
        if not ids:
            return []

        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        supports_returning = bool(getattr(dialect, "delete_returning", False))
        ids_condition = self._build_ids_condition(ids)

        if not force and self._supports_soft_delete():
            supports_update_returning = bool(getattr(dialect, "update_returning", False))
            deletion_time = now()
            stmt = (
                update(self.model)
                .where(ids_condition, self.model.deleted_at.is_(None))
                .values(deleted_at=deletion_time)
            )

            if supports_update_returning:
                stmt = stmt.returning(self.model)
                result = await self.db.execute(stmt)
                await self.db.flush()
                return list(result.scalars().all())

            await self.db.execute(stmt)
            await self.db.flush()
            query = select(self.model).where(ids_condition)
            res = await self.db.execute(query)
            return list(res.scalars().all())

        if supports_returning:
            stmt = delete(self.model).where(ids_condition).returning(self.model)
            result = await self.db.scalars(stmt)
            await self.db.flush()
            return list(result.all())
        else:
            query = select(self.model).where(ids_condition)
            records_res = await self.db.execute(query)
            records = list(records_res.scalars().all())
            if records:
                stmt = delete(self.model).where(ids_condition)
                await self.db.execute(stmt)
                await self.db.flush()
            return records

    async def restore(self, target: ModelType | Any) -> ModelType | None:
        """Restore a previously soft-deleted record supporting single and composite entities.

        Args:
            target: Model instance, primary key identifier, composite tuple, or composite mapping.

        Returns:
            The restored database model instance, or None if the record was not found or unsupported.
        """
        if not self._supports_soft_delete():
            return None

        if isinstance(target, self.model):
            record = target
        else:
            condition = self._build_pk_condition(target)
            query = select(self.model).where(condition)
            result = await self.db.execute(query)
            record = result.scalars().first()
            if not record:
                return None

        record.restore()
        await self.db.flush()
        await self.db.refresh(record)
        return record

    async def restore_multi(self, ids: list[Any]) -> Sequence[ModelType]:
        """Restore multiple soft-deleted records supporting single and composite entities.

        Args:
            ids: List of primary keys, composite tuples, mappings, or model instances.

        Returns:
            A sequence of restored database model instances.
        """
        if not ids or not self._supports_soft_delete():
            return []

        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        supports_update_returning = bool(getattr(dialect, "update_returning", False))
        ids_condition = self._build_ids_condition(ids)

        stmt = (
            update(self.model)
            .where(ids_condition, self.model.deleted_at.is_not(None))
            .values(deleted_at=None)
        )

        if supports_update_returning:
            stmt = stmt.returning(self.model)
            result = await self.db.execute(stmt)
            await self.db.flush()
            return list(result.scalars().all())

        await self.db.execute(stmt)
        await self.db.flush()
        query = select(self.model).where(ids_condition)
        res = await self.db.execute(query)
        return list(res.scalars().all())


class SearchRepositoryMixin(AbstractRepository[ModelType]):
    """Mixin coordinating structured application search operations."""

    async def search(
        self,
        search_in: "SearchRequest",
        pagination: Any = None,
        fields: list[Any] | None = None,
        options: list[ExecutableOption] | None = None,
    ) -> Any:
        """Search and filter database models dynamically with load optimization.

        Args:
            search_in: A SearchRequest parameter configuration representing constraints.
            pagination: Pagination settings. Defaults to None.
            fields: Specific entity fields to selectively load. Defaults to None.
            options: Additional SQLAlchemy executable options. Defaults to None.

        Returns:
            A paginated response object containing matches, or a complete list of
            unpaginated models.
        """
        from zcore.db.search import SearchEngine

        engine = SearchEngine(self.model)
        base_query = self._get_base_query()

        if fields:
            base_query = base_query.options(load_only(*fields))
        if options:
            base_query = (
                base_query.options(*options)
                if isinstance(options, list)
                else base_query.options(options)
            )

        query = engine.build_base_query(search_in, base_query=base_query)
        query = self._apply_filters(query)
        if pagination is None:
            result = await self.db.execute(query)
            return result.scalars().all()

        if isinstance(pagination, CursorParams):
            primary_sort = search_in.sort[0] if search_in.sort else None
            sort_field = pagination.cursor_field or (
                primary_sort.field if primary_sort else self.cursor_field
            )
            sort_order = pagination.order or (primary_sort.order if primary_sort else "desc")
            paginator = CursorPagination(cursor_field=sort_field, order=sort_order)
        else:
            paginator = PageNumberPagination()

        return await paginator.paginate(self.db, query, pagination, self.model)


class BaseRepository(
    Generic[ModelType],
    ReadRepositoryMixin[ModelType],
    WriteRepositoryMixin[ModelType],
    SearchRepositoryMixin[ModelType],
):
    """The default implementation combining Read, Write, and Search capabilities."""

    def __init__(self, model: type[ModelType], db: AsyncSession):
        """Initialize the BaseRepository supporting single and composite primary keys.

        Args:
            model: The SQLAlchemy declarative model class linked to this repository.
            db: The active asynchronous SQLAlchemy session.
        """
        self.model = model
        self.db = db
        mapper = inspect(self.model)
        self.pks = tuple(mapper.primary_key)
        self.pk_names = tuple(col.name for col in self.pks)
        self.is_composite_pk = len(self.pks) > 1
        self.cursor_field = self.pk_names[0]
