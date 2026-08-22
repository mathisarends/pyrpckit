from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from pydantic import BaseModel

from pyrpckit.errors import ProtocolDefinitionError

_METHOD_METADATA_KEY = "__pyrpckit_method__"
_EVENT_METADATA_KEY = "__pyrpckit_event__"


class RpcHandler:
    """Base for classes whose methods are exposed through ``@method``."""


@dataclass(frozen=True, slots=True)
class RpcMethodMetadata:
    name: str
    summary: str
    errors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RpcEventMetadata:
    name: str


@dataclass(frozen=True, slots=True)
class DecoratedRpcMethod:
    attribute_name: str
    function: FunctionType
    metadata: RpcMethodMetadata


def method[HandlerT: Callable[..., Any]](
    name: str,
    *,
    summary: str,
    errors: Iterable[int] = (),
) -> Callable[[HandlerT], HandlerT]:
    """Expose a handler method under ``name`` in the RPC protocol.

    The decorated method must accept exactly ``self`` and a Pydantic params model
    and must annotate its return type with a Pydantic model or ``None``.
    """

    def decorate(handler: HandlerT) -> HandlerT:
        if _METHOD_METADATA_KEY in handler.__dict__:
            raise ProtocolDefinitionError(f"RPC handler is already decorated: {handler.__name__}")
        setattr(
            handler,
            _METHOD_METADATA_KEY,
            RpcMethodMetadata(
                name=str(name),
                summary=summary,
                errors=tuple(int(code) for code in errors),
            ),
        )
        return handler

    return decorate


def event[EventT: type[BaseModel]](name: str) -> Callable[[EventT], EventT]:
    """Register a Pydantic model as the payload of the ``name`` event.

    The model must discriminate itself with a ``type`` field pinned to ``name``,
    so that clients can narrow a union of events on the wire.
    """

    def decorate(message: EventT) -> EventT:
        if _EVENT_METADATA_KEY in message.__dict__:
            raise ProtocolDefinitionError(f"RPC event is already decorated: {message.__name__}")
        declared = _declared_event_type(message)
        if declared != str(name):
            raise ProtocolDefinitionError(
                f"RPC event {message.__name__} declares type {declared!r}, expected {str(name)!r}"
            )
        setattr(message, _EVENT_METADATA_KEY, RpcEventMetadata(name=str(name)))
        return message

    return decorate


def decorated_methods(handler: type[RpcHandler]) -> Iterator[DecoratedRpcMethod]:
    for attribute_name, attribute in vars(handler).items():
        metadata = _method_metadata(attribute)
        if metadata is not None:
            yield DecoratedRpcMethod(attribute_name, attribute, metadata)


def event_metadata(message: type[BaseModel]) -> RpcEventMetadata | None:
    metadata = message.__dict__.get(_EVENT_METADATA_KEY)
    if metadata is None:
        return None
    if not isinstance(metadata, RpcEventMetadata):
        raise ProtocolDefinitionError(f"Invalid RPC event metadata on {message.__name__}")
    return metadata


def _method_metadata(attribute: object) -> RpcMethodMetadata | None:
    if not isinstance(attribute, FunctionType):
        return None
    metadata = attribute.__dict__.get(_METHOD_METADATA_KEY)
    if metadata is None:
        return None
    if not isinstance(metadata, RpcMethodMetadata):
        raise ProtocolDefinitionError(f"Invalid RPC method metadata on {attribute.__name__}")
    return metadata


def _declared_event_type(message: type[BaseModel]) -> Any:
    schema = message.model_json_schema()
    return schema.get("properties", {}).get("type", {}).get("const")
