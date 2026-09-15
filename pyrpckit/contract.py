import re
from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal

from pyrpckit.app import RpcChannel
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import RpcProtocol


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


type BinaryStreamDirection = Literal[
    "client-to-server", "server-to-client", "bidirectional"
]


@dataclass(frozen=True, slots=True)
class BinaryStream:
    """A binary WebSocket endpoint adjacent to the JSON-RPC control plane."""

    name: str
    url: str
    direction: BinaryStreamDirection = "bidirectional"
    content_type: str = "application/octet-stream"
    summary: str | None = None
    description: str | None = None
    variables: Mapping[str, ServerVariable] | None = None
    subprotocols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ProtocolDefinitionError("Binary stream names must be non-empty")
        if not isinstance(self.url, str) or not self.url:
            raise ProtocolDefinitionError("Binary stream URLs must be non-empty")
        if self.direction not in {
            "client-to-server",
            "server-to-client",
            "bidirectional",
        }:
            raise ProtocolDefinitionError(
                f"Invalid binary stream direction: {self.direction!r}"
            )
        if not isinstance(self.content_type, str) or not self.content_type:
            raise ProtocolDefinitionError(
                "Binary stream content types must be non-empty"
            )
        subprotocols = tuple(self.subprotocols)
        if any(not isinstance(value, str) or not value for value in subprotocols):
            raise ProtocolDefinitionError(
                "Binary stream subprotocols must be non-empty strings"
            )
        variables = dict(self.variables or {})
        if any(
            not isinstance(name, str) or not isinstance(value, ServerVariable)
            for name, value in variables.items()
        ):
            raise ProtocolDefinitionError(
                "Binary stream variables must map names to ServerVariable objects"
            )
        url_variables = set(re.findall(r"{([^{}]+)}", self.url))
        if set(variables) != url_variables:
            raise ProtocolDefinitionError(
                "Binary stream variables must match URL variables exactly"
            )
        object.__setattr__(self, "subprotocols", subprotocols)
        object.__setattr__(self, "variables", MappingProxyType(variables))

    def document(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "direction": self.direction,
            "contentType": self.content_type,
            "frameType": "binary",
        }
        if self.summary is not None:
            value["summary"] = self.summary
        if self.description is not None:
            value["description"] = self.description
        if self.variables:
            value["variables"] = {
                name: variable.document() for name, variable in self.variables.items()
            }
        if self.subprotocols:
            value["subprotocols"] = list(self.subprotocols)
        return value


@dataclass(frozen=True, slots=True)
class RpcContract:
    protocol: RpcProtocol
    title: str
    description: str = "Typed JSON-RPC API."
    servers: tuple[Mapping[str, Any], ...] = ()
    binary_streams: tuple[BinaryStream, ...] = ()

    @classmethod
    def from_channels(
        cls,
        *,
        channels: Iterable[RpcChannel],
        title: str,
        server_urls: Mapping[str, str],
        description: str = "Typed JSON-RPC API.",
        variables: Mapping[str, ServerVariable] | None = None,
        subprotocols: Mapping[str, str] | None = None,
        binary_streams: Iterable[BinaryStream] = (),
    ) -> "RpcContract":
        """Build a WebSocket contract from channels and explicit public URLs.

        URL variable names are preserved exactly. All channels must share a version.
        Export accesses each channel's protocol, freezing its definitions.
        """
        channels = tuple(channels)
        if not channels:
            raise ProtocolDefinitionError("RPC contracts need at least one channel")
        names = [channel.name for channel in channels]
        if len(set(names)) != len(names):
            raise ProtocolDefinitionError("Duplicate RPC channel names")
        if set(server_urls) != set(names):
            raise ProtocolDefinitionError(
                "RPC server URLs must match channel names exactly"
            )
        if any(not isinstance(url, str) or not url for url in server_urls.values()):
            raise ProtocolDefinitionError("RPC server URLs must be non-empty strings")
        variables = dict(variables or {})
        if any(
            not isinstance(name, str) or not isinstance(value, ServerVariable)
            for name, value in variables.items()
        ):
            raise ProtocolDefinitionError(
                "RPC contract variables must map names to ServerVariable objects"
            )
        subprotocols = dict(subprotocols or {})
        if set(subprotocols) - set(names) or any(
            not isinstance(value, str) or not value for value in subprotocols.values()
        ):
            raise ProtocolDefinitionError(
                "RPC subprotocols must map channel names to non-empty strings"
            )
        servers: list[Mapping[str, Any]] = []
        used_variables: set[str] = set()
        for channel in channels:
            url = server_urls[channel.name]
            url_variables = tuple(dict.fromkeys(re.findall(r"{([^{}]+)}", url)))
            used_variables.update(url_variables)
            transport: dict[str, Any] = {
                "type": "websocket",
                "messageEncoding": "json",
                "frameType": "text",
            }
            if channel.name in subprotocols:
                transport["subprotocols"] = [subprotocols[channel.name]]
            server: dict[str, Any] = {
                "name": channel.name,
                "url": url,
                "x-rpckit-transport": transport,
            }
            if url_variables:
                server["variables"] = {
                    name: variables.get(
                        name, ServerVariable(default=f"{{{name}}}")
                    ).document()
                    for name in url_variables
                }
            servers.append(server)
        if set(variables) - used_variables:
            raise ProtocolDefinitionError(
                "RPC contract variables are not present in any channel URL: "
                + ", ".join(sorted(set(variables) - used_variables))
            )
        versions = {channel.protocol.version for channel in channels}
        if len(versions) != 1:
            raise ProtocolDefinitionError("RPC channels must share a protocol version")
        return cls(
            protocol=_combined_protocol(channels, version=versions.pop()),
            title=title,
            description=description,
            servers=tuple(servers),
            binary_streams=tuple(binary_streams),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.protocol, RpcProtocol):
            raise ProtocolDefinitionError("RpcContract.protocol must be an RpcProtocol")
        if not self.title:
            raise ProtocolDefinitionError("RPC contract title cannot be empty")
        servers = tuple(
            MappingProxyType(deepcopy(dict(server))) for server in self.servers
        )
        names = [server.get("name") for server in servers]
        if any(not isinstance(name, str) or not name for name in names):
            raise ProtocolDefinitionError("RPC contract servers need non-empty names")
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        if duplicates:
            raise ProtocolDefinitionError(
                "Duplicate RPC contract server names: " + ", ".join(duplicates)
            )
        binary_streams = tuple(self.binary_streams)
        if any(not isinstance(stream, BinaryStream) for stream in binary_streams):
            raise ProtocolDefinitionError(
                "RpcContract.binary_streams must contain BinaryStream objects"
            )
        stream_names = [stream.name for stream in binary_streams]
        duplicate_streams = sorted(
            name for name in set(stream_names) if stream_names.count(name) > 1
        )
        if duplicate_streams:
            raise ProtocolDefinitionError(
                "Duplicate binary stream names: " + ", ".join(duplicate_streams)
            )
        object.__setattr__(self, "servers", servers)
        object.__setattr__(self, "binary_streams", binary_streams)


def _combined_protocol(
    channels: Iterable[RpcChannel],
    *,
    version: int,
) -> RpcProtocol:
    methods = [
        replace(method, server=channel.name)
        for channel in channels
        for method in channel.protocol.methods
    ]
    duplicate_requests = {
        name
        for name, count in Counter(method.request_name for method in methods).items()
        if count > 1
    }
    methods = [
        replace(
            method,
            request_name=(
                _request_name(method.name)
                if method.request_name in duplicate_requests
                else method.request_name
            ),
        )
        for method in methods
    ]
    events = tuple(
        replace(event, server=channel.name)
        for channel in channels
        for event in channel.protocol.notifications
    )
    event_types = tuple(
        event_type
        for channel in channels
        for event_type in channel.protocol.notification_types
    )
    return RpcProtocol(
        methods=methods,
        notifications=events,
        notification_types=event_types,
        version=version,
    )


def _request_name(wire_name: str) -> str:
    return "".join(part.capitalize() for part in wire_name.split(".")) + "Request"
