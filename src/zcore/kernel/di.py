"""Inversion of Control (IoC) and Dependency Injection (DI) Container.

This module implements custom Singleton, Scoped, and Transient injection strategies.
It resolves types dynamically using constructor reflection, utilizing signature caching
to mitigate reflection runtime performance overhead, implements protective mechanisms
against cyclic dependencies, and coordinates isolated background task execution scopes.
"""

import functools
import inspect
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import (
    Annotated,
    Any,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

import structlog
from anyio import to_thread
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()
T = TypeVar("T")

_current_scope_id: ContextVar[str | None] = ContextVar("scope_id", default=None)
_scoped_instances: ContextVar[dict[type[Any], Any]] = ContextVar(
    "scoped_instances", default={}
)


class DIException(Exception):
    """Base exception for Dependency Injection errors in ZCore."""

    pass


class CircularDependencyError(DIException):
    """Raised when a circular dependency loop is detected during resolution."""

    pass


class IoCContainer:
    """Central Inversion of Control (IoC) container for dependency management.

    Handles registration and dynamic resolution of classes. Supports transient,
    scoped, and singleton lifecycles. Employs caching on inspected constructor signatures
    to manage resolution overhead.

    Attributes:
        _singletons: In-memory store mapping interfaces to shared singleton instances.
        _scoped_definitions: Mappings of interfaces to factory functions bound to request contexts.
        _factories: Mappings of interfaces to factory functions for transient lifecycles.
        _constructor_cache: Cache storing raw init constructors of target classes.
        _dependency_signature_cache: Cache storing evaluated constructor dependencies of targets.
    """

    def __init__(self) -> None:
        """Initialize the IoCContainer with clean caches and registries."""
        self._singletons: dict[type[Any], Any] = {}
        self._scoped_definitions: dict[type[Any], Callable[..., Any]] = {}
        self._factories: dict[type[Any], Callable[..., Any]] = {}

        self._constructor_cache: dict[type[Any], Callable[..., Any] | None] = {}
        self._dependency_signature_cache: dict[type[Any], list[type[Any]]] = {}

    def register_singleton(self, interface: type[Any], instance: Any) -> None:
        """Register a pre-constructed instance as a global singleton.

        Args:
            interface: The interface type key to map against.
            instance: The constructed object instance to share.
        """
        self._singletons[interface] = instance

    def register_scoped(self, interface: type[Any], implementation: type[Any]) -> None:
        """Register a class bound to a context-scoped lifecycle.

        Scoped classes are resolved once per context-scope lifetime and shared across
        dependency graphs within that context execution boundary.

        Args:
            interface: The interface or class type key to map against.
            implementation: The target implementation class to instantiate.
        """
        self._scoped_definitions[interface] = lambda stack=None: self._auto_wire(
            implementation, stack
        )

    def register_scoped_instance(self, interface: type[Any], instance: Any) -> None:
        """Register a pre-constructed instance directly into the active request scope.

        Args:
            interface: The interface or class type to map against.
            instance: The active object instance to bind.

        Raises:
            DIException: If registered outside of an active scope boundary.
        """
        scope_id = _current_scope_id.get()
        if scope_id:
            current_instances = _scoped_instances.get()
            new_instances = dict(current_instances)
            new_instances[interface] = instance
            _scoped_instances.set(new_instances)
        else:
            raise DIException(
                "Cannot register scoped instance outside of an active scope."
            )

    def register_transient(
        self, interface: type[Any], implementation: type[Any]
    ) -> None:
        """Register a class bound to a transient lifecycle.

        Transient classes are constructed as a new instance on every resolution request.

        Args:
            interface: The interface or class type key to map against.
            implementation: The target implementation class to instantiate.
        """
        self._factories[interface] = lambda stack=None: self._auto_wire(
            implementation, stack
        )

    def resolve(self, interface: type[T], _stack: set[type[Any]] | None = None) -> T:
        """Resolve a specific interface or type dependency.

        Dynamically evaluates registered bindings (Singleton, Scoped, Transient) or
        attempts fallback auto-wiring to assemble the target graph.

        Args:
            interface: The interface or class type to resolve.
            _stack: Internal recursion validation stack representing parent classes active
                in the resolution tree. Defaults to None.

        Returns:
            The fully constructed instance of type `T`.

        Raises:
            CircularDependencyError: If a cyclic loop is detected during dependency tree assembly.
        """
        if interface in self._singletons:
            return self._singletons[interface]

        scope_id = _current_scope_id.get()
        if scope_id:
            current_instances = _scoped_instances.get()
            if interface in current_instances:
                return current_instances[interface]

            if interface in self._scoped_definitions:
                resolved_instance = self._scoped_definitions[interface](_stack)
                new_instances = dict(current_instances)
                new_instances[interface] = resolved_instance
                _scoped_instances.set(new_instances)
                return resolved_instance

        if interface in self._factories:
            return self._factories[interface](_stack)

        return self._auto_wire(interface, _stack)

    def _auto_wire(
        self, target_class: type[T], _stack: set[type[Any]] | None = None
    ) -> T:
        """Analyze, resolve parameters, and construct a class instance.

        Leverages constructor cache values and metadata reflection to construct targets.
        Utilizes `typing.get_type_hints` to resolve forward-references.

        Args:
            target_class: The concrete target class type to auto-wire.
            _stack: Recursion validation stack indicating target registration parents.
                Defaults to None.

        Returns:
            The constructed object instance of type `T`.

        Raises:
            CircularDependencyError: If target_class is already present in the active resolution stack.
        """
        if not inspect.isclass(target_class):
            return target_class

        _stack = _stack or set()
        if target_class in _stack:
            chain = (
                " -> ".join([c.__name__ for c in _stack])
                + f" -> {target_class.__name__}"
            )
            raise CircularDependencyError(f"Circular dependency detected: {chain}")

        _stack.add(target_class)

        try:
            if target_class in self._dependency_signature_cache:
                dependencies = self._dependency_signature_cache[target_class]
                resolved_args = [
                    self.resolve(dep, _stack.copy()) for dep in dependencies
                ]
                return target_class(*resolved_args)

            if target_class not in self._constructor_cache:
                constructor = getattr(target_class, "__init__", None)
                self._constructor_cache[target_class] = constructor
            else:
                constructor = self._constructor_cache[target_class]

            if constructor is None or constructor is object.__init__:
                self._dependency_signature_cache[target_class] = []
                return target_class()

            try:
                type_hints = get_type_hints(constructor)
            except Exception:
                type_hints = {}

            sig = inspect.signature(constructor)
            dependencies = []

            for name, param in sig.parameters.items():
                if name == "self":
                    continue
                annotation = type_hints.get(name, param.annotation)
                if annotation is inspect.Parameter.empty:
                    continue

                if get_origin(annotation) is Annotated:
                    annotation = get_args(annotation)[0]

                dependencies.append(annotation)

            self._dependency_signature_cache[target_class] = dependencies

            resolved_args = [self.resolve(dep, _stack.copy()) for dep in dependencies]
            return target_class(*resolved_args)

        finally:
            _stack.remove(target_class)

    def clear_scope(self, scope_id: str) -> None:
        """Explicit scope cleanup hook.

        Args:
            scope_id: The string identifier of the scope to purge.
        """
        _scoped_instances.set({})


container = IoCContainer()


class Injector:
    """Helper class that acts as a callable resolver wrapper.

    Integrates standard container resolution lookups with FastAPI's routing dependency
    injection structure.
    """

    def __init__(self, interface: type[Any]):
        """Initialize the Injector instance.

        Args:
            interface: The dependency interface class type to resolve on call.
        """
        self.interface = interface

    async def __call__(self) -> Any:
        """Resolve and return the configured interface class.

        Returns:
            The resolved dependency instance.
        """
        return container.resolve(self.interface)


class Inject:
    """Dynamic type marker supporting unified Annotated dependency injection.

    Allows type annotations in FastAPI routers, e.g., `service: Inject[UserService]`.
    """

    def __class_getitem__(cls, interface: type[T]) -> Any:
        """Map generic bracket access to an Annotated dependency representation.

        Args:
            interface: The target type interface dependency to resolve.

        Returns:
            An Annotated type wrapper containing the resolved target class.
        """
        return Annotated[interface, Depends(Injector(interface))]


@asynccontextmanager
async def background_scope(
    inherit_context: bool = True,
    **custom_context: Any,
) -> AsyncGenerator[None, None]:
    """Provide an isolated IoC, Database Session, and ZContext scope for background tasks.

    Ensures that asynchronous background routines execute within an independent transaction
    boundary and IoC scope without relying on completed HTTP request lifecycles.
    Automatically manages database session allocation, context variable isolation, and guaranteed
    resource disposal upon exit.

    Args:
        inherit_context: If True, clones active request context parameters into the background scope.
        **custom_context: Explicit key-value pairs to set or override in the background context store.

    Yields:
        None within an active, isolated execution scope.
    """
    from zcore.context.context import _request_context_store
    from zcore.db.setup import db_manager

    scope_id = str(uuid.uuid4())
    scope_token = _current_scope_id.set(scope_id)

    initial_store = dict(_request_context_store.get()) if inherit_context else {}
    initial_store.update(custom_context)
    ctx_token = _request_context_store.set(initial_store)

    try:
        async with db_manager.session() as session:
            container.register_scoped_instance(AsyncSession, session)
            yield
    finally:
        container.clear_scope(scope_id)
        _current_scope_id.reset(scope_token)
        _request_context_store.reset(ctx_token)


def background_task(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps a background task with an isolated scope and auto-resolves dependencies.

    Inspects the wrapped function's signature and automatically resolves any unprovided,
    type-annotated parameters directly from the global IoC container within a dedicated background scope.
    Supports both asynchronous coroutines and standard synchronous functions.

    Args:
        func: The target synchronous or asynchronous callable to execute in the background.

    Returns:
        An asynchronous wrapped callable suitable for scheduling with FastAPI BackgroundTasks.
    """
    is_coroutine = inspect.iscoroutinefunction(func)

    def _warn_if_closed_session_passed(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        for arg in args:
            if isinstance(arg, AsyncSession):
                logger.warning(
                    "Passing an existing AsyncSession directly to a background task is discouraged. "
                    "Background tasks should allow @background_task to inject an isolated session automatically."
                )
        for k, v in kwargs.items():
            if isinstance(v, AsyncSession):
                logger.warning(
                    f"Passing an existing AsyncSession directly via argument '{k}' to a background task is discouraged. "
                    "Background tasks should allow @background_task to inject an isolated session automatically."
                )

    def _resolve_injections(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        sig = inspect.signature(func)
        bound_args = sig.bind_partial(*args, **kwargs)
        resolved_kwargs = dict(kwargs)

        for param_name, param in sig.parameters.items():
            if param_name in bound_args.arguments:
                continue
            if param.default is not inspect.Parameter.empty:
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.annotation is not inspect.Parameter.empty:
                annotation = param.annotation
                if get_origin(annotation) is Annotated:
                    annotation = get_args(annotation)[0]
                resolved_kwargs[param_name] = container.resolve(annotation)

        return resolved_kwargs

    if is_coroutine:

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _warn_if_closed_session_passed(args, kwargs)
            async with background_scope():
                final_kwargs = _resolve_injections(args, kwargs)
                return await func(*args, **final_kwargs)

        return async_wrapper
    else:

        @functools.wraps(func)
        async def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _warn_if_closed_session_passed(args, kwargs)
            async with background_scope():
                final_kwargs = _resolve_injections(args, kwargs)
                return await to_thread.run_sync(
                    functools.partial(func, *args, **final_kwargs)
                )

        return sync_wrapper