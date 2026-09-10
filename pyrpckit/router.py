import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from pyrpckit.dependencies import RpcScope, call_scope
from pyrpckit.errors import ProtocolDefinitionError, RpcError, declared_error
from pyrpckit.protocol import RpcNotificationDefinition, notification_definition


@dataclass(frozen=True, slots=True)
class RpcRoute:
    name: str
    function: FunctionType
    summary: str | None
    errors: tuple[type[RpcError], ...]
    tags: tuple[str, ...]
    server: str | None
    scope: RpcScope


def _docstring_summary(handler: Any) -> str | None:
    docstring = inspect.getdoc(handler)
    if not docstring:
        return None
    return docstring.splitlines()[0].strip() or None


class RpcRouter:
    """Collect free RPC functions and notifications in one namespace."""

    def __init__(
        self,
        *,
        namespace: str = "",
        tags: Iterable[str] = (),
        server: str | None = None,
        scope: RpcScope = call_scope,
    ) -> None:
        if not callable(scope):
            raise ProtocolDefinitionError("RPC router scope must be callable")
        self._namespace = normalize_namespace(namespace)
        self._tags = normalize_tags(tags)
        self._server = normalize_server(server)
        self._scope = scope
        self._routes: list[RpcRoute] = []
        self._notifications: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def tags(self) -> tuple[str, ...]:
        return self._tags

    @property
    def server(self) -> str | None:
        return self._server

    @property
    def scope(self) -> RpcScope:
        return self._scope

    @property
    def routes(self) -> tuple[RpcRoute, ...]:
        return tuple(self._routes)

    @property
    def notifications(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._notifications)

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
                "RPC methods must use @router.method() with parentheses"
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
                    server=self._server,
                    scope=self._scope,
                )
            )
            return function

        return decorate

    def notification(
        self,
        name: str,
        *,
        payload: Any,
        summary: str | None = None,
    ) -> Callable[[FunctionType], FunctionType]:
        """Declare an async notification source."""
        full_name = join_rpc_name(self._namespace, _name(name))

        def decorate(function: FunctionType) -> FunctionType:
            if not isinstance(function, FunctionType) or not _is_free_function(
                function
            ):
                raise ProtocolDefinitionError(
                    f"RPC notification source {function!r} must be a free function"
                )
            definition = notification_definition(
                name=full_name,
                payload=payload,
                function=function,
                summary=(
                    summary if summary is not None else _docstring_summary(function)
                ),
                tags=self._tags,
                server=self._server,
            )
            self._reserve(full_name)
            self._notifications.append(definition)
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


def normalize_server(value: object | None) -> str | None:
    if value is None:
        return None
    server = str(value)
    if not server:
        raise ProtocolDefinitionError("RPC router server cannot be empty")
    return server
