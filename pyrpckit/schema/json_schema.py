import re
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.envelopes import RpcFailure, RpcSuccess
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
)

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def render_json_schema(
    protocol: RpcProtocol,
    *,
    title: str,
    description: str = "JSON-RPC requests, responses, and notifications.",
    schema_id: str | None = None,
) -> dict[str, Any]:
    """Render every frame of the protocol as one ``oneOf`` schema document."""
    annotations = _annotations(protocol)
    schema_map, root = TypeAdapter.json_schemas(
        ((name, "validation", TypeAdapter(annotation)) for name, annotation in annotations.items()),
        title=title,
        description=description,
    )
    refs = {name: schema_map[(name, "validation")] for name in annotations}
    definitions = root["$defs"]
    for method in protocol.methods:
        definitions[method.request_name] = _request_schema(method)
        refs[method.request_name] = _ref(method.request_name)
    for notification in protocol.notifications:
        name = notification_schema_name(notification.name)
        definitions[name] = _notification_schema(notification, name)
        refs[name] = _ref(name)
    document: dict[str, Any] = {"$schema": JSON_SCHEMA_DIALECT}
    if schema_id is not None:
        document["$id"] = schema_id
    document.update(
        {
            "title": title,
            "description": description,
            "oneOf": [refs[name] for name in _frame_names(protocol)],
            "$defs": definitions,
            "x-rpc-protocol-version": protocol.version,
            "x-rpc-methods": [_method_entry(method, refs) for method in protocol.methods],
            "x-rpc-notifications": [
                described(
                    {
                        "name": notification.name,
                        "payload": refs[type_name(notification.payload)],
                        "message": refs[notification_schema_name(notification.name)],
                    },
                    notification.summary,
                )
                for notification in protocol.notifications
            ],
            "x-rpc-events": [
                {"name": event.name, "payload": refs[type_name(event.payload)]}
                for event in protocol.events
            ],
        }
    )
    return document


def _method_entry(method: RpcMethodDefinition, refs: dict[str, Any]) -> dict[str, Any]:
    entry = described({"name": method.name}, method.summary)
    if method.feature is not None:
        entry["feature"] = method.feature
    entry.update(
        {
            "request": refs[method.request_name],
            "params": refs[type_name(method.params)],
            "result": refs[type_name(method.result)],
        }
    )
    if method.errors:
        entry["errors"] = [
            {"code": int(error.code), "message": error.message} for error in method.errors
        ]
    return entry


def described(entry: dict[str, Any], summary: str | None) -> dict[str, Any]:
    """Attach a summary only when the definition carries one."""
    if summary is not None:
        entry["summary"] = summary
    return entry


def type_name(annotation: Any) -> str:
    name = getattr(annotation, "__name__", None)
    if not isinstance(name, str):
        raise ProtocolDefinitionError(f"Protocol type has no stable schema name: {annotation!r}")
    return name


def notification_schema_name(name: str) -> str:
    parts = (part for part in re.split(r"[^a-zA-Z0-9]+", name) if part)
    return "".join(part.capitalize() for part in parts) + "Notification"


def _frame_names(protocol: RpcProtocol) -> list[str]:
    names = [method.request_name for method in protocol.methods]
    names.extend((type_name(RpcSuccess), type_name(RpcFailure)))
    names.extend(
        notification_schema_name(notification.name) for notification in protocol.notifications
    )
    return names


def _annotations(protocol: RpcProtocol) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for method in protocol.methods:
        _add(annotations, method.params)
        _add(annotations, method.result)
    for notification in protocol.notifications:
        _add(annotations, notification.payload)
    for event in protocol.events:
        _add(annotations, event.payload)
    for envelope in (RpcSuccess, RpcFailure):
        _add(annotations, envelope)
    return annotations


def _add(annotations: dict[str, Any], annotation: Any) -> None:
    name = type_name(annotation)
    existing = annotations.get(name)
    if existing is not None and existing != annotation:
        raise ProtocolDefinitionError(f"Duplicate protocol schema name: {name}")
    annotations[name] = annotation


def _request_schema(method: RpcMethodDefinition) -> dict[str, Any]:
    params_schema = method.params.model_json_schema()
    required = ["jsonrpc", "method"]
    if params_schema.get("required"):
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
            "params": _ref(type_name(method.params)),
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
