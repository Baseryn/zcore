from typing import TYPE_CHECKING, Any
from zcore.config import settings

if TYPE_CHECKING:
    from zcore.kernel.engine import Kernel
    from zcore.kernel.plugins import Plugin
    from zcore.kernel.di import container, Inject
    from zcore.db.setup import Base, SessionDep, db_manager, get_db
    from zcore.db.repository import BaseRepository
    from zcore.db.search import SearchRequest
    from zcore.db.uow import UnitOfWork
    from zcore.service.base import BaseService
    from zcore.web.base_router import BaseRouter, RouteKey
    from zcore.web.response import ResponseWrapper
    from zcore.web.api_router import ZCoreAPIRoute
    from zcore.web.projection import Zchema
    from zcore.context.context import ctx, ZContext

__all__ = [
    'Inject',
    'container',
    'Kernel',
    'settings',
    'Base',
    'SessionDep',
    'db_manager',
    'get_db',
    'BaseRepository',
    'SearchRequest',
    'UnitOfWork',
    'BaseService',
    'BaseRouter',
    'RouteKey',
    'ResponseWrapper',
    'ZCoreAPIRoute',
    'Zchema',
    'Plugin',
    'ctx',
    "ZContext"
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
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")