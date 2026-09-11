"""ZCore Framework Core Package Entrypoint."""

from typing import TYPE_CHECKING, Any

from zcore.config import settings

if TYPE_CHECKING:
    from zcore.config import (
        DatabaseSettings,
        LoggingSettings,
        Settings,
        get_settings,
        initialize_settings,
    )
    from zcore.context.context import ZContext, ctx
    from zcore.db.events import dispatch_db_event, register_db_event_dispatcher
    from zcore.db.pagination import (
        BasePagination,
        CursorPagination,
        CursorParams,
        PageNumberPagination,
        PageNumberParams,
        PaginatedResult,
    )
    from zcore.db.repository import AbstractRepository, BaseRepository
    from zcore.db.search import SearchEngine, SearchRequest
    from zcore.db.setup import Actions, Base, SessionDep, db_manager, get_db
    from zcore.db.soft_delete import SoftDeleteMixin
    from zcore.db.uow import UnitOfWork
    from zcore.kernel.di import (
        Inject,
        Injector,
        IoCContainer,
        background_scope,
        background_task,
        container,
    )
    from zcore.kernel.engine import Kernel
    from zcore.kernel.events import EventDispatcher, on_event
    from zcore.kernel.plugins import Plugin
    from zcore.security.auth import BaseAuth
    from zcore.security.permissions import BasePermission, HasScopes
    from zcore.security.protocols import UserProtocol
    from zcore.security.security import Security
    from zcore.service.base import AbstractService, BaseService
    from zcore.storage.base import StorageProvider, get_storage_provider
    from zcore.utils.helpers import CustomJSONEncoder, json_dumps, json_loads, slugify
    from zcore.utils.timezone import (
        ZDateTime,
        format_iso_with_app_timezone,
        get_app_timezone,
        now,
        to_app_timezone,
        utc_now,
    )
    from zcore.web.api_router import ZCoreAPIRoute, ZCoreJSONResponse, ZCoreRequest
    from zcore.web.base_router import BaseRouter, RouteKey
    from zcore.web.projection import Zchema
    from zcore.web.response import ResponseWrapper

__all__ = [
    "AbstractRepository",
    "AbstractService",
    "Actions",
    "Base",
    "BaseAuth",
    "BasePagination",
    "BasePermission",
    "BaseRepository",
    "BaseRouter",
    "BaseService",
    "CursorPagination",
    "CursorParams",
    "CustomJSONEncoder",
    "DatabaseSettings",
    "EventDispatcher",
    "HasScopes",
    "Inject",
    "Injector",
    "IoCContainer",
    "Kernel",
    "LoggingSettings",
    "PageNumberPagination",
    "PageNumberParams",
    "PaginatedResult",
    "Plugin",
    "ResponseWrapper",
    "RouteKey",
    "SearchEngine",
    "SearchRequest",
    "Security",
    "SessionDep",
    "Settings",
    "SoftDeleteMixin",
    "StorageProvider",
    "UnitOfWork",
    "UserProtocol",
    "ZContext",
    "ZCoreAPIRoute",
    "ZCoreJSONResponse",
    "ZCoreRequest",
    "ZDateTime",
    "Zchema",
    "background_scope",
    "background_task",
    "container",
    "ctx",
    "db_manager",
    "dispatch_db_event",
    "format_iso_with_app_timezone",
    "get_app_timezone",
    "get_db",
    "get_settings",
    "get_storage_provider",
    "initialize_settings",
    "json_dumps",
    "json_loads",
    "now",
    "on_event",
    "register_db_event_dispatcher",
    "settings",
    "slugify",
    "to_app_timezone",
    "utc_now",
]


def __getattr__(name: str) -> Any:
    """Dynamically resolve and lazily load framework components to prevent circular imports."""
    if name == "Kernel":
        from zcore.kernel.engine import Kernel

        return Kernel
    if name == "container":
        from zcore.kernel.di import container

        return container
    if name == "Inject":
        from zcore.kernel.di import Inject

        return Inject
    if name == "Injector":
        from zcore.kernel.di import Injector

        return Injector
    if name == "IoCContainer":
        from zcore.kernel.di import IoCContainer

        return IoCContainer
    if name == "background_scope":
        from zcore.kernel.di import background_scope

        return background_scope
    if name == "background_task":
        from zcore.kernel.di import background_task

        return background_task
    if name == "Base":
        from zcore.db.setup import Base

        return Base
    if name == "SessionDep":
        from zcore.db.setup import SessionDep

        return SessionDep
    if name == "db_manager":
        from zcore.db.setup import db_manager

        return db_manager
    if name == "get_db":
        from zcore.db.setup import get_db

        return get_db
    if name == "Actions":
        from zcore.db.setup import Actions

        return Actions
    if name == "BaseRepository":
        from zcore.db.repository import BaseRepository

        return BaseRepository
    if name == "AbstractRepository":
        from zcore.db.repository import AbstractRepository

        return AbstractRepository
    if name == "UnitOfWork":
        from zcore.db.uow import UnitOfWork

        return UnitOfWork
    if name == "SearchRequest":
        from zcore.db.search import SearchRequest

        return SearchRequest
    if name == "SearchEngine":
        from zcore.db.search import SearchEngine

        return SearchEngine
    if name == "BaseService":
        from zcore.service.base import BaseService

        return BaseService
    if name == "AbstractService":
        from zcore.service.base import AbstractService

        return AbstractService
    if name == "BaseRouter":
        from zcore.web.base_router import BaseRouter

        return BaseRouter
    if name == "ResponseWrapper":
        from zcore.web.response import ResponseWrapper

        return ResponseWrapper
    if name == "ZCoreAPIRoute":
        from zcore.web.api_router import ZCoreAPIRoute

        return ZCoreAPIRoute
    if name == "ZCoreJSONResponse":
        from zcore.web.api_router import ZCoreJSONResponse

        return ZCoreJSONResponse
    if name == "ZCoreRequest":
        from zcore.web.api_router import ZCoreRequest

        return ZCoreRequest
    if name == "Zchema":
        from zcore.web.projection import Zchema

        return Zchema
    if name == "RouteKey":
        from zcore.web.base_router import RouteKey

        return RouteKey
    if name == "Plugin":
        from zcore.kernel.plugins import Plugin

        return Plugin
    if name == "ZContext":
        from zcore.context.context import ZContext

        return ZContext
    if name == "ctx":
        from zcore.context.context import ctx

        return ctx
    if name == "Security":
        from zcore.security.security import Security

        return Security
    if name == "BaseAuth":
        from zcore.security.auth import BaseAuth

        return BaseAuth
    if name == "BasePermission":
        from zcore.security.permissions import BasePermission

        return BasePermission
    if name == "HasScopes":
        from zcore.security.permissions import HasScopes

        return HasScopes
    if name == "UserProtocol":
        from zcore.security.protocols import UserProtocol

        return UserProtocol
    if name == "on_event":
        from zcore.kernel.events import on_event

        return on_event
    if name == "EventDispatcher":
        from zcore.kernel.events import EventDispatcher

        return EventDispatcher
    if name == "SoftDeleteMixin":
        from zcore.db.soft_delete import SoftDeleteMixin

        return SoftDeleteMixin
    if name == "DatabaseSettings":
        from zcore.config import DatabaseSettings

        return DatabaseSettings
    if name == "LoggingSettings":
        from zcore.config import LoggingSettings

        return LoggingSettings
    if name == "Settings":
        from zcore.config import Settings

        return Settings
    if name == "get_settings":
        from zcore.config import get_settings

        return get_settings
    if name == "initialize_settings":
        from zcore.config import initialize_settings

        return initialize_settings
    if name == "ZDateTime":
        from zcore.utils.timezone import ZDateTime

        return ZDateTime
    if name == "now":
        from zcore.utils.timezone import now

        return now
    if name == "utc_now":
        from zcore.utils.timezone import utc_now

        return utc_now
    if name == "to_app_timezone":
        from zcore.utils.timezone import to_app_timezone

        return to_app_timezone
    if name == "get_app_timezone":
        from zcore.utils.timezone import get_app_timezone

        return get_app_timezone
    if name == "format_iso_with_app_timezone":
        from zcore.utils.timezone import format_iso_with_app_timezone

        return format_iso_with_app_timezone
    if name == "PaginatedResult":
        from zcore.db.pagination import PaginatedResult

        return PaginatedResult
    if name == "PageNumberParams":
        from zcore.db.pagination import PageNumberParams

        return PageNumberParams
    if name == "CursorParams":
        from zcore.db.pagination import CursorParams

        return CursorParams
    if name == "BasePagination":
        from zcore.db.pagination import BasePagination

        return BasePagination
    if name == "PageNumberPagination":
        from zcore.db.pagination import PageNumberPagination

        return PageNumberPagination
    if name == "CursorPagination":
        from zcore.db.pagination import CursorPagination

        return CursorPagination
    if name == "dispatch_db_event":
        from zcore.db.events import dispatch_db_event

        return dispatch_db_event
    if name == "register_db_event_dispatcher":
        from zcore.db.events import register_db_event_dispatcher

        return register_db_event_dispatcher
    if name == "StorageProvider":
        from zcore.storage.base import StorageProvider

        return StorageProvider
    if name == "get_storage_provider":
        from zcore.storage.base import get_storage_provider

        return get_storage_provider
    if name == "json_dumps":
        from zcore.utils.helpers import json_dumps

        return json_dumps
    if name == "json_loads":
        from zcore.utils.helpers import json_loads

        return json_loads
    if name == "slugify":
        from zcore.utils.helpers import slugify

        return slugify
    if name == "CustomJSONEncoder":
        from zcore.utils.helpers import CustomJSONEncoder

        return CustomJSONEncoder

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")