import functools
import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import FunctionType
from typing import Any, get_type_hints, overload

from pyrpckit.errors import ProtocolDefinitionError, RpcError, declared_error
from pyrpckit.protocol import RpcNotificationDefinition


class _BindingReference:
    def __init__(self) -> None:
        self.owner: type[object] | None = None
        self.attribute_name: str | None = None

    def set_owner(self, owner: type[object], attribute_name: str) -> None:
        if self.owner is not None and (
            self.owner is not owner or self.attribute_name != attribute_name
        ):
            raise ProtocolDefinitionError(
                "An RPC method descriptor cannot be assigned to multiple classes"
            )
        self.owner = owner
        self.attribute_name = attribute_name


@dataclass(frozen=True, slots=True)
class RpcRoute:
    name: str
    function: FunctionType
    summary: str | None
    errors: tuple[type[RpcError], ...]
    tags: tuple[str, ...]
    server: str | None
    binding: _BindingReference


def _docstring_summary(handler: Any) -> str | None:
    docstring = inspect.getdoc(handler)
    if not docstring:
        return None
    return docstring.splitlines()[0].strip() or None


class _RouterMethod:
    def __init__(self, route: RpcRoute) -> None:
        self.route = route
        self.__wrapped__ = route.function
        functools.update_wrapper(self, route.function)

    def __set_name__(self, owner: type[object], name: str) -> None:
        self.route.binding.set_owner(owner, name)

    def __get__(self, instance: object | None, owner: type[object]) -> Any:
        if instance is None:
            return self.route.function
        return self.route.function.__get__(instance, owner)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.route.function(*args, **kwargs)


class RpcRouter:
    """Collect RPC methods and notifications in one namespace."""

    def __init__(
        self,
        *,
        namespace: str = "",
        tags: Iterable[str] = (),
        server: str | None = None,
    ) -> None:
        self._namespace = normalize_namespace(namespace)
        self._tags = normalize_tags(tags)
        self._server = normalize_server(server)
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
    def routes(self) -> tuple[RpcRoute, ...]:
        return tuple(self._routes)

    @property
    def notifications(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._notifications)

    @overload
    def method(self, function: FunctionType, /) -> _RouterMethod: ...

    @overload
    def method(
        self,
        name: str | None = None,
        *,
        summary: str | None = None,
        errors: Iterable[type[RpcError]] = (),
    ) -> Callable[[FunctionType], _RouterMethod]: ...

    def method(
        self,
        name: str | FunctionType | None = None,
        *,
        summary: str | None = None,
        errors: Iterable[type[RpcError]] = (),
    ) -> Callable[[FunctionType], _RouterMethod] | _RouterMethod:
        """Declare an RPC method, inferring its wire name when omitted."""
        function = name if isinstance(name, FunctionType) else None
        explicit_name = None if function is not None or name is None else _name(name)
        declared_errors = tuple(declared_error(error) for error in errors)

        def decorate(function: FunctionType) -> _RouterMethod:
            if not isinstance(function, FunctionType):
                raise ProtocolDefinitionError(
                    f"RPC method must decorate a function, got {function!r}"
                )
            method_name = function.__name__ if explicit_name is None else explicit_name
            full_name = join_rpc_name(self._namespace, _name(method_name))
            route = RpcRoute(
                name=full_name,
                function=function,
                summary=(
                    summary if summary is not None else _docstring_summary(function)
                ),
                errors=declared_errors,
                tags=self._tags,
                server=self._server,
                binding=_BindingReference(),
            )
            self._reserve(full_name)
            self._routes.append(route)
            return _RouterMethod(route)

        return decorate(function) if function is not None else decorate

    def notification(
        self,
        name: str,
        *,
        summary: str | None = None,
    ) -> Callable[[FunctionType], FunctionType]:
        """Declare a server-initiated notification from a return annotation."""
        local_name = _name(name)

        def decorate(function: FunctionType) -> FunctionType:
            if not isinstance(function, FunctionType):
                raise ProtocolDefinitionError(
                    f"RPC notification must decorate a function, got {function!r}"
                )
            if inspect.signature(function).parameters:
                raise ProtocolDefinitionError(
                    f"RPC notification {function.__qualname__} must not take parameters"
                )
            payload = get_type_hints(function, include_extras=True).get("return")
            if payload is None:
                raise ProtocolDefinitionError(
                    f"RPC notification {function.__qualname__} needs a return "
                    "annotation"
                )
            full_name = join_rpc_name(self._namespace, local_name)
            self._reserve(full_name)
            self._notifications.append(
                RpcNotificationDefinition(
                    name=full_name,
                    payload=payload,
                    summary=(
                        summary if summary is not None else _docstring_summary(function)
                    ),
                    tags=self._tags,
                    server=self._server,
                )
            )
            return function

        return decorate

    def _reserve(self, name: str) -> None:
        if name in self._names:
            raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
        self._names.add(name)


def router_methods(owner: type[object]) -> tuple[RpcRoute, ...]:
    routes: list[RpcRoute] = []
    for base in owner.__mro__:
        for value in vars(base).values():
            if isinstance(value, _RouterMethod):
                routes.append(value.route)
    return tuple(routes)


def join_rpc_name(*parts: str) -> str:
    return ".".join(part for part in parts if part)


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
