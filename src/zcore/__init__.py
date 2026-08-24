from typing import TYPE_CHECKING, Any

from zcore.config import settings

if TYPE_CHECKING:
    from zcore.context.context import ZContext, ctx
    from zcore.db.repository import BaseRepository
    from zcore.db.search import SearchRequest
    from zcore.db.setup import Base, SessionDep, db_manager, get_db
    from zcore.db.soft_delete import SoftDeleteMixin
    from zcore.db.uow import UnitOfWork
    from zcore.kernel.di import Inject, container
    from zcore.kernel.engine import Kernel
    from zcore.kernel.events import EventDispatcher, on_event
    from zcore.kernel.plugins import Plugin
    from zcore.security.auth import BaseAuth
    from zcore.security.security import Security
    from zcore.service.base import BaseService
    from zcore.web.api_router import ZCoreAPIRoute
    from zcore.web.base_router import BaseRouter, RouteKey
    from zcore.web.projection import Zchema
    from zcore.web.response import ResponseWrapper

__all__ = [
    "Base",
    "BaseAuth",
    "BaseRepository",
    "BaseRouter",
    "BaseService",
    "EventDispatcher",
    "Inject",
    "Kernel",
    "Plugin",
    "ResponseWrapper",
    "RouteKey",
    "SearchRequest",
    "Security",
    "SessionDep",
    "SoftDeleteMixin",
    "UnitOfWork",
    "ZContext",
    "ZCoreAPIRoute",
    "Zchema",
    "container",
    "ctx",
    "db_manager",
    "get_db",
    "on_event",
    "settings",
]


def __getattr__(name: str) -> Any:
    if name == "Kernel":
        from zcore.kernel.engine import Kernel

        return Kernel
    if name == "container":
        from zcore.kernel.di import container

        return container
    if name == "Inject":
        from zcore.kernel.di import Inject

        return Inject
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
    if name == "BaseRepository":
        from zcore.db.repository import BaseRepository

        return BaseRepository
    if name == "UnitOfWork":
        from zcore.db.uow import UnitOfWork

        return UnitOfWork
    if name == "SearchRequest":
        from zcore.db.search import SearchRequest

        return SearchRequest
    if name == "BaseService":
        from zcore.service.base import BaseService

        return BaseService
    if name == "BaseRouter":
        from zcore.web.base_router import BaseRouter

        return BaseRouter
    if name == "ResponseWrapper":
        from zcore.web.response import ResponseWrapper

        return ResponseWrapper
    if name == "ZCoreAPIRoute":
        from zcore.web.api_router import ZCoreAPIRoute

        return ZCoreAPIRoute
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
    if name == "on_event":
        from zcore.kernel.events import on_event

        return on_event
    if name == "EventDispatcher":
        from zcore.kernel.events import EventDispatcher

        return EventDispatcher
    
    if name == "SoftDeleteMixin":
        from zcore.db.soft_delete import SoftDeleteMixin

        return SoftDeleteMixin

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
