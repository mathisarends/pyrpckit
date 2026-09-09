import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from types import FunctionType, UnionType
from typing import (
    Annotated,
    Any,
    Literal,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, create_model

from pyrpckit._wire import wire_annotation
from pyrpckit.errors import ProtocolDefinitionError, RpcError, RpcMethodNotFoundError
from pyrpckit.models import RpcModel


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
    owner: type[object] | None = None
    params_style: Literal["model", "kwargs"] = "model"


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
    owner: type[object] | None,
    summary: str | None,
    errors: tuple[type[RpcError], ...],
    tags: tuple[str, ...],
    server: str | None,
    request_name: str | None = None,
) -> RpcMethodDefinition:
    request_name = request_name or f"{_pascal_case(handler_name)}Request"
    params, params_style = _router_params_model(
        function,
        instance_method=owner is not None,
        model_name=f"{_pascal_case(name.replace('.', '_'))}Params",
    )
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
        owner=owner,
        params_style=params_style,
    )


def _router_params_model(
    function: Any,
    *,
    instance_method: bool,
    model_name: str,
) -> tuple[type[BaseModel] | None, Literal["model", "kwargs"]]:
    parameters = tuple(inspect.signature(function).parameters.values())
    if instance_method:
        if not parameters or parameters[0].name != "self":
            raise ProtocolDefinitionError(
                f"RPC handler {function.__qualname__} must start with self"
            )
        parameters = parameters[1:]

    if not parameters:
        return None, "model"

    hints = get_type_hints(function, include_extras=True)
    if len(parameters) == 1:
        parameter = parameters[0]
        annotation = hints.get(parameter.name)
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ) and _is_model(annotation):
            return wire_annotation(annotation), "model"

    unsupported = [
        parameter.name
        for parameter in parameters
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY
    ]
    if unsupported:
        names = ", ".join(unsupported)
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} must use one positional Pydantic "
            f"params model or keyword-only fields; unsupported: {names}"
        )

    fields: dict[str, tuple[Any, Any]] = {}
    for parameter in parameters:
        annotation = hints.get(parameter.name)
        if annotation is None:
            raise ProtocolDefinitionError(
                f"RPC handler {function.__qualname__} parameter "
                f"{parameter.name!r} needs an annotation"
            )
        default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
        )
        fields[parameter.name] = (wire_annotation(annotation), default)
    return create_model(model_name, __base__=RpcModel, **fields), "kwargs"


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
