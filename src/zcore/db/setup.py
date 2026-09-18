"""Database Engine and Declarative Base Infrastructure.

This module initializes the core SQLAlchemy engine and session factories for
asynchronous communication. It also provides the fundamental declarative base class
enriched with class-level metadata helper methods to manage object security permissions.
"""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import structlog
from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from zcore.config import settings
from zcore.kernel.di import container
from zcore.utils.helpers import json_dumps, json_loads


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
        for action in cls.__dataclass_fields__:
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
            raise AttributeError(
                f"Model {cls.__name__} does not have a __tablename__ defined."
            )
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
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    def _register_query_logger(self, sync_engine: Engine) -> None:
        """Register events on connection cursors to intercept, sanitize, and log query statistics.

        Args:
            sync_engine: The synchronous core SQLAlchemy Engine instance.
        """
        dialect_name = sync_engine.dialect.name
        db_logger = structlog.get_logger(f"zcore.db.{dialect_name}")

        @event.listens_for(sync_engine, "before_cursor_execute")
        def before_cursor_execute(
            conn, cursor, statement, parameters, context, exec_many
        ):
            context._query_start_time = time.perf_counter()

        @event.listens_for(sync_engine, "after_cursor_execute")
        def after_cursor_execute(
            conn, cursor, statement, parameters, context, exec_many
        ):
            logging_cfg = getattr(settings, "LOGGING", None)
            if logging_cfg and not getattr(logging_cfg, "log_sql_queries", True):
                return

            start_time = getattr(context, "_query_start_time", None)
            duration_ms = 0.0
            if start_time:
                duration_ms = (time.perf_counter() - start_time) * 1000

            slow_threshold = (
                getattr(logging_cfg, "slow_query_threshold_ms", None)
                if logging_cfg
                else None
            )
            if slow_threshold is not None and duration_ms < slow_threshold:
                return

            compact_statement = " ".join(statement.split())

            ignored_keywords = [
                "pg_catalog",
                "schema",
                "standard_conforming_strings",
                "transaction",
            ]
            if any(kw in compact_statement.lower() for kw in ignored_keywords):
                return

            db_logger.info(
                "sql_query",
                sql=compact_statement,
                params=parameters,
                duration_ms=round(duration_ms, 2),
            )

    def init_app(
        self,
        db_url: str | None = None,
        config: Any | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle: int = 1800,
        pool_pre_ping: bool = True,
        echo: bool = False,
        connect_args: dict[str, Any] | None = None,
        execution_options: dict[str, Any] | None = None,
        **engine_kwargs: Any,
    ) -> None:
        """Configure the connection pool, engine, and session factories.

        Configures parameters for SQLite and server-based relational engines
        (such as PostgreSQL or MySQL), supporting structured settings, custom
        connect_args, execution_options, and JSON serializers.

        Args:
            db_url: The primary database connection URL. Defaults to None.
            config: An optional `DatabaseSettings` instance or configuration dictionary.
                Defaults to None.
            pool_size: The connection pool size for non-SQLite databases. Defaults to 5.
            max_overflow: The max overflowing connections beyond pool size. Defaults to 10.
            pool_recycle: Connection recycle time in seconds. Defaults to 1800.
            pool_pre_ping: Boolean flag to enable pool pre-ping connection checks. Defaults to True.
            echo: Verbose SQL logging flag. Unused directly, replaced by interceptor.
            connect_args: Connection arguments passed directly to the DBAPI driver.
            execution_options: Execution options passed to the SQLAlchemy engine.
            **engine_kwargs: Additional keyword arguments forwarded to `create_async_engine`.

        Raises:
            ValueError: If no valid database URL is provided directly or via config.
        """
        target_url = db_url
        target_pool_size = pool_size
        target_max_overflow = max_overflow
        target_pool_recycle = pool_recycle
        target_pool_pre_ping = pool_pre_ping
        target_connect_args = connect_args or {}
        target_execution_options = execution_options or {}
        extra_kwargs = dict(engine_kwargs)

        if config is not None:
            if hasattr(config, "model_dump"):
                cfg_dict = config.model_dump()
            elif isinstance(config, dict):
                cfg_dict = dict(config)
            else:
                cfg_dict = {}

            target_url = cfg_dict.get("url", target_url)
            target_pool_size = cfg_dict.get("pool_size", target_pool_size)
            target_max_overflow = cfg_dict.get("max_overflow", target_max_overflow)
            target_pool_recycle = cfg_dict.get("pool_recycle", target_pool_recycle)
            target_pool_pre_ping = cfg_dict.get("pool_pre_ping", target_pool_pre_ping)

            cfg_connect_args = cfg_dict.get("connect_args")
            if isinstance(cfg_connect_args, dict):
                target_connect_args = {**target_connect_args, **cfg_connect_args}

            cfg_execution_options = cfg_dict.get("execution_options")
            if isinstance(cfg_execution_options, dict):
                target_execution_options = {
                    **target_execution_options,
                    **cfg_execution_options,
                }

            cfg_extra = cfg_dict.get("extra_engine_kwargs")
            if isinstance(cfg_extra, dict):
                extra_kwargs.update(cfg_extra)

        if not target_url:
            raise ValueError("A valid database URL must be provided to init_app.")

        final_engine_kwargs: dict[str, Any] = {
            "echo": False,
            "json_serializer": json_dumps,
            "json_deserializer": json_loads,
            "pool_pre_ping": target_pool_pre_ping,
            **extra_kwargs,
        }

        if "sqlite" not in target_url.lower():
            final_engine_kwargs["pool_size"] = target_pool_size
            final_engine_kwargs["max_overflow"] = target_max_overflow
            final_engine_kwargs["pool_recycle"] = target_pool_recycle

        if target_connect_args:
            final_engine_kwargs["connect_args"] = target_connect_args

        if target_execution_options:
            final_engine_kwargs["execution_options"] = target_execution_options

        self._engine = create_async_engine(
            target_url,
            **final_engine_kwargs,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine, class_=AsyncSession, expire_on_commit=False
        )

        self._register_query_logger(self._engine.sync_engine)

        dialect_logger = structlog.get_logger(f"zcore.db.{self._engine.dialect.name}")
        dialect_logger.info(
            "DatabaseManager successfully initialized with dialect statement logger."
        )

    async def close(self) -> None:
        """Dispose of the database connection pool and terminate engine lifecycles."""
        if self._engine:
            await self._engine.dispose()

            dialect_logger = structlog.get_logger(
                f"zcore.db.{self._engine.dialect.name}"
            )
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
            raise RuntimeError(
                "DatabaseManager has not been initialized. Call init_app() first."
            )

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