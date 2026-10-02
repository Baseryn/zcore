"""Database Pagination Infrastructure.

This module provides pagination strategies for relational query outputs. It supports
both Page-Number (Offset-based) pagination and high-performance Cursor (Keyset-based)
pagination with base64 encoded metadata, integrating with dynamic configuration boundaries
and inspecting declarative model primary keys dynamically.
"""

import base64
import contextlib
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.sqltypes import DateTime

from zcore.config import settings
from zcore.exceptions.base import ValidationError
from zcore.utils.helpers import json_dumps, json_loads

T = TypeVar("T")


class PaginatedResult(Generic[T]):
    """Standardized wrapper container housing paginated database entities and metadata.

    Attributes:
        data: The segment sequence of retrieved database entities.
        meta: A dictionary containing structural page metadata.
    """

    def __init__(self, data: Sequence[T], meta: dict[str, Any]):
        """Initialize the PaginatedResult container.

        Args:
            data: The sequence of retrieved database items.
            meta: Metadata parameters associated with the active pagination page.
        """
        self.data = data
        self.meta = meta


class PageNumberParams(BaseModel):
    """Pydantic parameters representing page-number pagination configurations.

    Attributes:
        page: The targeted page index. Defaults to 1.
        size: The maximum quantity of records to yield per page.
        sort_by: The model attribute name to apply sorting on. Defaults to None.
        sort_order: Sorting direction, either ascending ('asc') or descending ('desc').
            Defaults to "asc".
        include_count: If True, performs an additional database execution to count the
            total matching resources. Defaults to True.
    """

    page: int = Field(default=1, ge=1)
    size: int | None = None
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] = "asc"
    include_count: bool = True

    @model_validator(mode="after")
    def _validate_and_bound_size(self) -> "PageNumberParams":
        default_size = getattr(settings, "PAGINATION_DEFAULT_SIZE", 20)
        max_size = getattr(settings, "PAGINATION_MAX_SIZE", 100)

        if self.size is None:
            self.size = default_size
        elif self.size < 1:
            self.size = 1
        elif self.size > max_size:
            self.size = max_size

        return self


class CursorParams(BaseModel):
    """Pydantic parameters representing cursor keyset pagination configurations.

    Attributes:
        cursor: The base64-encoded keyset cursor string specifying position context.
            If None, fetches from the beginning. Defaults to None.
        size: The maximum quantity of records to yield in the dataset.
    """

    cursor: str | None = None
    size: int | None = None

    @model_validator(mode="after")
    def _validate_and_bound_size(self) -> "CursorParams":
        default_size = getattr(settings, "PAGINATION_DEFAULT_SIZE", 20)
        max_size = getattr(settings, "PAGINATION_MAX_SIZE", 100)

        if self.size is None:
            self.size = default_size
        elif self.size < 1:
            self.size = 1
        elif self.size > max_size:
            self.size = max_size

        return self


class BasePagination(Generic[T]):
    """Abstract interface for constructing pagination engines.

    Attributes:
        params_class: The Pydantic model type representing valid pagination inputs.
    """

    params_class: type[BaseModel]

    async def paginate(
        self, session: AsyncSession, query: Select, params: Any, model: Any
    ) -> PaginatedResult[T]:
        """Apply pagination constraints onto an active SQLAlchemy Query.

        Args:
            session: The active asynchronous database session.
            query: The initial SQLAlchemy Select statement.
            params: Validated parameters corresponding to the active `params_class`.
            model: The bound database declarative model.

        Returns:
            A PaginatedResult wrapper enclosing matching records and metadata.

        Raises:
            NotImplementedError: Raised if the subclass does not implement pagination.
        """
        raise NotImplementedError


class PageNumberPagination(BasePagination[T]):
    """Offset-based page-number pagination strategy."""

    params_class = PageNumberParams

    async def paginate(
        self, session: AsyncSession, query: Select, params: PageNumberParams, model: Any
    ) -> PaginatedResult[T]:
        """Paginate results using page numbers and offsets.

        Args:
            session: The active asynchronous database session.
            query: The database query to execute.
            params: Parameters defining page indexes, sorting rules, and limits.
            model: The SQLAlchemy model to parse metadata attributes from.

        Returns:
            An offset-based PaginatedResult container.

        Raises:
            ValidationError: If the requested `sort_by` field does not exist on the target model.
        """
        page = params.page
        size = params.size or getattr(settings, "PAGINATION_DEFAULT_SIZE", 20)
        offset = (page - 1) * size

        if params.sort_by:
            valid_columns = {col.key for col in inspect(model).columns}
            if params.sort_by not in valid_columns:
                raise ValidationError(
                    message=f"Invalid sort field: '{params.sort_by}' on {model.__name__}"
                )

            col = getattr(model, params.sort_by)
            if params.sort_order == "desc":
                query = query.order_by(col.desc())
            else:
                query = query.order_by(col.asc())

        total = None
        total_pages = None
        has_next = False
        has_prev = page > 1

        if params.include_count:
            cleaned_query = query.order_by(None)
            count_query = select(func.count()).select_from(cleaned_query.subquery())

            count_result = await session.execute(count_query)
            total = count_result.scalar_one()
            total_pages = math.ceil(total / size) if size > 0 else 0
            has_next = page < total_pages

            paginated_query = query.offset(offset).limit(size)
            items_result = await session.execute(paginated_query)
            items = list(items_result.scalars().all())
        else:
            paginated_query = query.offset(offset).limit(size + 1)
            items_result = await session.execute(paginated_query)
            items = list(items_result.scalars().all())

            if len(items) > size:
                items = items[:size]
                has_next = True

        meta = {
            "total": total,
            "page": page,
            "size": size,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev,
        }
        return PaginatedResult(data=items, meta=meta)


class CursorPagination(BasePagination[T]):
    """Keyset-based cursor pagination strategy with dynamic primary key inspection."""

    params_class = CursorParams

    def __init__(self, cursor_field: str | None = None, order: str = "desc"):
        """Initialize the CursorPagination engine.

        Args:
            cursor_field: The model field key used to coordinate position boundaries.
                If None, dynamically defaults to the model's inspected primary key.
            order: The sorting order ('asc' or 'desc'). Defaults to "desc".
        """
        self.cursor_field = cursor_field
        self.order = order.lower()

    def _encode_cursor(self, last_item: Any, cursor_field: str, pk_name: str) -> str:
        """Serialize and encode an entity's coordinate parameters to base64.

        Args:
            last_item: The boundary database model record to serialize.
            cursor_field: The resolved coordinate field name.
            pk_name: The inspected primary key column identifier of the entity.

        Returns:
            A base64 encoded URL-safe string representation of the cursor coordinate payload.
        """
        value = getattr(last_item, cursor_field, None)
        if isinstance(value, datetime):
            value = (
                value.replace(tzinfo=UTC)
                if value.tzinfo is None
                else value.astimezone(UTC)
            )
            value = value.isoformat()

        payload = {"value": value, "pk": str(getattr(last_item, pk_name, ""))}

        json_str = json_dumps(payload)
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode()
        return encoded.rstrip("=")

    def _decode_cursor(self, cursor_str: str) -> dict[str, Any] | None:
        """Decode and parse an encoded base64 cursor token.

        Args:
            cursor_str: The base64 URL-safe cursor string value.

        Returns:
            A dictionary containing parsed "value" and "pk" fields, or None if empty.

        Raises:
            ValidationError: If the cursor payload is malformed or invalid.
        """
        if not cursor_str:
            return None
        try:
            padding_needed = (4 - len(cursor_str) % 4) % 4
            cursor_str += "=" * padding_needed
            decoded = base64.urlsafe_b64decode(cursor_str.encode()).decode()
            decoded_dict = json_loads(decoded)
            if not isinstance(decoded_dict, dict) or "value" not in decoded_dict:
                raise ValidationError(message="Malformed cursor parameter provided.")

            if "pk" not in decoded_dict and "id" in decoded_dict:
                decoded_dict["pk"] = decoded_dict["id"]

            if "pk" not in decoded_dict:
                raise ValidationError(message="Malformed cursor parameter provided.")

            return decoded_dict
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                message="Malformed cursor parameter provided."
            ) from e

    async def paginate(
        self, session: AsyncSession, query: Select, params: CursorParams, model: Any
    ) -> PaginatedResult[T]:
        """Paginate results using encoded keyset cursors.

        Args:
            session: The active asynchronous database session.
            query: The database select statement.
            params: Parameters specifying active cursor keys and page size limits.
            model: The target database declarative model class.

        Returns:
            A keyset-based PaginatedResult container.

        Raises:
            ValidationError: If the specified cursor_field is invalid or not located
                on the target database model.
        """
        size = params.size or getattr(settings, "PAGINATION_DEFAULT_SIZE", 20)
        cursor_data = self._decode_cursor(params.cursor) if params.cursor else None

        mapper = inspect(model)
        pk_col = mapper.primary_key[0]
        pk_name = pk_col.name
        effective_cursor_field = self.cursor_field or pk_name

        valid_columns = {col.key for col in mapper.columns}
        if effective_cursor_field not in valid_columns:
            raise ValidationError(
                message=f"Invalid cursor field: '{effective_cursor_field}' on {model.__name__}"
            )

        col = getattr(model, effective_cursor_field)

        if cursor_data:
            val = cursor_data["value"]
            last_pk = cursor_data["pk"]

            try:
                pk_type = getattr(pk_col.type, "python_type", None)
            except (NotImplementedError, AttributeError):
                pk_type = None

            if pk_type is int and isinstance(last_pk, str):
                with contextlib.suppress(ValueError):
                    last_pk = int(last_pk)
            elif pk_type is uuid.UUID and isinstance(last_pk, str):
                with contextlib.suppress(ValueError):
                    last_pk = uuid.UUID(last_pk)

            if isinstance(col.type, DateTime) and isinstance(val, str):
                with contextlib.suppress(ValueError):
                    val = datetime.fromisoformat(val)

            if effective_cursor_field == pk_name:
                query = query.where(col < val if self.order == "desc" else col > val)
            else:
                if self.order == "desc":
                    query = query.where(
                        or_(col < val, and_(col == val, pk_col < last_pk))
                    )
                else:
                    query = query.where(
                        or_(col > val, and_(col == val, pk_col > last_pk))
                    )

        if effective_cursor_field == pk_name:
            query = query.order_by(col.desc() if self.order == "desc" else col.asc())
        else:
            if self.order == "desc":
                query = query.order_by(col.desc(), pk_col.desc())
            else:
                query = query.order_by(col.asc(), pk_col.asc())

        paginated_query = query.limit(size + 1)
        items_result = await session.execute(paginated_query)
        items = list(items_result.scalars().all())

        has_more = len(items) > size
        next_cursor = None

        if has_more:
            items = items[:size]
            next_cursor = self._encode_cursor(items[-1], effective_cursor_field, pk_name)

        meta = {"next_cursor": next_cursor, "has_more": has_more, "size": size}
        return PaginatedResult(data=items, meta=meta)