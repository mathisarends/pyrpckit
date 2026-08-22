import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from types import UnionType
from typing import (
    Annotated,
    Any,
    TypeAliasType,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel

from pyrpckit.decorators import (
    DecoratedRpcMethod,
    decorated_methods,
    event_metadata,
)
from pyrpckit.errors import ProtocolDefinitionError, RpcError, RpcMethodNotFoundError


@dataclass(frozen=True, slots=True)
class RpcMethodDefinition:
    name: str
    handler_name: str
    request_name: str
    params: type[BaseModel]
    result: Any
    summary: str | None = None
    errors: tuple[type[RpcError], ...] = ()
    feature: str | None = None


@dataclass(frozen=True, slots=True)
class RpcNotificationDefinition:
    name: str
    payload: Any
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class RpcEventDefinition:
    name: str
    payload: type[BaseModel]


@dataclass(frozen=True, slots=True)
class RpcFeatureDefinition:
    name: str | None
    handlers: tuple[type, ...]
    methods: tuple[RpcMethodDefinition, ...] = ()
    notifications: tuple[RpcNotificationDefinition, ...] = ()
    events: tuple[RpcEventDefinition, ...] = ()


class RpcProtocol:
    """The complete, validated API surface assembled from its features."""

    def __init__(
        self,
        *features: RpcFeatureDefinition,
        version: int = 1,
    ) -> None:
        self._version = version
        self._features = features
        self._methods = _unique(
            "method",
            ((method.name, method) for method in _all_methods(self._features)),
        )
        self._notifications = _unique(
            "notification",
            (
                (notification.name, notification)
                for notification in _all_notifications(self._features)
            ),
        )
        self._events = _unique(
            "event",
            ((event.name, event) for event in _all_events(self._features)),
        )

    @classmethod
    def of(cls, *handlers: type, version: int = 1) -> "RpcProtocol":
        """Assemble a protocol straight from handler classes, without features."""
        return cls(_feature_definition(None, handlers, ()), version=version)

    @property
    def version(self) -> int:
        return self._version

    @property
    def features(self) -> tuple[RpcFeatureDefinition, ...]:
        return self._features

    @property
    def methods(self) -> tuple[RpcMethodDefinition, ...]:
        return tuple(self._methods.values())

    @property
    def notifications(self) -> tuple[RpcNotificationDefinition, ...]:
        return tuple(self._notifications.values())

    @property
    def events(self) -> tuple[RpcEventDefinition, ...]:
        return tuple(self._events.values())

    def method(self, name: str) -> RpcMethodDefinition:
        try:
            return self._methods[name]
        except KeyError as error:
            raise RpcMethodNotFoundError(name) from error


def feature(
    name: str,
    *,
    handlers: Iterable[type] = (),
    notifications: Iterable[RpcNotificationDefinition] = (),
) -> RpcFeatureDefinition:
    """Describe one feature of the API from its handler classes.

    The name groups the methods of that feature in the generated contract, where
    it becomes an OpenRPC tag.
    """
    return _feature_definition(str(name), tuple(handlers), tuple(notifications))


def notification(
    name: str,
    payload: Any,
    *,
    summary: str | None = None,
) -> RpcNotificationDefinition:
    """Describe a server-initiated message carrying one event or a union of them."""
    return RpcNotificationDefinition(name=str(name), payload=payload, summary=summary)


def _feature_definition(
    name: str | None,
    handlers: tuple[type, ...],
    notifications: tuple[RpcNotificationDefinition, ...],
) -> RpcFeatureDefinition:
    methods = tuple(
        _method_definition(decorated, name)
        for handler in handlers
        for decorated in _handler_methods(handler)
    )
    events = tuple(
        event
        for notification in notifications
        for event in _event_definitions(notification.payload)
    )
    return RpcFeatureDefinition(
        name=name,
        handlers=handlers,
        methods=methods,
        notifications=notifications,
        events=events,
    )


def _handler_methods(handler: type) -> tuple[DecoratedRpcMethod, ...]:
    if not isinstance(handler, type):
        raise ProtocolDefinitionError(f"RPC handler must be a class, got {handler!r}")
    methods = tuple(decorated_methods(handler))
    if not methods:
        raise ProtocolDefinitionError(
            f"RPC handler {handler.__name__} declares no @method"
        )
    return methods


def _method_definition(
    decorated: DecoratedRpcMethod,
    feature_name: str | None,
) -> RpcMethodDefinition:
    function = decorated.function
    metadata = decorated.metadata
    params = _params_model(function)
    result = _result_model(function)
    return RpcMethodDefinition(
        name=metadata.name,
        handler_name=decorated.attribute_name,
        request_name=f"{_pascal_case(decorated.attribute_name)}Request",
        params=params,
        result=result,
        summary=metadata.summary,
        errors=metadata.errors,
        feature=feature_name,
    )


def _params_model(function: Any) -> type[BaseModel]:
    parameters = tuple(inspect.signature(function).parameters.values())
    if len(parameters) != 2 or parameters[0].name != "self":
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} must accept only self and params"
        )
    params = get_type_hints(function).get(parameters[1].name)
    if not _is_model(params):
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} params must be a Pydantic model"
        )
    return params


def _result_model(function: Any) -> Any:
    result = get_type_hints(function).get("return")
    if result is None:
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} needs a return annotation"
        )
    if result is not type(None) and not _is_model(result):
        raise ProtocolDefinitionError(
            f"RPC handler {function.__qualname__} result must be "
            "a Pydantic model or None"
        )
    return result


def _event_definitions(annotation: Any) -> tuple[RpcEventDefinition, ...]:
    definitions: list[RpcEventDefinition] = []
    for message in _event_message_types(annotation):
        metadata = event_metadata(message)
        if metadata is None:
            raise ProtocolDefinitionError(
                f"RPC event is not decorated: {message.__name__}"
            )
        definitions.append(RpcEventDefinition(metadata.name, message))
    return tuple(definitions)


def _event_message_types(annotation: Any) -> tuple[type[BaseModel], ...]:
    value = (
        annotation.__value__ if isinstance(annotation, TypeAliasType) else annotation
    )
    if get_origin(value) is Annotated:
        value = get_args(value)[0]
    members = get_args(value) if get_origin(value) is UnionType else (value,)
    if not all(_is_model(member) for member in members):
        raise ProtocolDefinitionError(
            "RPC notification payload must contain Pydantic event models"
        )
    return members


def _all_methods(
    features: tuple[RpcFeatureDefinition, ...],
) -> Iterable[RpcMethodDefinition]:
    return (method for feature in features for method in feature.methods)


def _all_notifications(
    features: tuple[RpcFeatureDefinition, ...],
) -> Iterable[RpcNotificationDefinition]:
    return (
        notification for feature in features for notification in feature.notifications
    )


def _all_events(
    features: tuple[RpcFeatureDefinition, ...],
) -> Iterable[RpcEventDefinition]:
    return (event for feature in features for event in feature.events)


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
