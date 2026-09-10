import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from types import FunctionType, UnionType
from typing import (
    Annotated,
    Any,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel

from pyrpckit.dependencies import RpcInjectedParameter, RpcScope, injected_parameter
from pyrpckit.errors import ProtocolDefinitionError, RpcError, RpcMethodNotFoundError
from pyrpckit.wire import wire_annotation


@dataclass(frozen=True, slots=True)
class RpcMethodDefinition:
    name: str
    handler_name: str
    request_name: str
    params: type[BaseModel] | None
    result: Any
    summary: str | None = None
    errors: tuple[type[RpcError], ...] = ()
    tags: tuple[str, ...] = ()
    server: str | None = None
    function: FunctionType | None = None
    injected_parameters: tuple[RpcInjectedParameter, ...] = ()
    scope: RpcScope | None = None
    params_parameter: str | None = None


@dataclass(frozen=True, slots=True)
class RpcNotificationDefinition:
    name: str
    payload: Any
    summary: str | None = None
    tags: tuple[str, ...] = ()
    server: str | None = None


@dataclass(frozen=True, slots=True)
class RpcNotificationTypeDefinition:
    name: str
    payload: type[BaseModel]


class RpcProtocol:
    """The immutable protocol materialized by an ``RpcApp``."""

    def __init__(
        self,
        *,
        methods: Iterable[RpcMethodDefinition] = (),
        notifications: Iterable[RpcNotificationDefinition] = (),
        notification_types: Iterable[RpcNotificationTypeDefinition] = (),
        version: int = 1,
    ) -> None:
        self._version = version
        self._methods = _unique(
            "method",
            ((method.name, method) for method in methods),
        )
        self._notifications = _unique(
            "notification",
            ((notification.name, notification) for notification in notifications),
        )
        self._notification_types = _unique(
            "notification type",
            ((item.name, item) for item in notification_types),
        )

    @property
    def version(self) -> int:
        return self._version

    @property
    def methods(self) -> tuple[RpcMethodDefinition, ...]:
        return tuple(self._methods.values())

    @property
    def notifications(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._notifications.values())

    @property
    def notification_types(self) -> tuple[RpcNotificationTypeDefinition, ...]:
        return tuple(self._notification_types.values())

    def method(self, name: str) -> RpcMethodDefinition:
        try:
            return self._methods[name]
        except KeyError as error:
            raise RpcMethodNotFoundError(name) from error


def method_definition(
    *,
    name: str,
    function: FunctionType,
    handler_name: str,
    summary: str | None,
    errors: tuple[type[RpcError], ...],
    tags: tuple[str, ...],
    server: str | None,
    scope: RpcScope,
    request_name: str | None = None,
) -> RpcMethodDefinition:
    request_name = request_name or f"{_pascal_case(handler_name)}Request"
    params, params_parameter, injected = _router_params_model(function)
    return RpcMethodDefinition(
        name=name,
        handler_name=handler_name,
        request_name=request_name,
        params=params,
        result=wire_annotation(_result_annotation(function)),
        summary=summary,
        errors=errors,
        tags=tags,
        server=server,
        function=function,
        injected_parameters=injected,
        scope=scope,
        params_parameter=params_parameter,
    )


def _router_params_model(
    function: Any,
) -> tuple[
    type[BaseModel] | None,
    str | None,
    tuple[RpcInjectedParameter, ...],
]:
    parameters = tuple(inspect.signature(function).parameters.values())
    hints = get_type_hints(function, include_extras=True)
    injected: list[RpcInjectedParameter] = []
    wire_parameters: list[inspect.Parameter] = []
    for parameter in parameters:
        annotation = hints.get(parameter.name)
        if annotation is None:
            raise ProtocolDefinitionError(
                f"RPC handler {function.__qualname__} parameter "
                f"{parameter.name!r} needs an annotation"
            )
        try:
            dependency = injected_parameter(parameter.name, annotation)
        except TypeError as error:
            raise ProtocolDefinitionError(str(error)) from error
        if dependency is None:
            wire_parameters.append(parameter)
            continue
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ProtocolDefinitionError(
                f"Injected RPC parameter {parameter.name!r} must be passable by name"
            )
        if parameter.default is not inspect.Parameter.empty:
            raise ProtocolDefinitionError(
                f"Injected RPC parameter {parameter.name!r} cannot have a default"
            )
        injected.append(dependency)

    if not wire_parameters:
        return None, None, tuple(injected)

    if len(wire_parameters) == 1:
        parameter = wire_parameters[0]
        annotation = hints[parameter.name]
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,) and _is_model(
            annotation
        ):
            return (
                wire_annotation(annotation),
                parameter.name,
                tuple(injected),
            )

    names = ", ".join(parameter.name for parameter in wire_parameters)
    raise ProtocolDefinitionError(
        f"RPC handler {function.__qualname__} must use at most one positional "
        f"Pydantic params model; unsupported wire parameters: {names}"
    )


def _result_annotation(function: Any) -> Any:
    result = get_type_hints(function, include_extras=True).get("return")
    if result is None:
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} needs a return annotation"
        )
    return result


def notification_type_definitions(
    annotation: Any,
) -> tuple[RpcNotificationTypeDefinition, ...]:
    definitions: list[RpcNotificationTypeDefinition] = []
    messages = _notification_message_types(annotation)
    for message in messages:
        name = _declared_notification_type(message)
        if not isinstance(name, str):
            if len(messages) == 1:
                continue
            raise ProtocolDefinitionError(
                f"RPC notification type {message.__name__} needs a type field "
                "pinned to a string literal when used in a union"
            )
        definitions.append(RpcNotificationTypeDefinition(name, message))
    return tuple(definitions)


def _notification_message_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    value = (
        annotation.__value__ if isinstance(annotation, TypeAliasType) else annotation
    )
    if get_origin(value) is Annotated:
        value = get_args(value)[0]
    members = get_args(value) if get_origin(value) in (Union, UnionType) else (value,)
    if not all(_is_model(member) for member in members):
        raise ProtocolDefinitionError(
            "RPC notification payload must contain Pydantic models"
        )
    return members


def _declared_notification_type(message: type[BaseModel]) -> Any:
    schema = message.model_json_schema()
    return schema.get("properties", {}).get("type", {}).get("const")


def _unique[ValueT](
    kind: str,
    entries: Iterable[tuple[str, ValueT]],
) -> dict[str, ValueT]:
    unique: dict[str, ValueT] = {}
    for name, value in entries:
        if name in unique:
            raise ProtocolDefinitionError(f"Duplicate RPC {kind}: {name}")
        unique[name] = value
    return unique


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))
