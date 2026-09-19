from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import TypeAdapter

from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import RpcCallback, RpcMethodDefinition, RpcProtocol
from pyrpckit.schema.components import (
    callback_schema_name,
    components,
    described,
    notification_schema_name,
    type_name,
)

OPENRPC_VERSION = "1.4.1"

type Server = Mapping[str, Any]


def render_openrpc(
    protocol: RpcProtocol,
    *,
    title: str,
    description: str = "Typed JSON-RPC API.",
    servers: Iterable[Server] = (),
    binary_streams: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Render the protocol as an OpenRPC 1.4.1 document."""
    server_documents = tuple(dict(server) for server in servers)
    server_lookup = _server_lookup(server_documents)
    _validate_server_references(protocol, server_lookup)
    schemas = _rewrite_refs(components(protocol))
    document = {
        "openrpc": OPENRPC_VERSION,
        "info": {
            "title": title,
            "version": f"{protocol.version}.0.0",
            "description": description,
        },
        "servers": list(server_documents),
        "methods": [
            _method(method, schemas, server_lookup) for method in protocol.methods
        ],
        "components": {"schemas": schemas},
        "x-rpc-protocol-version": protocol.version,
        "x-rpc-notifications": [
            described(
                _with_server(
                    {
                        "name": notification.name,
                        "payload": _ref(type_name(notification.payload)),
                        "message": _ref(notification_schema_name(notification.name)),
                    },
                    notification.server,
                    server_lookup,
                ),
                notification.summary,
            )
            for notification in protocol.notifications
        ],
        "x-rpc-notification-types": [
            {"name": item.name, "payload": _ref(type_name(item.payload))}
            for item in protocol.notification_types
        ],
    }
    if protocol.callbacks:
        document["x-rpc-callbacks"] = [
            _callback(callback, schemas, server_lookup)
            for callback in protocol.callbacks
        ]
    streams = [dict(stream) for stream in binary_streams]
    if streams:
        document["x-rpckit-binary-streams"] = streams
    return document


def _method(
    method: RpcMethodDefinition,
    components: dict[str, Any],
    servers: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    document: dict[str, Any] = {"name": method.name}
    if method.summary is not None:
        document["summary"] = method.summary
    document |= {
        "paramStructure": "by-name",
        "params": _params(method, components),
        "result": {"name": "result", "schema": _result_schema(method, components)},
        "x-rpc-request-schema": _ref(method.request_name),
    }
    if method.params is not None:
        document["x-rpc-params-schema"] = _ref(type_name(method.params))
    if method.raises:
        document["errors"] = [_error(error) for error in method.raises]
    if method.server is not None:
        document["servers"] = [servers[method.server]]
    return document


def _callback(
    callback: RpcCallback,
    components: dict[str, Any],
    servers: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    document: dict[str, Any] = {"name": callback.name}
    if callback.summary is not None:
        document["summary"] = callback.summary
    document |= {
        "paramStructure": "by-name",
        "params": _params(callback, components),
        "result": {"name": "result", "schema": _result_schema(callback, components)},
        "x-rpc-request-schema": _ref(callback_schema_name(callback.name)),
    }
    if callback.params is not None:
        document["x-rpc-params-schema"] = _ref(type_name(callback.params))
    if callback.raises:
        document["errors"] = [_error(error) for error in callback.raises]
    return _with_server(document, callback.server, servers)


def _error(error: type) -> dict[str, Any]:
    document: dict[str, Any] = {
        "code": int(error.rpc_code),
        "message": error.message,
        "x-rpckit-code": error.code,
    }
    if error.details_type is not None:
        document["x-rpckit-details-schema"] = _ref(type_name(error.details_type))
    return document


def _server_lookup(
    servers: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for server in servers:
        name = server.get("name")
        if not isinstance(name, str) or not name:
            raise ProtocolDefinitionError("OpenRPC servers need a non-empty name")
        if name in lookup:
            raise ProtocolDefinitionError(f"Duplicate OpenRPC server name: {name}")
        lookup[name] = server
    return lookup


def _validate_server_references(
    protocol: RpcProtocol,
    servers: Mapping[str, dict[str, Any]],
) -> None:
    references = [
        ("method", method.name, method.server)
        for method in protocol.methods
        if method.server is not None
    ]
    references.extend(
        ("notification", notification.name, notification.server)
        for notification in protocol.notifications
        if notification.server is not None
    )
    references.extend(
        ("callback", callback.name, callback.server)
        for callback in protocol.callbacks
        if callback.server is not None
    )
    missing = [reference for reference in references if reference[2] not in servers]
    if missing:
        details = ", ".join(
            f"{kind} {name!r} -> {server!r}" for kind, name, server in missing
        )
        raise ProtocolDefinitionError(
            f"RPC routes reference undeclared OpenRPC servers: {details}"
        )


def _with_server(
    document: dict[str, Any],
    server: str | None,
    servers: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    if server is not None:
        document["servers"] = [servers[server]]
    return document


def _params(
    method: RpcMethodDefinition | RpcCallback,
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
    method: RpcMethodDefinition | RpcCallback,
    components: dict[str, Any],
) -> dict[str, Any]:
    name = getattr(method.result, "__name__", None)
    if isinstance(name, str) and name in components:
        return _ref(name)
    schema = TypeAdapter(method.result).json_schema(
        by_alias=True,
        mode="serialization",
    )
    schema.pop("$defs", None)
    return _rewrite_refs(schema)


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
