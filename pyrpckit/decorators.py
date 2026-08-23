import inspect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from types import FunctionType
from typing import Any

from pydantic import BaseModel

from pyrpckit.errors import ProtocolDefinitionError, RpcError, declared_error

_METHOD_METADATA_KEY = "__pyrpckit_method__"
_EVENT_METADATA_KEY = "__pyrpckit_event__"


class RpcHandler:
    """Base for classes whose methods are exposed through ``@method``."""


@dataclass(frozen=True, slots=True)
class RpcMethodMetadata:
    name: str
    summary: str | None
    errors: tuple[type[RpcError], ...]


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
    summary: str | None = None,
    errors: Iterable[type[RpcError]] = (),
) -> Callable[[HandlerT], HandlerT]:
    """Expose a handler method under ``name`` in the RPC protocol.

    The decorated method accepts ``self`` and at most one Pydantic params model,
    and must annotate its return type with a Pydantic model or ``None``. A method
    that takes no params declares none, and one that answers with nothing returns
    ``None``; neither needs a placeholder model. Without an explicit ``summary``
    the first line of the docstring is used, if there is one. Each declared error
    must be an ``RpcError`` subclass; the server serialises those directly, so
    they need no ``error_mapper``.
    """

    def decorate(handler: HandlerT) -> HandlerT:
        if _METHOD_METADATA_KEY in handler.__dict__:
            raise ProtocolDefinitionError(
                f"RPC handler is already decorated: {handler.__name__}"
            )
        setattr(
            handler,
            _METHOD_METADATA_KEY,
            RpcMethodMetadata(
                name=str(name),
                summary=summary if summary is not None else _docstring_summary(handler),
                errors=tuple(declared_error(error) for error in errors),
            ),
        )
        return handler

    return decorate


def event[EventT: type[BaseModel]](message: EventT) -> EventT:
    """Register a Pydantic model as an event payload.

    The model must discriminate itself with a ``type`` field pinned to a literal,
    so that clients can narrow a union of events on the wire. That literal is the
    event name.
    """
    if _EVENT_METADATA_KEY in message.__dict__:
        raise ProtocolDefinitionError(
            f"RPC event is already decorated: {message.__name__}"
        )
    declared = _declared_event_type(message)
    if not isinstance(declared, str):
        raise ProtocolDefinitionError(
            f"RPC event {message.__name__} needs a type field "
            "pinned to a string literal"
        )
    setattr(message, _EVENT_METADATA_KEY, RpcEventMetadata(name=declared))
    return message


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
        raise ProtocolDefinitionError(
            f"Invalid RPC event metadata on {message.__name__}"
        )
    return metadata


def _method_metadata(attribute: object) -> RpcMethodMetadata | None:
    if not isinstance(attribute, FunctionType):
        return None
    metadata = attribute.__dict__.get(_METHOD_METADATA_KEY)
    if metadata is None:
        return None
    if not isinstance(metadata, RpcMethodMetadata):
        raise ProtocolDefinitionError(
            f"Invalid RPC method metadata on {attribute.__name__}"
        )
    return metadata


def _docstring_summary(handler: Any) -> str | None:
    docstring = inspect.getdoc(handler)
    if not docstring:
        return None
    return docstring.splitlines()[0].strip() or None


def _declared_event_type(message: type[BaseModel]) -> Any:
    schema = message.model_json_schema()
    return schema.get("properties", {}).get("type", {}).get("const")
