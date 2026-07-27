"""Database Engine and Declarative Base Infrastructure.

This module initializes the core SQLAlchemy engine and session factories for 
asynchronous communication. It also provides the fundamental declarative base class 
enriched with class-level metadata helper methods to manage object security permissions.
"""

import time
import structlog
from fastapi import Depends
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, Any, Optional, Annotated
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from zcore.kernel.di import container


@dataclass(frozen=True)
class Actions:
    """Action permission mappings tied to a specific database model.

    This immutable container maps standard CRUD/view operational concepts to unique 
    permission keys for use in security policy evaluation.

    Attributes:
        LISTVIEW: Security action key for listing multiple model entities.
        VIEW: Security action key for viewing details of a single model entity.
        CREATE: Security action key for generating a new model entity.
        UPDATE: Security action key for updating an existing model entity.
        DELETE: Security action key for removing a model entity.
    """

    LISTVIEW: str
    VIEW: str
    CREATE: str
    UPDATE: str
    DELETE: str

    @classmethod
    def actions(cls, t_name: str) -> "Actions":
        """Generate formatted security actions keyed specifically to a table name.

        Args:
            t_name: The database table name associated with the actions.

        Returns:
            An instance of Actions containing computed permission keys.
        """
        actions = {}
        for action in cls.__dataclass_fields__.keys():
            actions[action] = f"{t_name}:{action.lower()}"
        return cls(**actions)


class Base(DeclarativeBase):
    """Declarative Base class for SQLAlchemy ORM models in ZCore.

    Provides a foundational configuration structure, including methods to expose standard 
    authorization action keys mapped directly to database tables.
    """

    @classmethod
    def actions(cls) -> Actions:
        """Construct the permission actions descriptor mapped to this model's table.

        Returns:
            The standard descriptive Actions instance for the model.

        Raises:
            AttributeError: If the subclass model has not defined a `__tablename__` property.
        """
        t_name = getattr(cls, "__tablename__", None)
        if not t_name:
            raise AttributeError(f"Model {cls.__name__} does not have a __tablename__ defined.")
        return Actions.actions(t_name)


class DatabaseManager:
    """Coordinator for the asynchronous database connection pool and engine lifecycles.

    Manages the creation and disposal of the primary asynchronous database engine and 
    exposes a session factory utilized across the application.

    Attributes:
        _engine: The active SQLAlchemy `AsyncEngine` instance, or None if uninitialized.
        _session_factory: The configured factory class for creating new database sessions,
            or None if uninitialized.
    """

    def __init__(self) -> None:
        """Initialize the DatabaseManager with empty internal engines and factories."""
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    def _register_query_logger(self, sync_engine: Engine) -> None:
        """Register events on connection cursors to intercept, sanitize, and log query statistics."""
        dialect_name = sync_engine.dialect.name
        db_logger = structlog.get_logger(f"zcore.db.{dialect_name}")
        
        @event.listens_for(sync_engine, "before_cursor_execute")
        def before_cursor_execute(conn, cursor, statement, parameters, context, exec_many):
            context._query_start_time = time.perf_counter()

        @event.listens_for(sync_engine, "after_cursor_execute")
        def after_cursor_execute(conn, cursor, statement, parameters, context, exec_many):
            start_time = getattr(context, "_query_start_time", None)
            duration_ms = 0.0
            if start_time:
                duration_ms = (time.perf_counter() - start_time) * 1000

            compact_statement = " ".join(statement.split())
            
            # Bypass low-level database metadata or catalog lookups to avoid noise
            ignored_keywords = ["pg_catalog", "schema", "standard_conforming_strings", "transaction"]
            if any(kw in compact_statement.lower() for kw in ignored_keywords):
                return

            db_logger.info(
                "sql_query",
                sql=compact_statement,
                params=parameters,
                duration_ms=round(duration_ms, 2)
            )

    def init_app(
        self, 
        db_url: str, 
        pool_size: int = 5, 
        max_overflow: int = 10, 
        pool_recycle: int = 1800, 
        echo: bool = False,
        **engine_kwargs: Any
    ) -> None:
        """Configure the connection pool, engine, and session factories.

        Configures parameters for SQLite and server-based relational engines
        (such as PostgreSQL).

        Args:
            db_url: The primary database connection URL.
            pool_size: The connection pool size for non-SQLite databases. Defaults to 5.
            max_overflow: The max overflowing connections beyond pool size. Defaults to 10.
            pool_recycle: Connection recycle time in seconds. Defaults to 1800.
            echo: Verbose SQL logging flag. Unused directly, replaced by interceptor.
            **engine_kwargs: Additional keyword arguments forwarded to `create_async_engine`.
        """
        kwargs: dict[str, Any] = {}
        if "sqlite" not in db_url:
            kwargs["pool_size"] = pool_size
            kwargs["max_overflow"] = max_overflow
            kwargs["pool_recycle"] = pool_recycle

        self._engine = create_async_engine(
            db_url,
            echo=False,  # Core SQLAlchemy raw echo is disabled to prevent logger double-cluttering
            pool_pre_ping=True,
            **kwargs,
            **engine_kwargs
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        # Attach the query-timing event hook directly to the underlying sync engine pool
        self._register_query_logger(self._engine.sync_engine)
        
        # Resolve dialect-specific structural logging namespace
        dialect_logger = structlog.get_logger(f"zcore.db.{self._engine.dialect.name}")
        dialect_logger.info("DatabaseManager successfully initialized with dialect statement logger.")

    async def close(self) -> None:
        """Dispose of the database connection pool and terminate engine lifecycles."""
        if self._engine:
            await self._engine.dispose()
            
            dialect_logger = structlog.get_logger(f"zcore.db.{self._engine.dialect.name}")
            dialect_logger.info("DatabaseManager engine connections closed.")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide an asynchronous context manager for executing database operations.

        Yields:
            An active `AsyncSession` ready for transactional queries.

        Raises:
            RuntimeError: If called before `init_app` is executed.
            Exception: If an error is encountered inside the session block, triggering
                an automatic rollback before propagating.
        """
        if not self._session_factory:
            raise RuntimeError("DatabaseManager has not been initialized. Call init_app() first.")
            
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


db_manager = DatabaseManager()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Retrieve an active database session dependency for web requests.

    Yields:
        An active `AsyncSession` instance managed by FastAPI's dependency injection system.
    """
    session = container.resolve(AsyncSession)
    yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]