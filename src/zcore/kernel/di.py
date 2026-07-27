"""Inversion of Control (IoC) and Dependency Injection (DI) Container.

This module implements custom Singleton, Scoped, and Transient injection strategies.
It resolves types dynamically using constructor reflection, utilizing signature caching 
to mitigate reflection runtime performance overhead, and implements protective mechanisms 
against cyclic dependencies.
"""

import inspect
from contextvars import ContextVar
from typing import Any, Callable, Dict, Type, TypeVar, Optional, Set, List, get_type_hints, get_origin, get_args, Annotated
from fastapi import Depends

T = TypeVar("T")

# Safe context storage for active scoped instances
_current_scope_id: ContextVar[Optional[str]] = ContextVar("scope_id", default=None)
_scoped_instances: ContextVar[Dict[Type[Any], Any]] = ContextVar("scoped_instances", default={})


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
        _dependency_signature_cache: Cache storing evaluated constructor dependencies 
            of targets.
    """

    def __init__(self) -> None:
        """Initialize the IoCContainer with clean caches and registries."""
        self._singletons: Dict[Type[Any], Any] = {}
        self._scoped_definitions: Dict[Type[Any], Callable[..., Any]] = {}
        self._factories: Dict[Type[Any], Callable[..., Any]] = {}
        
        # High-performance caching for resolved type hints and constructors
        self._constructor_cache: Dict[Type[Any], Optional[Callable[..., Any]]] = {}
        self._dependency_signature_cache: Dict[Type[Any], List[Type[Any]]] = {}

    def register_singleton(self, interface: Type[Any], instance: Any) -> None:
        """Register a pre-constructed instance as a global singleton.

        Args:
            interface: The interface type key to map against.
            instance: The constructed object instance to share.
        """
        self._singletons[interface] = instance

    def register_scoped(self, interface: Type[Any], implementation: Type[Any]) -> None:
        """Register a class bound to a context-scoped lifecycle.

        Scoped classes are resolved once per context-scope lifetime and shared across
        dependency graphs within that context execution boundary.

        Args:
            interface: The interface or class type key to map against.
            implementation: The target implementation class to instantiate.
        """
        self._scoped_definitions[interface] = lambda stack=None: self._auto_wire(implementation, stack)

    def register_scoped_instance(self, interface: Type[Any], instance: Any) -> None:
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
            raise DIException("Cannot register scoped instance outside of an active scope.")

    def register_transient(self, interface: Type[Any], implementation: Type[Any]) -> None:
        """Register a class bound to a transient lifecycle.

        Transient classes are constructed as a new instance on every resolution request.

        Args:
            interface: The interface or class type key to map against.
            implementation: The target implementation class to instantiate.
        """
        self._factories[interface] = lambda stack=None: self._auto_wire(implementation, stack)

    def resolve(self, interface: Type[T], _stack: Optional[Set[Type[Any]]] = None) -> T:
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

    def _auto_wire(self, target_class: Type[T], _stack: Optional[Set[Type[Any]]] = None) -> T:
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

        # Initialize recursion verification stack
        _stack = _stack or set()
        if target_class in _stack:
            chain = " -> ".join([c.__name__ for c in _stack]) + f" -> {target_class.__name__}"
            raise CircularDependencyError(f"Circular dependency detected: {chain}")

        _stack.add(target_class)

        try:
            # High-Performance Signature Caching Lookup
            if target_class in self._dependency_signature_cache:
                dependencies = self._dependency_signature_cache[target_class]
                resolved_args = [self.resolve(dep, _stack.copy()) for dep in dependencies]
                return target_class(*resolved_args)

            # Retrieve constructor safely
            if target_class not in self._constructor_cache:
                constructor = getattr(target_class, "__init__", None)
                self._constructor_cache[target_class] = constructor
            else:
                constructor = self._constructor_cache[target_class]

            if constructor is None or constructor is object.__init__:
                self._dependency_signature_cache[target_class] = []
                return target_class()

            # Using typing.get_type_hints to resolve forward references
            try:
                type_hints = get_type_hints(constructor)
            except Exception:
                type_hints = {}

            sig = inspect.signature(constructor)
            dependencies = []

            for name, param in sig.parameters.items():
                if name == "self":
                    continue
                # Resolve the annotation type, checking get_type_hints first for forward references
                annotation = type_hints.get(name, param.annotation)
                if annotation is inspect.Parameter.empty:
                    continue
                
                if get_origin(annotation) is Annotated:
                    annotation = get_args(annotation)[0]
                    
                dependencies.append(annotation)

            # Warm cache for subsequent requests (reduces reflection overhead)
            self._dependency_signature_cache[target_class] = dependencies

            resolved_args = [self.resolve(dep, _stack.copy()) for dep in dependencies]
            return target_class(*resolved_args)
            
        finally:
            _stack.remove(target_class)

    def clear_scope(self, scope_id: str) -> None:
        """Explicit scope cleanup hook.

        Maintains structural compatibility with scoped lifecycle triggers.

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

    def __init__(self, interface: Type[Any]):
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

    Allows elegant type annotations in FastAPI routers, e.g., `service: Inject[UserService]`.
    Under the hood, this converts the parameter metadata using `Annotated` and 
    binds it to the IoC container's resolver.
    """

    def __class_getitem__(cls, interface: Type[T]) -> Any:
        """Map the class generic bracket access to an Annotated dependency representation.

        Args:
            interface: The target type interface dependency to resolve.

        Returns:
            An Annotated type wrapper containing the resolved target class.
        """
        return Annotated[interface, Depends(Injector(interface))]