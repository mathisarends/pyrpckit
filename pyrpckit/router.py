import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from pyrpckit.dependencies import RpcResolverScope, call_scope
from pyrpckit.errors import ProtocolDefinitionError, RpcError, declared_error
from pyrpckit.protocol import RpcNotificationDefinition, notification_definition


@dataclass(frozen=True, slots=True)
class RpcRoute:
    name: str
    function: FunctionType
    summary: str | None
    errors: tuple[type[RpcError], ...]
    tags: tuple[str, ...]
    resolver_scope: RpcResolverScope


def _docstring_summary(handler: Any) -> str | None:
    docstring = inspect.getdoc(handler)
    if not docstring:
        return None
    return docstring.splitlines()[0].strip() or None


class RpcModule:
    """Collect endpoint-independent RPC operations for optional reuse."""

    def __init__(
        self,
        *,
        namespace: str = "",
        tags: Iterable[str] = (),
        resolver_scope: RpcResolverScope = call_scope,
    ) -> None:
        if not callable(resolver_scope):
            raise ProtocolDefinitionError("RPC resolver scope must be callable")
        self._namespace = normalize_namespace(namespace)
        self._tags = normalize_tags(tags)
        self._resolver_scope = resolver_scope
        self._routes: list[RpcRoute] = []
        self._events: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def tags(self) -> tuple[str, ...]:
        return self._tags

    @property
    def resolver_scope(self) -> RpcResolverScope:
        return self._resolver_scope

    @property
    def routes(self) -> tuple[RpcRoute, ...]:
        return tuple(self._routes)

    @property
    def events(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._events)

    def method(
        self,
        name: str | None = None,
        *,
        summary: str | None = None,
        errors: Iterable[type[RpcError]] = (),
    ) -> Callable[[FunctionType], FunctionType]:
        """Declare an async free function as an RPC method."""
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC methods must use @module.method() with parentheses"
            )
        explicit_name = None if name is None else _name(name)
        declared_errors = tuple(declared_error(error) for error in errors)

        def decorate(function: FunctionType) -> FunctionType:
            if not isinstance(function, FunctionType):
                raise ProtocolDefinitionError(
                    f"RPC method must decorate a function, got {function!r}"
                )
            if not inspect.iscoroutinefunction(function):
                raise ProtocolDefinitionError(
                    f"RPC method {function.__qualname__} must be async"
                )
            parameters = inspect.signature(function).parameters
            if (
                not _is_free_function(function)
                or "self" in parameters
                or "cls" in parameters
            ):
                raise ProtocolDefinitionError(
                    f"RPC method {function.__qualname__} must be a free function"
                )
            method_name = function.__name__ if explicit_name is None else explicit_name
            full_name = join_rpc_name(self._namespace, _name(method_name))
            self._reserve(full_name)
            self._routes.append(
                RpcRoute(
                    name=full_name,
                    function=function,
                    summary=(
                        summary if summary is not None else _docstring_summary(function)
                    ),
                    errors=declared_errors,
                    tags=self._tags,
                    resolver_scope=self._resolver_scope,
                )
            )
            return function

        return decorate

    def event(
        self,
        name: str,
        *,
        payload: Any,
        summary: str | None = None,
    ) -> Callable[[FunctionType], FunctionType]:
        """Declare an async source for a server-pushed event."""
        full_name = join_rpc_name(self._namespace, _name(name))

        def decorate(function: FunctionType) -> FunctionType:
            if not isinstance(function, FunctionType) or not _is_free_function(
                function
            ):
                raise ProtocolDefinitionError(
                    f"RPC event source {function!r} must be a free function"
                )
            definition = notification_definition(
                name=full_name,
                payload=payload,
                function=function,
                summary=(
                    summary if summary is not None else _docstring_summary(function)
                ),
                tags=self._tags,
                server=None,
            )
            self._reserve(full_name)
            self._events.append(definition)
            return function

        return decorate

    def _reserve(self, name: str) -> None:
        if name in self._names:
            raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
        self._names.add(name)


def join_rpc_name(*parts: str) -> str:
    return ".".join(part for part in parts if part)


def _is_free_function(function: FunctionType) -> bool:
    local_name = function.__qualname__.rsplit("<locals>.", 1)[-1]
    return "." not in local_name


def normalize_namespace(value: object) -> str:
    namespace = str(value)
    if namespace:
        _validate_dotted_name(namespace, kind="namespace")
    return namespace


def _name(value: object) -> str:
    name = str(value)
    _validate_dotted_name(name, kind="name")
    return name


def _validate_dotted_name(value: str, *, kind: str) -> None:
    if not value or any(not segment for segment in value.split(".")):
        raise ProtocolDefinitionError(f"Invalid RPC {kind}: {value!r}")


def normalize_tags(values: Iterable[str]) -> tuple[str, ...]:
    unique: dict[str, None] = {}
    for value in values:
        tag = str(value)
        if not tag:
            raise ProtocolDefinitionError("RPC tags cannot be empty")
        unique.setdefault(tag, None)
    return tuple(unique)
