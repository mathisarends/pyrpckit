from collections.abc import Mapping
from dataclasses import dataclass, field
from string import Formatter
from types import MappingProxyType
from typing import Any

from pyrpckit.app import RpcApp
from pyrpckit.errors import ProtocolDefinitionError


@dataclass(frozen=True, slots=True)
class ServerVariable:
    default: str
    description: str | None = None
    enum: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.default, str):
            raise ProtocolDefinitionError(
                "OpenRPC server variable defaults must be strings"
            )
        values = tuple(self.enum)
        if any(not isinstance(value, str) for value in values):
            raise ProtocolDefinitionError(
                "OpenRPC server variable enums must be strings"
            )
        if values and self.default not in values:
            raise ProtocolDefinitionError(
                "OpenRPC server variable default must be one of its enum values"
            )
        object.__setattr__(self, "enum", values)

    def document(self) -> dict[str, Any]:
        value: dict[str, Any] = {"default": self.default}
        if self.description is not None:
            value["description"] = self.description
        if self.enum:
            value["enum"] = list(self.enum)
        return value


@dataclass(frozen=True, slots=True)
class OpenRpcServer:
    name: str
    url: str
    summary: str | None = None
    description: str | None = None
    variables: Mapping[str, ServerVariable] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ProtocolDefinitionError("OpenRPC server name cannot be empty")
        if not self.url:
            raise ProtocolDefinitionError("OpenRPC server URL cannot be empty")
        variables = dict(self.variables)
        if any(
            not isinstance(name, str) or not isinstance(value, ServerVariable)
            for name, value in variables.items()
        ):
            raise ProtocolDefinitionError(
                "OpenRPC server variables must map names to ServerVariable objects"
            )
        extensions = dict(self.extensions)
        invalid_extensions = [
            name
            for name in extensions
            if not isinstance(name, str) or not name.startswith("x-")
        ]
        if invalid_extensions:
            raise ProtocolDefinitionError(
                "OpenRPC server extension keys must start with 'x-': "
                + ", ".join(map(str, invalid_extensions))
            )
        _validate_transport(self.name, extensions.get("x-rpckit-transport"))
        _validate_url_variables(self.url, variables)
        object.__setattr__(self, "variables", MappingProxyType(variables))
        object.__setattr__(self, "extensions", MappingProxyType(extensions))

    def document(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name, "url": self.url}
        if self.summary is not None:
            value["summary"] = self.summary
        if self.description is not None:
            value["description"] = self.description
        if self.variables:
            value["variables"] = {
                name: variable.document() for name, variable in self.variables.items()
            }
        value.update(self.extensions)
        return value


def _validate_transport(server_name: str, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ProtocolDefinitionError(
            f"Server {server_name!r} x-rpckit-transport must be an object"
        )
    transport_type = value.get("type")
    if not isinstance(transport_type, str) or not transport_type:
        raise ProtocolDefinitionError(
            f"Server {server_name!r} transport needs a non-empty type"
        )
    message_encoding = value.get("messageEncoding")
    if message_encoding is not None and not isinstance(message_encoding, str):
        raise ProtocolDefinitionError(
            f"Server {server_name!r} transport messageEncoding must be a string"
        )
    subprotocols = value.get("subprotocols", ())
    if not isinstance(subprotocols, list | tuple) or any(
        not isinstance(item, str) for item in subprotocols
    ):
        raise ProtocolDefinitionError(
            f"Server {server_name!r} transport subprotocols must be strings"
        )
    if transport_type == "websocket" and message_encoding != "json":
        raise ProtocolDefinitionError(
            f"Server {server_name!r} websocket transport must use JSON encoding"
        )


@dataclass(frozen=True, slots=True)
class OpenRpcContract:
    app: RpcApp
    title: str
    description: str = "Typed JSON-RPC API."
    servers: tuple[OpenRpcServer, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.app, RpcApp):
            raise ProtocolDefinitionError("OpenRpcContract.app must be an RpcApp")
        if not self.title:
            raise ProtocolDefinitionError("OpenRPC contract title cannot be empty")
        servers = tuple(self.servers)
        if any(not isinstance(server, OpenRpcServer) for server in servers):
            raise ProtocolDefinitionError(
                "OpenRpcContract.servers must contain OpenRpcServer objects"
            )
        names = [server.name for server in servers]
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        if duplicates:
            raise ProtocolDefinitionError(
                "Duplicate OpenRPC server names: " + ", ".join(duplicates)
            )
        _validate_server_references(self.app, set(names))
        object.__setattr__(self, "servers", servers)


def _validate_url_variables(url: str, variables: Mapping[str, ServerVariable]) -> None:
    try:
        parsed = tuple(Formatter().parse(url))
    except ValueError as error:
        raise ProtocolDefinitionError(
            f"Invalid templated OpenRPC server URL {url!r}: {error}"
        ) from error
    placeholders: set[str] = set()
    for _, name, format_spec, conversion in parsed:
        if name is None:
            continue
        if not name or format_spec or conversion:
            raise ProtocolDefinitionError(
                f"Invalid OpenRPC server variable placeholder in {url!r}"
            )
        placeholders.add(name)
    declared = set(variables)
    missing = sorted(placeholders - declared)
    unused = sorted(declared - placeholders)
    if missing or unused:
        raise ProtocolDefinitionError(
            "OpenRPC server URL variables do not match their declarations: "
            f"missing={missing}, unused={unused}"
        )


def _validate_server_references(app: RpcApp, server_names: set[str]) -> None:
    references = [
        ("method", method.name, method.server)
        for method in app.protocol.methods
        if method.server is not None
    ]
    references.extend(
        ("notification", notification.name, notification.server)
        for notification in app.protocol.notifications
        if notification.server is not None
    )
    missing = [
        reference for reference in references if reference[2] not in server_names
    ]
    if not missing:
        return
    details = ", ".join(
        f"{kind} {name!r} -> {server!r}" for kind, name, server in missing
    )
    raise ProtocolDefinitionError(
        "RPC routes reference OpenRPC servers that are not declared in "
        f"OpenRpcContract.servers: {details}"
    )
