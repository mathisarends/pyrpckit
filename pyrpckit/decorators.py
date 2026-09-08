import inspect
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from pyrpckit.errors import ProtocolDefinitionError

_EVENT_METADATA_KEY = "__pyrpckit_event__"


@dataclass(frozen=True, slots=True)
class RpcEventMetadata:
    name: str


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


def event_metadata(message: type[BaseModel]) -> RpcEventMetadata | None:
    metadata = message.__dict__.get(_EVENT_METADATA_KEY)
    if metadata is None:
        return None
    if not isinstance(metadata, RpcEventMetadata):
        raise ProtocolDefinitionError(
            f"Invalid RPC event metadata on {message.__name__}"
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
