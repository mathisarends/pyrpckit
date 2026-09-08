import functools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import FunctionType
from typing import Any, overload

from pyrpckit.decorators import _docstring_summary
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
    binding: _BindingReference


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
    """Collect RPC methods and events near the feature that declares them."""

    def __init__(
        self,
        *,
        prefix: str = "",
        tags: Iterable[str] = (),
    ) -> None:
        self._prefix = normalize_prefix(prefix)
        self._tags = normalize_tags(tags)
        self._routes: list[RpcRoute] = []
        self._events: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def tags(self) -> tuple[str, ...]:
        return self._tags

    @property
    def routes(self) -> tuple[RpcRoute, ...]:
        return tuple(self._routes)

    @property
    def events(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._events)

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
            full_name = join_rpc_name(self._prefix, _name(method_name))
            route = RpcRoute(
                name=full_name,
                function=function,
                summary=(
                    summary if summary is not None else _docstring_summary(function)
                ),
                errors=declared_errors,
                tags=self._tags,
                binding=_BindingReference(),
            )
            self._reserve(full_name)
            self._routes.append(route)
            return _RouterMethod(route)

        return decorate(function) if function is not None else decorate

    def event(
        self,
        name: str,
        payload: Any,
        *,
        summary: str | None = None,
    ) -> None:
        """Declare a server-initiated JSON-RPC notification."""
        full_name = join_rpc_name(self._prefix, _name(name))
        self._reserve(full_name)
        self._events.append(
            RpcNotificationDefinition(
                name=full_name,
                payload=payload,
                summary=summary,
                tags=self._tags,
            )
        )

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


def normalize_prefix(value: object) -> str:
    prefix = str(value)
    if prefix:
        _validate_dotted_name(prefix, kind="prefix")
    return prefix


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
