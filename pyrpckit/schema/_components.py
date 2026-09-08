import re
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
)

EMPTY_PARAMS_SCHEMA: dict[str, Any] = {
    "additionalProperties": False,
    "type": "object",
}
"""What a method without params accepts: an omitted or empty ``params`` member."""


def components(protocol: RpcProtocol) -> dict[str, Any]:
    """Build the JSON Schema components referenced by the OpenRPC document."""
    annotations = _annotations(protocol)
    _, root = TypeAdapter.json_schemas(
        (
            (name, "validation", TypeAdapter(annotation))
            for name, annotation in annotations.items()
        )
    )
    definitions = root.get("$defs", {})
    for method in protocol.methods:
        definitions[method.request_name] = _request_schema(method)
    for notification in protocol.notifications:
        name = notification_schema_name(notification.name)
        definitions[name] = _notification_schema(notification, name)
    return definitions


def described(entry: dict[str, Any], summary: str | None) -> dict[str, Any]:
    """Attach a summary only when the definition carries one."""
    if summary is not None:
        entry["summary"] = summary
    return entry


def type_name(annotation: Any) -> str:
    name = getattr(annotation, "__name__", None)
    if not isinstance(name, str):
        raise ProtocolDefinitionError(
            f"Protocol type has no stable schema name: {annotation!r}"
        )
    return name


def notification_schema_name(name: str) -> str:
    parts = (part for part in re.split(r"[^a-zA-Z0-9]+", name) if part)
    return "".join(part.capitalize() for part in parts) + "Notification"


def _annotations(protocol: RpcProtocol) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for method in protocol.methods:
        if method.params is not None:
            _add(annotations, method.params)
        _add(annotations, method.result)
    for notification in protocol.notifications:
        _add(annotations, notification.payload)
    for event in protocol.events:
        _add(annotations, event.payload)
    return annotations


def _add(annotations: dict[str, Any], annotation: Any) -> None:
    name = type_name(annotation)
    existing = annotations.get(name)
    if existing is not None and existing != annotation:
        raise ProtocolDefinitionError(f"Duplicate protocol schema name: {name}")
    annotations[name] = annotation


def _request_schema(method: RpcMethodDefinition) -> dict[str, Any]:
    required = ["jsonrpc", "method"]
    if method.params is None:
        params = EMPTY_PARAMS_SCHEMA
    else:
        params = _ref(type_name(method.params))
        if method.params.model_json_schema().get("required"):
            required.append("params")
    schema: dict[str, Any] = {
        "additionalProperties": False,
        "type": "object",
        "title": method.request_name,
        "properties": {
            "jsonrpc": {"const": "2.0", "type": "string"},
            "id": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                    {"type": "null"},
                ],
                "default": None,
            },
            "method": {"const": method.name, "type": "string"},
            "params": params,
        },
        "required": required,
    }
    if method.summary is not None:
        schema["description"] = method.summary
    return schema


def _notification_schema(
    notification: RpcNotificationDefinition,
    schema_name: str,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "additionalProperties": False,
        "type": "object",
        "title": schema_name,
        "properties": {
            "jsonrpc": {"const": "2.0", "type": "string"},
            "method": {"const": notification.name, "type": "string"},
            "params": _ref(type_name(notification.payload)),
        },
        "required": ["jsonrpc", "method", "params"],
    }
    if notification.summary is not None:
        schema["description"] = notification.summary
    return schema


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}
