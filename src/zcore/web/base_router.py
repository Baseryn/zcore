"""Automated Web Router Scaffolding.

This module provides the generic `BaseRouter` interface, which scaffolds 
standard security-aware CRUD endpoints (POST, GET, GET_ALL, SEARCH, UPDATE, PATCH, DELETE) 
and integrates them with services, schemas, dependency requirements, and pagination handlers.
"""

import uuid
from enum import StrEnum
from typing import TypeVar, Generic, Type, Any, Optional, Union, TYPE_CHECKING
from pydantic import BaseModel
from fastapi import APIRouter, status, Depends
from fastapi.routing import APIRoute
from fastapi.params import Depends as DependsClass

from zcore.security.permissions import HasScopes
from zcore.web.response import ResponseWrapper
from zcore.web.api_router import ZCoreAPIRoute
from zcore.db.search import SearchRequest
from zcore.service.base import BaseService
from zcore.kernel.di import Injector

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
    UPDATE = "UPDATE"
    PATCH = "PATCH"
    DELETE = "DELETE"


class BaseRouter(Generic[CreateSchemaType, UpdateSchemaType]):
    """Generic web router orchestrator.

    Automatically maps operations to matching database model dependencies and handles
    dependency injections and schema checks.

    Attributes:
        model: The database declarative model class.
        create_schema: Schema class for validating entity creations.
        update_schema: Schema class for validating entity updates.
        schema_out: Schema class representing responses.
        service: Business service callable class.
        prefix: Path prefix representing the route.
        tags: Endpoint group classification tags.
        exclude: Explicit endpoints to bypass during route scaffolding.
        pagination_class: Pagination engine class to construct list queries.
        route_class: Custom routing processing class. Defaults to ZCoreAPIRoute.
        expose_schemas: Exposes target endpoint schemas dynamically.
    """

    model: Type[Any]
    create_schema: Optional[Type[CreateSchemaType]] = None
    update_schema: Optional[Type[UpdateSchemaType]] = None
    schema_out: Optional[Type[BaseModel]] = None
    service: Any = None

    prefix: str = ""
    tags: Optional[list[str]] = None
    exclude: Optional[set[RouteKey]] = None
    pagination_class: Optional[Type["BasePagination"]] = None
    route_class: Type[APIRoute] = ZCoreAPIRoute

    expose_schemas: Union[set[RouteKey], bool] = False

    def __init__(self) -> None:
        """Initialize the BaseRouter.

        Performs fail-fast configuration checks and registers configured CRUD endpoints.

        Raises:
            ValueError: If the required service parameter is not configured.
        """
        # Fail-Fast Startup Verification
        if not self.service:
            raise ValueError(f"Service class must be defined in '{self.__class__.__name__}'.")
            
        self.router = APIRouter(
            prefix=self.prefix,
            tags=self.tags or [],
            route_class=self.route_class
        )
        self.exclude = self.exclude or set()
        self._validate_schema_configurations()
        self._register_routes()

    def _validate_schema_configurations(self) -> None:
        """Perform validation checks on configured route schema definitions.

        Raises:
            ValueError: If an active endpoint lacks required schema configurations.
        """
        if RouteKey.POST not in self.exclude and self.create_schema is None:
            raise ValueError(f"POST route is enabled in '{self.__class__.__name__}', but 'create_schema' is None.")
        if (RouteKey.UPDATE not in self.exclude or RouteKey.PATCH not in self.exclude) and self.update_schema is None:
            raise ValueError(f"UPDATE/PATCH route is enabled in '{self.__class__.__name__}', but 'update_schema' is None.")
        
        if not getattr(self, "model", None):
            active_standard_routes = set(RouteKey) - (self.exclude or set())
            if active_standard_routes:
                raise ValueError(f"Model class must be defined in '{self.__class__.__name__}' to resolve route actions.")

    def _get_openapi_extra(self, route_key: RouteKey) -> Optional[dict[str, Any]]:
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

    def _normalize_dependencies(self, raw_deps: Union[list[Any], Any]) -> list[DependsClass]:
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
            raise ValueError(f"Model class must be defined in {self.__class__.__name__} to resolve route actions.")
        
        action_map = {
            RouteKey.POST: self.model.actions().CREATE,
            RouteKey.GET: self.model.actions().VIEW,
            RouteKey.GET_ALL: self.model.actions().LISTVIEW,
            RouteKey.SEARCH: self.model.actions().LISTVIEW,
            RouteKey.UPDATE: self.model.actions().UPDATE,
            RouteKey.PATCH: self.model.actions().UPDATE,
            RouteKey.DELETE: self.model.actions().DELETE,
        }
        return action_map[route_key]

    def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
        """Generate default route dependencies (authentication, authorization, logging, etc.).

        Subclasses can override this method to inject dynamic, runtime dependencies
        with full OOP flexibility.

        Args:
            route_key: The target operational route key.
            action: The computed database/permission action identifier.

        Returns:
            A list of dependencies (callable, classes or FastAPI Depends objects).
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

    def _sort_routes(self) -> None:
        """Sort routes to place parameterized dynamic paths at the end of the routing table.

        This avoids dynamic routes (e.g., /{id:uuid}) from shadowing static sibling endpoints (e.g., /search).
        """
        self.router.routes.sort(key=lambda r: "{" in r.path)

    def _register_routes(self) -> None:
        """Dynamically generate and bind endpoints to the APIRouter."""
        service_callable = self.service
        service_dependency = Depends(Injector(service_callable))

        if RouteKey.POST not in self.exclude:
            c_schema = self.create_schema
            async def _create_endpoint(data_in: c_schema, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.create_endpoint(data_in, service_inst, **kwargs)
            
            self.router.add_api_route(
                path="/",
                endpoint=_create_endpoint,
                methods=["POST"],
                dependencies=self._get_route_dependencies(RouteKey.POST),
                status_code=status.HTTP_201_CREATED,
                response_model=ResponseWrapper[self.schema_out] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.POST)
            )

        if RouteKey.GET not in self.exclude:
            async def _get_endpoint(id: uuid.UUID, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.get_endpoint(id, service_inst, **kwargs)

            self.router.add_api_route(
                path="/{id:uuid}",
                endpoint=_get_endpoint,
                methods=["GET"],
                dependencies=self._get_route_dependencies(RouteKey.GET),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.GET)
            )

        if RouteKey.GET_ALL not in self.exclude:
            if self.pagination_class:
                params_class = self.pagination_class.params_class
                
                async def _get_all_endpoint(params: params_class = Depends(), service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                    return await self.get_all_endpoint(service_inst, params, **kwargs)
            else:
                async def _get_all_endpoint(service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                    return await self.get_all_endpoint(service_inst, **kwargs)

            self.router.add_api_route(
                path="/",
                endpoint=_get_all_endpoint,
                methods=["GET"],
                dependencies=self._get_route_dependencies(RouteKey.GET_ALL),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[list[self.schema_out]] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.GET_ALL)
            )

        if RouteKey.SEARCH not in self.exclude:
            async def _search_endpoint(search_in: SearchRequest, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.search_endpoint(search_in, service_inst, **kwargs)

            self.router.add_api_route(
                path="/search",
                endpoint=_search_endpoint,
                methods=["POST"],
                dependencies=self._get_route_dependencies(RouteKey.SEARCH),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[list[self.schema_out]] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.SEARCH)
            )

        if RouteKey.UPDATE not in self.exclude:
            u_schema = self.update_schema
            
            async def _update_endpoint(id: uuid.UUID, data_in: u_schema, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.update_endpoint(id, data_in, service_inst, **kwargs)

            self.router.add_api_route(
                path="/{id:uuid}",
                endpoint=_update_endpoint,
                methods=["PUT"],
                dependencies=self._get_route_dependencies(RouteKey.UPDATE),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.UPDATE)
            )

        if RouteKey.PATCH not in self.exclude:
            u_schema = self.update_schema
            
            async def _patch_endpoint(id: uuid.UUID, data_in: u_schema, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.patch_endpoint(id, data_in, service_inst, **kwargs)
                
            self.router.add_api_route(
                path="/{id:uuid}",
                endpoint=_patch_endpoint,
                methods=["PATCH"],
                dependencies=self._get_route_dependencies(RouteKey.PATCH),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper[self.schema_out] if self.schema_out else ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.PATCH)
            )

        if RouteKey.DELETE not in self.exclude:
            async def _delete_endpoint(id: uuid.UUID, service_inst: BaseService = service_dependency, **kwargs: Any) -> ResponseWrapper:
                return await self.delete_endpoint(id, service_inst, **kwargs)

            self.router.add_api_route(
                path="/{id:uuid}",
                endpoint=_delete_endpoint,
                methods=["DELETE"],
                dependencies=self._get_route_dependencies(RouteKey.DELETE),
                status_code=status.HTTP_200_OK,
                response_model=ResponseWrapper,
                openapi_extra=self._get_openapi_extra(RouteKey.DELETE)
            )
            
        self._sort_routes()
            
    async def create_endpoint(self, data_in: CreateSchemaType, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute the POST creation transaction.

        Args:
            data_in: Validated input schema containing creation properties.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The created entity wrapped in a ResponseWrapper.
        """
        data = await service.create(data_in)
        return ResponseWrapper(data=data)
    
    async def get_endpoint(self, id: uuid.UUID, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute a single-record query lookup.

        Args:
            id: The primary key of the target entity.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The resolved model record wrapped in a ResponseWrapper.
        """
        data = await service.get(id=id)
        return ResponseWrapper(data=data)
    
    async def get_all_endpoint(self, service: BaseService, pagination: Any = None, **kwargs: Any) -> ResponseWrapper:
        """Execute batch query listings, applying optional page boundaries.

        Args:
            service: Active business service instance.
            pagination: Optional offset or keyset cursor parameters. Defaults to None.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The list of resolved model records wrapped in a ResponseWrapper.
        """
        result = await service.get_list(pagination)
        from zcore.db.pagination import PaginatedResult
        if isinstance(result, PaginatedResult):
            return ResponseWrapper(data=result.data, meta=result.meta)
        return ResponseWrapper(data=result)
    
    async def search_endpoint(self, search_in: SearchRequest, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute dynamic filter searches, applying mapped page limits.

        Args:
            search_in: Target filtering limits request parameters.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The matching model records wrapped in a ResponseWrapper.
        """
        pagination = None
        from zcore.db.pagination import PageNumberParams, CursorParams
        if self.pagination_class:
            if self.pagination_class.params_class == CursorParams:
                pagination = CursorParams(cursor=search_in.cursor, size=search_in.size)
            else:
                pagination = PageNumberParams(page=search_in.page, size=search_in.size)

        result = await service.search(search_in, pagination)
        from zcore.db.pagination import PaginatedResult
        if isinstance(result, PaginatedResult):
            return ResponseWrapper(data=result.data, meta=result.meta)
        return ResponseWrapper(data=result)
    
    async def update_endpoint(self, id: uuid.UUID, data_in: UpdateSchemaType, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute a full-record entity update transaction.

        Args:
            id: Target entity identifier to update.
            data_in: Validated schema containing updated properties.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The updated model record wrapped in a ResponseWrapper.
        """
        data = await service.update(id, data_in)
        return ResponseWrapper(data=data)
    
    async def patch_endpoint(self, id: uuid.UUID, data_in: UpdateSchemaType, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute a partial record update (PATCH) transaction.

        Args:
            id: Target entity identifier to patch.
            data_in: Validated schema containing partial changes.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            The updated model record wrapped in a ResponseWrapper.
        """
        data = await service.update(id, data_in, partial=True)
        return ResponseWrapper(data=data)
    
    async def delete_endpoint(self, id: uuid.UUID, service: BaseService, **kwargs: Any) -> ResponseWrapper:
        """Execute a single-record delete transaction.

        Args:
            id: Target entity identifier to delete.
            service: Active business service instance.
            **kwargs: Extra dynamic route arguments.

        Returns:
            A success response wrapped in a ResponseWrapper.
        """
        await service.delete(id)
        return ResponseWrapper(message="Deleted successfully")