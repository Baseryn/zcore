"""Dynamic Database Search and Filtering Engine.

This module provides a dynamic query-builder that parses client-supplied filters,
sorting criteria, and relation-preload requests. Crucially, the system coordinates
with security context restrictions, intercepting and blocking queries that target
unauthorized fields or database relations.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field
from sqlalchemy import and_, asc, desc, inspect, or_, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.sql import Select

from zcore.context.context import ctx
from zcore.db.setup import Base
from zcore.exceptions.base import ForbiddenError, ValidationError

ModelType = TypeVar("ModelType", bound=Base)


class FilterItem(BaseModel):
    """Pydantic model representing a single, structured filtering condition.

    Can model simple comparisons (e.g., field equals value) or nested relational groups
    (e.g., OR conditions over other FilterItems).

    Attributes:
        field: The dotted path representation of the field to filter (e.g., "owner.email").
            Defaults to None.
        op: The logical comparison or grouping operator to evaluate.
        value: The parameter value used during evaluation. Defaults to None.
        items: Nested list of sub-filters to evaluate when the operator is 'or' or 'and'.
            Defaults to None.
    """

    field: str | None = None
    op: Literal[
        "eq", "ne", "gt", "lt", "ge", "le", "ilike", "in", "is_null",
        "contains", "startwith", "endwith", "between", "or", "and"
    ]
    value: Any | None = None
    items: list[FilterItem] | None = None


class SortItem(BaseModel):
    """Pydantic model mapping explicit sorting configurations.

    Attributes:
        field: The dotted target field path on the model.
        order: The direction to apply, either 'asc' (ascending) or 'desc' (descending).
            Defaults to "asc".
    """

    field: str
    order: Literal["asc", "desc"] = "asc"


class SearchRequest(BaseModel):
    """Comprehensive request model containing dynamic search parameters.

    Attributes:
        filters: A recursive collection of filter constraints. Defaults to an empty list.
        include: Relationship attributes or dot-paths indicating database relationships
            to eager load. Defaults to an empty list.
        sort: Explicit ordering instructions. Defaults to an empty list.
        size: The limit on retrieved records. Defaults to 20.
        page: The target page offset index. Defaults to 1.
        cursor: Keyset pagination indicator. Defaults to None.
    """

    filters: list[FilterItem] | None = []
    include: list[str] | None = []
    sort: list[SortItem] | None = []
    size: int = Field(default=20, le=100)
    page: int = 1
    cursor: str | None = None


class SearchEngine:
    """The central query coordinator translating SearchRequests into secure SQLAlchemy queries.

    This engine parses nested criteria structures, safely coerces incoming parameter types,
    supports custom field handlers, and cross-references active context restriction definitions
    to prevent unauthorized data exposure.

    Attributes:
        model: The root database model bound to this search instance.
        mapper: The SQLAlchemy mapper interface for the model.
        custom_handlers: Custom translation callbacks registered for specific fields.
    """

    def __init__(self, model: type[ModelType]):
        """Initialize the SearchEngine.

        Args:
            model: The core SQLAlchemy Base class this engine will query.
        """
        self.model = model
        self.mapper = inspect(model)
        self.custom_handlers: dict[str, Callable[[Any], Any]] = {}

    def register_handler(
        self, field_name: str, handler: Callable[[Any], Any]
    ) -> SearchEngine:
        """Bind a custom callback handler to parse a specific field's values dynamically.

        Args:
            field_name: The attribute name to register the handler for.
            handler: A callback accepting raw filter values and returning computed expressions.

        Returns:
            The current active instance of the SearchEngine for chaining.
        """
        self.custom_handlers[field_name] = handler
        return self

    def _is_path_restricted(self, path: str, restricted_set: set[str]) -> bool:
        """Assess if a specific database dot-path is restricted by security policies.

        Args:
            path: The relative dot-path of the field or relationship to verify.
            restricted_set: The system-reported set of restricted fields in the active context.

        Returns:
            True if the target path matches or falls within a restricted prefix, False otherwise.
        """
        if not restricted_set:
            return False

        normalized_path = path.lower()
        for restricted in restricted_set:
            normalized_restricted = restricted.lower()

            if normalized_path == normalized_restricted:
                return True
            if normalized_path.startswith(normalized_restricted + "."):
                return True

        return False

    def _validate_request(self, search_in: SearchRequest, max_depth: int = 3) -> None:
        """Validate search inputs against depth limits and context security policies.

        Args:
            search_in: The SearchRequest input model containing requested parameters.
            max_depth: The maximum allowable nesting depth for search filters. Defaults to 3.

        Raises:
            ForbiddenError: If access to a requested column or relation is restricted.
            ValidationError: If inclusion depth exceeds limits or if a requested path
                does not exist on the target schemas.
        """
        restricted = set(ctx.restricted_fields)
        valid_columns = {col.key for col in self.mapper.columns}

        MAX_INCLUDE_DEPTH = 3
        if search_in.include:
            for path in search_in.include:
                if self._is_path_restricted(path, restricted):
                    raise ForbiddenError(
                        message=f"Access to relation path '{path}' is restricted due to security policies."
                    )

                parts = path.split(".")
                if len(parts) > MAX_INCLUDE_DEPTH + 1:
                    raise ValidationError(
                        message=f"Relation inclusion depth of '{path}' exceeds the maximum limit of {MAX_INCLUDE_DEPTH}."
                    )

                accumulated_path: list[str] = []
                for part in parts:
                    accumulated_path.append(part)
                    current_path = ".".join(accumulated_path)
                    if self._is_path_restricted(current_path, restricted):
                        raise ForbiddenError(
                            message=f"Access to relation path '{current_path}' is restricted."
                        )

                current_model = self.model
                for part in parts:
                    rel = inspect(current_model).relationships.get(part)
                    if not rel:
                        raise ValidationError(
                            message=f"Invalid include relation path: '{path}'"
                        )
                    current_model = rel.mapper.class_

                if len(parts) > MAX_INCLUDE_DEPTH:
                    raise ValidationError(
                        message=f"Relation inclusion depth of '{path}' exceeds the maximum limit of {MAX_INCLUDE_DEPTH}."
                    )

        if search_in.sort:
            for s in search_in.sort:
                if self._is_path_restricted(s.field, restricted):
                    raise ForbiddenError(
                        message=f"Sorting by restricted field '{s.field}' is forbidden."
                    )
                if s.field not in valid_columns:
                    raise ValidationError(
                        message=f"Invalid sort field: '{s.field}' on {self.model.__name__}"
                    )

        if search_in.filters:
            self._validate_filters_recursive(
                search_in.filters,
                valid_columns,
                restricted,
                current_depth=1,
                max_depth=max_depth,
            )

    def _validate_filter_field(self, field_path: str, restricted: set[str]) -> None:
        """Ensure a single filter field path exists and does not violate access policies.

        Args:
            field_path: The dot-path of the field to check.
            restricted: The list of restricted dot-paths in the active security context.

        Raises:
            ForbiddenError: If the filter path is restricted by security policies.
            ValidationError: If the field path is invalid or references an invalid relation.
        """
        parts = field_path.split(".")
        current_model = self.model

        accumulated_path: list[str] = []
        for i, part in enumerate(parts):
            accumulated_path.append(part)
            current_path = ".".join(accumulated_path)

            if self._is_path_restricted(current_path, restricted):
                raise ForbiddenError(
                    message=f"Filtering by restricted path '{current_path}' is forbidden."
                )

            if i == len(parts) - 1:
                valid_columns = {col.key for col in inspect(current_model).columns}
                if part not in valid_columns:
                    raise ValidationError(
                        message=f"Invalid filter field: '{part}' on {current_model.__name__}"
                    )
            else:
                rel = inspect(current_model).relationships.get(part)
                if not rel:
                    raise ValidationError(
                        message=f"Invalid filter relation: '{part}' on {current_model.__name__}"
                    )
                current_model = rel.mapper.class_

    def _validate_filters_recursive(
        self,
        filters: list[FilterItem],
        valid_columns: set[str],
        restricted: set[str],
        current_depth: int,
        max_depth: int,
    ) -> None:
        """Recursively validate a list of search filters.

        Args:
            filters: The collection of filters to evaluate.
            valid_columns: Valid columns mapped directly to the root model.
            restricted: Fields restricted in the active security context.
            current_depth: The current recursion level.
            max_depth: The maximum allowable nesting depth.

        Raises:
            ValidationError: If the query filter structure exceeds nesting thresholds.
        """
        if current_depth > max_depth:
            raise ValidationError(
                message="Search query filter structure is too complex."
            )

        for f in filters:
            if f.op in ["or", "and"]:
                if f.items:
                    self._validate_filters_recursive(
                        f.items, valid_columns, restricted, current_depth + 1, max_depth
                    )
            else:
                if f.field:
                    self._validate_filter_field(f.field, restricted)

    @staticmethod
    def _escape_like_wildcards(val: Any) -> str:
        """Sanitize SQL wildcards (e.g. %, _) from search query strings.

        Args:
            val: The input query string value.

        Returns:
            The sanitized string safe for LIKE searches.
        """
        if not isinstance(val, str):
            return str(val)
        return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _get_operator_expression(self, f: FilterItem) -> Any:
        """Translate a single filter block into a SQLAlchemy comparison expression.

        Args:
            f: The FilterItem configuration to parse.

        Returns:
            An SQL-coerced comparison expression, or None.
        """
        if f.op in ["or", "and"] and f.items:
            sub_exprs = [self._get_operator_expression(item) for item in f.items]
            sub_exprs = [e for e in sub_exprs if e is not None]
            return or_(*sub_exprs) if f.op == "or" else and_(*sub_exprs)

        if f.field in self.custom_handlers:
            return self.custom_handlers[f.field](f.value)

        if not f.field:
            return None

        return self._build_expression_for_field(self.model, f.field, f.op, f.value)

    def _build_expression_for_field(
        self, current_model: Any, field_path: str, op: str, value: Any
    ) -> Any:
        """Recursively resolve comparison expressions for single or dot-path relations.

        Args:
            current_model: The SQLAlchemy model class context for resolution.
            field_path: The remaining dot-path segment.
            op: The filter comparison operator.
            value: The query filter parameter value.

        Returns:
            The resolved SQL comparison clause, or None.
        """
        parts = field_path.split(".")
        current_mapper = inspect(current_model)

        if len(parts) == 1:
            col = getattr(current_model, parts[0], None)
            if col is None:
                return None
            return self._compare_column(col, op, value)

        rel_name = parts[0]
        rel = current_mapper.relationships.get(rel_name)
        if not rel:
            return None

        target_model = rel.mapper.class_
        remaining_path = ".".join(parts[1:])

        sub_expr = self._build_expression_for_field(
            target_model, remaining_path, op, value
        )
        if sub_expr is None:
            return None

        rel_attr = getattr(current_model, rel_name)
        if rel.uselist:
            return rel_attr.any(sub_expr)
        else:
            return rel_attr.has(sub_expr)

    def _coerce_value(self, col: Any, value: Any) -> Any:
        """Coerce incoming payload types to match the target database column types.

        Args:
            col: The target database model column.
            value: The raw parameter value to coerce.

        Returns:
            The coerced python value.
        """
        if value is None:
            return value

        if isinstance(value, (list, tuple)):
            return [self._coerce_value(col, v) for v in value]

        try:
            python_type = col.type.python_type

            if python_type is date and isinstance(value, str):
                return date.fromisoformat(value.split("T")[0])

            if python_type is datetime and isinstance(value, str):
                return datetime.fromisoformat(value)

            if python_type is uuid.UUID and isinstance(value, str):
                return uuid.UUID(value)

            if python_type is bool and isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "t", "y")

        except (NotImplementedError, AttributeError, ValueError, TypeError):
            pass

        return value

    def _compare_column(self, col: Any, op: str, value: Any) -> Any:
        """Build database comparison clauses matching standard filter operators.

        Args:
            col: The active comparison column.
            op: The filter comparison operator string.
            value: The coerced parameter value.

        Returns:
            The generated comparison clause, or None if unsupported.
        """
        if op == "is_null":
            return col.is_(None) if value else col.isnot(None)

        if op == "ilike":
            escaped_value = self._escape_like_wildcards(value)
            return col.ilike(f"%{escaped_value}%", escape="\\")

        if op == "startwith":
            escaped_value = self._escape_like_wildcards(value)
            return col.ilike(f"{escaped_value}%", escape="\\")
        
        if op == "endwith":
            escaped_value = self._escape_like_wildcards(value)
            return col.ilike(f"%{escaped_value}", escape="\\")
        
        if op == "contains":
            if hasattr(col.type, "python_type") and col.type.python_type is str:
                escaped_value = self._escape_like_wildcards(value)
                return col.ilike(f"%{escaped_value}%", escape="\\")
            return col.contains(value)

        if op == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValidationError(message=f"Operator 'between' expects a list of 2 elements, got {value}")
            lower = self._coerce_value(col, value[0])
            upper = self._coerce_value(col, value[1])
            return col.between(lower, upper)

        coerced_value = self._coerce_value(col, value)

        if op == "eq":
            return col == coerced_value
        if op == "ne":
            return col != coerced_value
        if op == "gt":
            return col > coerced_value
        if op == "lt":
            return col < coerced_value
        if op == "ge":
            return col >= coerced_value
        if op == "le":
            return col <= coerced_value

        if op == "in":
            return col.in_(
                coerced_value
                if isinstance(coerced_value, (list, tuple))
                else [coerced_value]
            )

        return None

    def _apply_includes(self, query: Select, include_paths: list[str]) -> Select:
        """Configure relation loader strategies on the Select query.

        Args:
            query: The active SQLAlchemy SELECT query.
            include_paths: Dot-paths denoting relationships to eager load.

        Returns:
            The query configured with appropriate relation loader options.
        """
        unique_paths = sorted(list(set(include_paths)), key=len)

        for path in unique_paths:
            parts = path.split(".")
            loader = None
            current_model = self.model

            for i, part in enumerate(parts):
                rel = inspect(current_model).relationships.get(part)
                if not rel:
                    break

                load_method_name = selectinload if rel.uselist else joinedload
                if i == 0:
                    load_func = selectinload if rel.uselist else joinedload
                    loader = load_func(getattr(current_model, part))
                else:
                    loader = getattr(loader, load_method_name.__name__)(
                        getattr(current_model, part)
                    )

                current_model = rel.mapper.class_

            if loader:
                query = query.options(loader)
        return query

    def build_base_query(
        self, search_in: SearchRequest, base_query: Select | None = None
    ) -> Select:
        """Parse search inputs, validate policies, and construct the base Select statement.

        Args:
            search_in: The user-supplied search and filter request model.
            base_query: Optional base Select query statement to extend. Defaults to None.

        Returns:
            A secure, compiled SQLAlchemy SELECT statement complete with filtering,
            eager loading joins, and sorting options.
        """
        self._validate_request(search_in)

        query = base_query if base_query is not None else select(self.model)

        if search_in.include:
            query = self._apply_includes(query, search_in.include)

        if search_in.filters:
            expressions = [self._get_operator_expression(f) for f in search_in.filters]
            expressions = [e for e in expressions if e is not None]
            if expressions:
                query = query.where(and_(*expressions))

        if search_in.sort:
            for s in search_in.sort:
                col = getattr(self.model, s.field, None)
                if col:
                    query = query.order_by(asc(col) if s.order == "asc" else desc(col))

        return query

    def build_query(self, search_in: SearchRequest) -> Select:
        """Construct the base Select query and apply limit and offset boundaries.

        Args:
            search_in: The search and filter request model containing pagination parameters.

        Returns:
            A securely configured, size-bounded SQLAlchemy SELECT statement.
        """
        query = self.build_base_query(search_in)
        offset = (search_in.page - 1) * search_in.size
        return query.offset(offset).limit(search_in.size)
