from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.protocol import RpcMethodDefinition, RpcProtocol
from pyrpckit.schema.json_schema import (
    described,
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
            described(
                {
                    "name": notification.name,
                    "payload": _ref(type_name(notification.payload)),
                    "message": _ref(notification_schema_name(notification.name)),
                },
                notification.summary,
            )
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
    document: dict[str, Any] = {"name": method.name}
    if method.summary is not None:
        document["summary"] = method.summary
    if method.feature is not None:
        document["tags"] = [{"name": method.feature}]
    document |= {
        "paramStructure": "by-name",
        "params": _params(method, components),
        "result": {"name": "result", "schema": _result_schema(method, components)},
        "x-rpc-request-schema": _ref(method.request_name),
    }
    if method.params is not None:
        document["x-rpc-params-schema"] = _ref(type_name(method.params))
    if method.errors:
        document["errors"] = [
            {"code": int(error.code), "message": error.message}
            for error in method.errors
        ]
    return document


def _params(
    method: RpcMethodDefinition,
    components: dict[str, Any],
) -> list[dict[str, Any]]:
    if method.params is None:
        return []
    schema = components[type_name(method.params)]
    required = set(schema.get("required", ()))
    return [
        {"name": name, "required": name in required, "schema": member}
        for name, member in schema.get("properties", {}).items()
    ]


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
