"""ZCore Core Configuration Module.

This module provides the core settings and configuration loading infrastructure for the
ZCore framework. It leverages Pydantic Settings (v2) for validation and environment
variable parsing, and registers itself within the dependency injection (DI) container
to support singleton management. A dynamic proxy is also provided to support lazy resolution
across the application lifecycle.
"""

import os
from typing import Any, TypeVar, cast

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from zcore.kernel.di import container

T = TypeVar("T", bound="Settings")


class DatabaseSettings(BaseModel):
    """Database connection and engine configuration schema."""

    url: str = "sqlite+aiosqlite:///zcore.db"
    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    echo: bool = False
    connect_args: dict[str, Any] = Field(default_factory=dict)
    execution_options: dict[str, Any] = Field(default_factory=dict)
    extra_engine_kwargs: dict[str, Any] = Field(default_factory=dict)

class LoggingSettings(BaseModel):
    """Structured logging configuration schema."""

    level: str = "INFO"
    json_format: bool | None = None
    log_sql_queries: bool = True
    slow_query_threshold_ms: float | None = None
    file_path: str | None = None
    muted_loggers: list[str] = Field(
        default_factory=lambda: [
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
            "sqlalchemy.engine",
        ]
    )
    custom_processors: list[Any] = Field(default_factory=list)

class Settings(BaseSettings):
    """Core settings and environment variables configuration for the ZCore framework.

    This class parses configuration variables from both environment variables and
    optional file-based sources (such as a `.env` file). It manages configuration for
    the database engine, authentication parameters, file storage paths, timezone policies,
    and other core services.

    Attributes:
        DATABASE: Structured configuration model for database engine settings.
        DATABASE_URL: Connection URI for the primary relational database.
        MAX_OVERFLOW: Maximum number of connections allowed beyond the database pool size.
        POOL_SIZE: The connection pool size for database connections.
        DATABASE_TEST_URL: Connection URI for database testing and integration runs.
        TIMEZONE: IANA standard timezone string used across the application.
        AUTO_CONVERT_TIMEZONE: Boolean flag determining automatic API timezone conversions.
        SECRET_KEY: Cryptographic secret key used for signing web tokens and hashes.
        PROJECT_NAME: Name of the project.
        ALGORITHM: Cryptographic algorithm utilized for signing JWTs.
        ACCESS_TOKEN_EXPIRE_MINUTES: Expiry duration for authentication access tokens in minutes.
        REFRESH_TOKEN_EXPIRE_DAYS: Expiry duration for refresh tokens in days.
        STORAGE_PATH: Local filesystem base path reserved for target storage uploads.
        REDIS_URL: Redis connection URI, or None if Redis is not used.
        DEBUG: Boolean flag indicating whether the application is in debug mode.
    """

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"), extra="ignore", case_sensitive=True
    )

    DATABASE: DatabaseSettings = Field(default_factory=DatabaseSettings)
    DATABASE_URL: str = "sqlite+aiosqlite:///zcore.db"
    MAX_OVERFLOW: int = 10
    POOL_SIZE: int = 5
    DATABASE_TEST_URL: str = "sqlite+aiosqlite:///zcore_test.db"

    TIMEZONE: str = "UTC"
    AUTO_CONVERT_TIMEZONE: bool = True

    SECRET_KEY: str = "zcore-insecure-fallback-secret-key-must-be-changed"
    PROJECT_NAME: str = "ZCore Application"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    STORAGE_PATH: str = "./storage"
    REDIS_URL: str | None = None
    DEBUG: bool = True

    @model_validator(mode="after")
    def _sync_database_settings(self) -> "Settings":
        if self.DATABASE_URL != "sqlite+aiosqlite:///zcore.db" and self.DATABASE.url == "sqlite+aiosqlite:///zcore.db":
            self.DATABASE.url = self.DATABASE_URL
        elif self.DATABASE.url != "sqlite+aiosqlite:///zcore.db" and self.DATABASE_URL == "sqlite+aiosqlite:///zcore.db":
            self.DATABASE_URL = self.DATABASE.url
        if self.POOL_SIZE != 5 and self.DATABASE.pool_size == 5:
            self.DATABASE.pool_size = self.POOL_SIZE
        if self.MAX_OVERFLOW != 10 and self.DATABASE.max_overflow == 10:
            self.DATABASE.max_overflow = self.MAX_OVERFLOW
        return self


def initialize_settings(settings_inst: Settings) -> None:
    """Register the settings instance in the IoC dependency injection container.

    This function binds the instantiated settings class to the DI container. If the
    provided instance is a subclass of Settings, it registers both the specific
    subclass and the base Settings type, allowing downstream components to
    inject the base class or the custom subclass seamlessly.

    Args:
        settings_inst: An instance of `Settings` (or its subclasses)
            to register into the global container.
    """
    container.register_singleton(settings_inst.__class__, settings_inst)
    if settings_inst.__class__ is not Settings:
        container.register_singleton(Settings, settings_inst)


def get_settings(settings_class: type[T] = Settings) -> T:
    """Retrieve the settings instance from the dependency injection container.

    If the specified settings class has not yet been registered in the DI container,
    this function instantiates it, registers it as a singleton, and then returns it.

    Args:
        settings_class: The class type of the settings to resolve.
            Defaults to Settings.

    Returns:
        The resolved settings instance of type `T`.
    """
    try:
        return cast(T, container.resolve(settings_class))
    except Exception:
        settings_inst = settings_class()
        initialize_settings(settings_inst)
        return cast(T, settings_inst)


class SettingsProxy:
    """Proxy object providing lazy attribute access to the active settings instance.

    This proxy allows developers to import a global `settings` object without triggering
    premature initialization of the dependency injection container or settings configuration
    lookup during import time. Configuration lookups are dynamically resolved against the
    active registered settings instance on demand.
    """

    def __getattr__(self, name: str) -> Any:
        """Dynamically retrieve configuration values from the active settings instance.

        Args:
            name: The attribute name of the configuration option to fetch.

        Returns:
            The value associated with the specified attribute name.

        Raises:
            AttributeError: If the resolved settings instance does not contain
                the requested attribute.
        """
        return getattr(get_settings(), name)


settings = SettingsProxy()