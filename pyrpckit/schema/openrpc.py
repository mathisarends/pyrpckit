from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.errors import error_message
from pyrpckit.protocol import RpcMethodDefinition, RpcProtocol
from pyrpckit.schema.json_schema import (
    notification_schema_name,
    render_json_schema,
    type_name,
)

OPENRPC_VERSION = "1.3.2"

type Server = Mapping[str, str]


def render_openrpc(
    protocol: RpcProtocol,
    *,
    title: str,
    description: str = "Typed JSON-RPC API.",
    servers: Iterable[Server] = (),
) -> dict[str, Any]:
    """Render the protocol as an OpenRPC 1.3.2 document."""
    components = _rewrite_refs(
        render_json_schema(protocol, title=title, description=description)["$defs"]
    )
    return {
        "openrpc": OPENRPC_VERSION,
        "info": {
            "title": title,
            "version": f"{protocol.version}.0.0",
            "description": description,
        },
        "servers": [dict(server) for server in servers],
        "methods": [_method(method, components) for method in protocol.methods],
        "components": {"schemas": components},
        "x-rpc-protocol-version": protocol.version,
        "x-rpc-notifications": [
            {
                "name": notification.name,
                "summary": notification.summary,
                "payload": _ref(type_name(notification.payload)),
                "message": _ref(notification_schema_name(notification.name)),
            }
            for notification in protocol.notifications
        ],
        "x-rpc-events": [
            {"name": event.name, "payload": _ref(type_name(event.payload))}
            for event in protocol.events
        ],
    }


def _method(
    method: RpcMethodDefinition,
    components: dict[str, Any],
) -> dict[str, Any]:
    params_name = type_name(method.params)
    params_schema = components[params_name]
    required = set(params_schema.get("required", ()))
    document: dict[str, Any] = {
        "name": method.name,
        "summary": method.summary,
        "paramStructure": "by-name",
        "params": [
            {"name": name, "required": name in required, "schema": schema}
            for name, schema in params_schema.get("properties", {}).items()
        ],
        "result": {"name": "result", "schema": _result_schema(method, components)},
        "x-rpc-request-schema": _ref(method.request_name),
        "x-rpc-params-schema": _ref(params_name),
    }
    if method.errors:
        document["errors"] = [
            {"code": int(code), "message": error_message(code)} for code in method.errors
        ]
    return document


def _result_schema(
    method: RpcMethodDefinition,
    components: dict[str, Any],
) -> dict[str, Any]:
    name = type_name(method.result)
    if name in components:
        return _ref(name)
    return _rewrite_refs(TypeAdapter(method.result).json_schema())


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _rewrite_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    return value
