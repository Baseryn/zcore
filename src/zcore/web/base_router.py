"""Automated Web Router Scaffolding.

This module provides the generic `BaseRouter` interface, which scaffolds
standard security-aware CRUD and lookup endpoints (POST, GET, GET_ALL, SEARCH, LOOKUP, UPDATE, PATCH, DELETE)
and integrates them with services, schemas, dependency requirements, and pagination handlers,
with clean declarative syntax, primary key auto-detection, and weighted route specificity sorting.
"""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from fastapi import APIRouter, Depends, status
from fastapi.params import Depends as DependsClass
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.orm import joinedload, selectinload

from zcore.config import settings
from zcore.db.search import SearchRequest
from zcore.exceptions.base import ValidationError
from zcore.kernel.di import Injector
from zcore.security.permissions import HasScopes
from zcore.service.base import BaseService
from zcore.web.api_router import ZCoreAPIRoute
from zcore.web.response import ResponseWrapper

if TYPE_CHECKING:
    from zcore.db.pagination import BasePagination

CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class RouteKey(StrEnum):
    """Enumeration of standard HTTP endpoints managed by the scaffolded router."""

    POST = "POST"
    GET = "GET"
    GET_ALL = "GET_ALL"
    SEARCH = "SEARCH"
    LOOKUP = "LOOKUP"
    UPDATE = "UPDATE"
    PATCH = "PATCH"
    DELETE = "DELETE"


class BaseRouter(Generic[CreateSchemaType, UpdateSchemaType]):
    """Declarative web router orchestrator.

    Automatically maps CRUD and lookup operations to matching database model dependencies and handles
    dependency injections, schema validations, dynamic primary key route resolution,
    and hierarchical route ordering based on path specificity.

    Attributes:
        model: The database declarative model class.
        create_schema: Schema class for validating entity creations.
        update_schema: Schema class for validating entity updates.
        schema_out: Schema class representing responses.
        lookup_schema: Minimal schema class representing relational reference lookups.
        allowed_lookup_fields: Explicit whitelist of filterable/sortable fields for lookup queries.
        max_lookup_size: Maximum records threshold allowed in lookup responses.
        max_page_size: Maximum records threshold allowed across general list queries.
        default_page_size: Fallback record count when not specified in query requests.
        service: Business service callable class.
        pk_type: Optional explicit primary key type. If None, auto-resolved from model metadata.
        prefix: Path prefix representing the route.
        tags: Endpoint group classification tags.
        exclude: Explicit endpoints to bypass during route scaffolding.
        pagination_class: Pagination engine class to construct list queries.
        route_class: Custom routing processing class. Defaults to ZCoreAPIRoute.
        expose_schemas: Exposes target endpoint schemas dynamically.
    """

    model: type[Any]
    create_schema: type[CreateSchemaType] | type[BaseModel] | None = None
    update_schema: type[UpdateSchemaType] | type[BaseModel] | None = None
    schema_out: type[BaseModel] | None = None
    lookup_schema: type[BaseModel] | None = None
    allowed_lookup_fields: set[str] | None = None
    max_lookup_size: int | None = None
    max_page_size: int | None = None
    default_page_size: int | None = None
    service: Any = None
    pk_type: type[Any] | None = None

    prefix: str = ""
    tags: list[str] | None = None
    exclude: set[RouteKey] | None = None
    pagination_class: type["BasePagination"] | None = None
    route_class: type[APIRoute] = ZCoreAPIRoute

    expose_schemas: set[RouteKey] | bool = False

    def __init__(self) -> None:
        """Initialize the BaseRouter.

        Performs fail-fast configuration checks and registers configured CRUD endpoints.

        Raises:
            ValueError: If the required service parameter is not configured.
        """
        if not self.service:
            raise ValueError(f"Service class must be defined in '{self.__class__.__name__}'.")

        self.router = APIRouter(
            prefix=self.prefix, tags=self.tags or [], route_class=self.route_class
        )
        self.exclude = self.exclude or set()
        self._validate_schema_configurations()
        self._register_routes()

    def _resolve_pk_type(self) -> type[Any]:
        """Resolve the effective primary key type from explicit definition or model reflection.

        Returns:
            The resolved python type class for the primary key.
        """
        if getattr(self, "pk_type", None) is not None:
            return self.pk_type

        if getattr(self, "model", None) is not None:
            try:
                mapper = inspect(self.model)
                if mapper.primary_key:
                    pk_col = mapper.primary_key[0]
                    python_type = getattr(pk_col.type, "python_type", None)
                    if python_type is not None:
                        return python_type
            except Exception:
                pass

        return uuid.UUID

    def _get_pk_path(self, pk_type: type[Any]) -> str:
        """Construct the URL path segment matching the primary key converter.

        Args:
            pk_type: The resolved primary key type.

        Returns:
            The formatted route path string.
        """
        if pk_type is int:
            return "/{id:int}"
        if pk_type is uuid.UUID:
            return "/{id:uuid}"
        return "/{id}"

    def _validate_schema_configurations(self) -> None:
        """Perform validation checks on configured route schema definitions.

        Raises:
            ValueError: If an active endpoint lacks required schema configurations.
        """
        if RouteKey.POST not in self.exclude and self.create_schema is None:
            raise ValueError(
                f"POST route is enabled in '{self.__class__.__name__}', but 'create_schema' is None."
            )
        if (
            RouteKey.UPDATE not in self.exclude or RouteKey.PATCH not in self.exclude
        ) and self.update_schema is None:
            raise ValueError(
                f"UPDATE/PATCH route is enabled in '{self.__class__.__name__}', but 'update_schema' is None."
            )

        if not getattr(self, "model", None):
            active_standard_routes = set(RouteKey) - (self.exclude or set())
            if active_standard_routes:
                raise ValueError(
                    f"Model class must be defined in '{self.__class__.__name__}' to resolve route actions."
                )

    def _get_openapi_extra(self, route_key: RouteKey) -> dict[str, Any] | None:
        """Construct OpenAPI specifications for dynamic schema endpoints.

        Args:
            route_key: Target operational key to check.

        Returns:
            An openapi metadata dictionary, or None.
        """
        if isinstance(self.expose_schemas, bool):
            expose = self.expose_schemas
        else:
            expose = route_key in (self.expose_schemas or set())

        if expose:
            return {"expose_schema": True}
        return None

    def _normalize_dependencies(self, raw_deps: list[Any] | Any) -> list[DependsClass]:
        """Normalize raw classes or parameters into FastAPI Depends structures.

        Args:
            raw_deps: Single dependencies or lists of security dependencies.

        Returns:
            A list containing standardized Depends wrappers.
        """
        if raw_deps is None:
            return []

        deps_list = raw_deps if isinstance(raw_deps, list) else [raw_deps]
        normalized: list[DependsClass] = []

        for dep in deps_list:
            if isinstance(dep, DependsClass):
                normalized.append(dep)
            else:
                normalized.append(Depends(dep))

        return normalized

    def get_route_action(self, route_key: RouteKey) -> str:
        """Retrieve the database/permission action name for a given route key.

        Args:
            route_key: The target operational key.

        Returns:
            The calculated permission action identifier string.
        """
        if not self.model:
            raise ValueError(
                f"Model class must be defined in {self.__class__.__name__} to resolve route actions."
            )

        action_map = {
            RouteKey.POST: self.model.actions().CREATE,
            RouteKey.GET: self.model.actions().VIEW,
            RouteKey.GET_ALL: self.model.actions().LISTVIEW,
            RouteKey.SEARCH: self.model.actions().LISTVIEW,
            RouteKey.LOOKUP: self.model.actions().LOOKUP,
            RouteKey.UPDATE: self.model.actions().UPDATE,
            RouteKey.PATCH: self.model.actions().UPDATE,
            RouteKey.DELETE: self.model.actions().DELETE,
        }
        return action_map[route_key]

    def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
        """Generate default route dependencies.

        Args:
            route_key: The target operational route key.
            action: The computed database/permission action identifier.

        Returns:
            A list of dependencies.
        """
        if not action:
            return []
        return [HasScopes(action)]

    def _get_route_dependencies(self, route_key: RouteKey) -> list[DependsClass]:
        """Internal helper to resolve and normalize route dependencies.

        Args:
            route_key: The target operational route key.

        Returns:
            A normalized list of FastAPI dependency parameters.
        """
        try:
            action = self.get_route_action(route_key)
        except (ValueError, AttributeError):
            action = ""

        dependencies = self.get_route_dependencies(route_key, action)
        return self._normalize_dependencies(dependencies)

    def _get_effective_lookup_fields(self) -> set[str]:
        """Resolve the effective whitelist of filterable and sortable fields for lookup queries.

        Returns:
            A set of allowed field names extracted from explicit configurations or the lookup schema.
        """
        if self.allowed_lookup_fields is not None:
            return set(self.allowed_lookup_fields)

        if self.lookup_schema is not None and hasattr(self.lookup_schema, "model_fields"):
            return set(self.lookup_schema.model_fields.keys())

        return set()

    def _resolve_lookup_projections(self) -> tuple[list[Any] | None, list[Any] | None]:
        """Resolve database columns and relationship loader strategies required by the lookup schema.

        Returns:
            A tuple containing the list of column attributes to load and executable loader options.
        """
        if not self.lookup_schema or not self.model:
            return None, None

        schema_fields = (
            set(self.lookup_schema.model_fields.keys())
            if hasattr(self.lookup_schema, "model_fields")
            else set()
        )
        if not schema_fields:
            return None, None

        try:
            mapper = inspect(self.model)
        except Exception:
            return None, None

        column_keys = {col.key for col in mapper.columns}
        relationship_map = {rel.key: rel for rel in mapper.relationships}

        load_columns: list[Any] = []
        for pk_col in mapper.primary_key:
            pk_attr = getattr(self.model, pk_col.key, None)
            if pk_attr is not None:
                load_columns.append(pk_attr)

        loader_options: list[Any] = []

        for field_name in schema_fields:
            if field_name in column_keys:
                col_attr = getattr(self.model, field_name, None)
                if col_attr is not None and col_attr not in load_columns:
                    load_columns.append(col_attr)
            elif field_name in relationship_map:
                rel = relationship_map[field_name]
                rel_attr = getattr(self.model, field_name, None)
                if rel_attr is not None:
                    loader_options.append(
                        selectinload(rel_attr) if rel.uselist else joinedload(rel_attr)
                    )

        return (
            load_columns if load_columns else None,
            loader_options if loader_options else None,
        )

    def _validate_lookup_request(self, search_in: SearchRequest, allowed_fields: set[str]) -> None:
        """Validate that incoming lookup filters and sorting parameters conform to allowed fields.

        Args:
            search_in: Client-supplied SearchRequest.
            allowed_fields: Whitelist of allowed field names.

        Raises:
            ValidationError: If an unapproved field is targeted by filters or sorting rules.
        """
        if not allowed_fields:
            return

        def _check_filters(items: list[Any] | None) -> None:
            for f in items or []:
                if f.field and f.field.split(".")[0] not in allowed_fields:
                    raise ValidationError(
                        message=f"Field '{f.field}' is not permitted in lookup queries."
                    )
                if f.items:
                    _check_filters(f.items)

        _check_filters(search_in.filters)

        if search_in.sort:
            for s in search_in.sort:
                if s.field.split(".")[0] not in allowed_fields:
                    raise ValidationError(
                        message=f"Sort field '{s.field}' is not permitted in lookup queries."
                    )

    def _sort_routes(self) -> None:
        """Sort routes using hierarchical specificity scoring to avoid path shadowing."""

        def _route_specificity_key(route: Any) -> tuple[int, int, list[int], int]:
            path = getattr(route, "path", "")
            segments = [seg for seg in path.strip("/").split("/") if seg]

            segment_scores: list[int] = []
            for seg in segments:
                if seg.startswith("{") and seg.endswith("}"):
                    if ":path}" in seg:
                        segment_scores.append(2)
                    else:
                        segment_scores.append(1)
                else:
                    segment_scores.append(0)

            has_dynamic = 1 if any(s > 0 for s in segment_scores) else 0
            total_dynamic = sum(segment_scores)
            return (has_dynamic, total_dynamic, segment_scores, -len(segments))

        self.router.routes.sort(key=_route_specificity_key)

    def _register_routes(self) -> None:
        """Dynamically generate and bind endpoints to the APIRouter."""
        service_callable = self.service
        service_dependency = Depends(Injector(service_callable))
        target_pk_type = self._resolve_pk_type()
        pk_path = self._get_pk_path(target_pk_type)

        if RouteKey.POST not in self.exclude:
            c_schema = self.create_schema

            async def _create_endpoint(
                data_in: c_schema,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.create_endpoint(data_in, service_inst)

            self.router.add_api_route(
                path="/",
                endpoint=_create_endpoint,
                methods=["POST"],
                dependencies=self._get_route_dependencies(RouteKey.POST),
                status_code=status.HTTP_201_CREATED,
                response_model=ResponseWrapper[self.schema_out]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.POST),
            )

        if RouteKey.GET not in self.exclude:

            async def _get_endpoint(
                id: target_pk_type,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.get_endpoint(id, service_inst)

            self.router.add_api_route(
                path=pk_path,
                endpoint=_get_endpoint,
                methods=["GET"],
                dependencies=self._get_route_dependencies(RouteKey.GET),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.GET),
            )

        if RouteKey.GET_ALL not in self.exclude:
            if self.pagination_class:
                params_class = self.pagination_class.params_class

                async def _get_all_endpoint(
                    params: params_class = Depends(),
                    service_inst: BaseService = service_dependency,
                ) -> ResponseWrapper:
                    return await self.get_all_endpoint(service_inst, params)
            else:

                async def _get_all_endpoint(
                    service_inst: BaseService = service_dependency,
                ) -> ResponseWrapper:
                    return await self.get_all_endpoint(service_inst)

            self.router.add_api_route(
                path="/",
                endpoint=_get_all_endpoint,
                methods=["GET"],
                dependencies=self._get_route_dependencies(RouteKey.GET_ALL),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[list[self.schema_out]]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.GET_ALL),
            )

        if RouteKey.SEARCH not in self.exclude:

            async def _search_endpoint(
                search_in: SearchRequest,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.search_endpoint(search_in, service_inst)

            self.router.add_api_route(
                path="/search",
                endpoint=_search_endpoint,
                methods=["POST"],
                dependencies=self._get_route_dependencies(RouteKey.SEARCH),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[list[self.schema_out]]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.SEARCH),
            )

        if RouteKey.LOOKUP not in self.exclude and self.lookup_schema is not None:

            async def _lookup_endpoint(
                search_in: SearchRequest,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.lookup_endpoint(search_in, service_inst)

            self.router.add_api_route(
                path="/lookup",
                endpoint=_lookup_endpoint,
                methods=["POST"],
                dependencies=self._get_route_dependencies(RouteKey.LOOKUP),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[list[self.lookup_schema]],
                openapi_extra=self._get_openapi_extra(RouteKey.LOOKUP),
            )

        if RouteKey.UPDATE not in self.exclude:
            u_schema = self.update_schema

            async def _update_endpoint(
                id: target_pk_type,
                data_in: u_schema,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.update_endpoint(id, data_in, service_inst)

            self.router.add_api_route(
                path=pk_path,
                endpoint=_update_endpoint,
                methods=["PUT"],
                dependencies=self._get_route_dependencies(RouteKey.UPDATE),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.UPDATE),
            )

        if RouteKey.PATCH not in self.exclude:
            u_schema = self.update_schema

            async def _patch_endpoint(
                id: target_pk_type,
                data_in: u_schema,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.patch_endpoint(id, data_in, service_inst)

            self.router.add_api_route(
                path=pk_path,
                endpoint=_patch_endpoint,
                methods=["PATCH"],
                dependencies=self._get_route_dependencies(RouteKey.PATCH),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out]
                if self.schema_out
                else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.PATCH),
            )

        if RouteKey.DELETE not in self.exclude:

            async def _delete_endpoint(
                id: target_pk_type,
                force: bool = False,
                service_inst: BaseService = service_dependency,
            ) -> ResponseWrapper:
                return await self.delete_endpoint(id, service_inst, force=force)

            self.router.add_api_route(
                path=pk_path,
                endpoint=_delete_endpoint,
                methods=["DELETE"],
                dependencies=self._get_route_dependencies(RouteKey.DELETE),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.DELETE),
            )

        self._sort_routes()

    async def create_endpoint(self, data_in: Any, service: BaseService) -> ResponseWrapper:
        """Execute the POST creation transaction.

        Args:
            data_in: Validated input schema containing creation properties.
            service: Active business service instance.

        Returns:
            The created entity wrapped in a ResponseWrapper.
        """
        data = await service.create(data_in)
        return ResponseWrapper(data=data)

    async def get_endpoint(self, id: Any, service: BaseService) -> ResponseWrapper:
        """Execute a single-record query lookup.

        Args:
            id: The primary key of the target entity.
            service: Active business service instance.

        Returns:
            The resolved model record wrapped in a ResponseWrapper.
        """
        data = await service.get(id=id)
        return ResponseWrapper(data=data)

    async def get_all_endpoint(
        self, service: BaseService, pagination: Any = None
    ) -> ResponseWrapper:
        """Execute batch query listings, applying optional page boundaries.

        Args:
            service: Active business service instance.
            pagination: Optional offset or keyset cursor parameters. Defaults to None.

        Returns:
            The list of resolved model records wrapped in a ResponseWrapper.
        """
        result = await service.get_list(pagination=pagination)
        from zcore.db.pagination import PaginatedResult

        if isinstance(result, PaginatedResult):
            return ResponseWrapper(data=result.data, meta=result.meta)
        return ResponseWrapper(data=result)

    async def search_endpoint(
        self, search_in: SearchRequest, service: BaseService
    ) -> ResponseWrapper:
        """Execute dynamic filter searches, applying mapped page limits.

        Args:
            search_in: Target filtering limits request parameters.
            service: Active business service instance.

        Returns:
            The matching model records wrapped in a ResponseWrapper.
        """
        pagination = None
        if self.pagination_class:
            from zcore.db.pagination import CursorParams, PageNumberParams

            default_size = self.default_page_size or getattr(
                settings, "PAGINATION_DEFAULT_SIZE", 20
            )
            max_size = self.max_page_size or getattr(settings, "PAGINATION_MAX_SIZE", 100)

            effective_size = min(search_in.size or default_size, max_size)

            if self.pagination_class.params_class == CursorParams:
                pagination = CursorParams(cursor=search_in.cursor, size=effective_size)
            else:
                pagination = PageNumberParams(page=search_in.page, size=effective_size)

        result = await service.search(search_in, pagination)
        from zcore.db.pagination import PaginatedResult

        if isinstance(result, PaginatedResult):
            return ResponseWrapper(data=result.data, meta=result.meta)
        return ResponseWrapper(data=result)

    async def lookup_endpoint(
        self, search_in: SearchRequest, service: BaseService
    ) -> ResponseWrapper:
        """Execute lightweight, field-restricted relational lookup queries.

        Args:
            search_in: Filter criteria and pagination limits.
            service: Active business service instance.

        Returns:
            Optimized, projected entity records wrapped in a ResponseWrapper.
        """
        allowed_fields = self._get_effective_lookup_fields()
        self._validate_lookup_request(search_in, allowed_fields)

        max_limit = (
            self.max_lookup_size
            or self.max_page_size
            or getattr(settings, "PAGINATION_MAX_SIZE", 100)
        )
        default_limit = self.default_page_size or getattr(settings, "PAGINATION_DEFAULT_SIZE", 20)

        effective_size = min(search_in.size or default_limit, max_limit)
        search_in.size = effective_size

        load_fields, loader_options = self._resolve_lookup_projections()

        pagination = None
        if self.pagination_class:
            from zcore.db.pagination import CursorParams, PageNumberParams

            if self.pagination_class.params_class == CursorParams:
                pagination = CursorParams(cursor=search_in.cursor, size=search_in.size)
            else:
                pagination = PageNumberParams(page=search_in.page, size=search_in.size)

        result = await service.search(
            search_in,
            pagination=pagination,
            fields=load_fields,
            options=loader_options,
        )
        from zcore.db.pagination import PaginatedResult

        if isinstance(result, PaginatedResult):
            return ResponseWrapper(data=result.data, meta=result.meta)
        return ResponseWrapper(data=result)

    async def update_endpoint(
        self,
        id: Any,
        data_in: Any,
        service: BaseService,
    ) -> ResponseWrapper:
        """Execute a full-record entity update transaction.

        Args:
            id: Target entity identifier to update.
            data_in: Validated schema containing updated properties.
            service: Active business service instance.

        Returns:
            The updated model record wrapped in a ResponseWrapper.
        """
        data = await service.update(id, data_in)
        return ResponseWrapper(data=data)

    async def patch_endpoint(
        self,
        id: Any,
        data_in: Any,
        service: BaseService,
    ) -> ResponseWrapper:
        """Execute a partial record update (PATCH) transaction.

        Args:
            id: Target entity identifier to patch.
            data_in: Validated schema containing partial changes.
            service: Active business service instance.

        Returns:
            The updated model record wrapped in a ResponseWrapper.
        """
        data = await service.update(id, data_in, partial=True)
        return ResponseWrapper(data=data)

    async def delete_endpoint(
        self, id: Any, service: BaseService, force: bool = False
    ) -> ResponseWrapper:
        """Execute a single-record delete transaction.

        Args:
            id: Target entity identifier to delete.
            service: Active business service instance.
            force: If True, triggers permanent hard deletion. Defaults to False.

        Returns:
            A success response wrapped in a ResponseWrapper.
        """
        await service.delete(id, force=force)
        return ResponseWrapper(message="Deleted successfully")
