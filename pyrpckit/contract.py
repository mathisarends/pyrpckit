import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import RpcProtocol
from pyrpckit.streams import RpcStreamDirection


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
        if any(not isinstance(value, str) for value in self.enum):
            raise ProtocolDefinitionError(
                "OpenRPC server variable enums must be strings"
            )
        if self.enum and self.default not in self.enum:
            raise ProtocolDefinitionError(
                "OpenRPC server variable default must be one of its enum values"
            )

    def document(self) -> dict[str, Any]:
        value: dict[str, Any] = {"default": self.default}
        if self.description is not None:
            value["description"] = self.description
        if self.enum:
            value["enum"] = list(self.enum)
        return value


@dataclass(frozen=True, slots=True)
class RpcContract:
    protocol: RpcProtocol
    title: str
    description: str = "Typed JSON-RPC API."
    servers: tuple[Mapping[str, Any], ...] = ()
    binary_streams: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.title:
            raise ProtocolDefinitionError("RPC contract title cannot be empty")
        object.__setattr__(
            self,
            "servers",
            tuple(MappingProxyType(deepcopy(dict(item))) for item in self.servers),
        )
        object.__setattr__(
            self,
            "binary_streams",
            tuple(
                MappingProxyType(deepcopy(dict(item))) for item in self.binary_streams
            ),
        )

    def to_openrpc(self) -> dict[str, Any]:
        from pyrpckit.schema.openrpc import render_openrpc

        return render_openrpc(
            self.protocol,
            title=self.title,
            description=self.description,
            servers=self.servers,
            binary_streams=self.binary_streams,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_openrpc(), indent=2, ensure_ascii=False) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8", newline="\n")


def contract_from_service(
    service,
    *,
    title: str,
    base_url: str,
    description: str,
    variables: Mapping[str, ServerVariable] | None,
) -> RpcContract:
    from pyrpckit.service import RpcEndpoint, RpcStreamEndpoint

    if (
        not isinstance(base_url, str)
        or not base_url
        or not (base_url.startswith(("http://", "https://", "ws://", "wss://", "{")))
    ):
        raise ProtocolDefinitionError(
            "RPC contract base_url must be an HTTP or WebSocket URL"
        )
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url.removeprefix("https://")
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url.removeprefix("http://")
    base_url = base_url.rstrip("/")
    supplied = dict(variables or {})
    used: set[str] = set()
    servers = []
    streams = []
    for endpoint in service.endpoints:
        url = base_url + endpoint.path
        names = tuple(dict.fromkeys(re.findall(r"{([^{}]+)}", url)))
        used.update(names)
        variable_docs = {
            name: supplied.get(name, ServerVariable(default=f"{{{name}}}")).document()
            for name in names
        }
        if isinstance(endpoint, RpcEndpoint):
            transport: dict[str, Any] = {
                "type": "websocket",
                "messageEncoding": "json",
                "frameType": "text",
            }
            if endpoint.subprotocol:
                transport["subprotocols"] = [endpoint.subprotocol]
            server: dict[str, Any] = {
                "name": endpoint.name,
                "url": url,
                "x-rpckit-transport": transport,
            }
            if endpoint.summary:
                server["summary"] = endpoint.summary
            if variable_docs:
                server["variables"] = variable_docs
            servers.append(server)
        elif isinstance(endpoint, RpcStreamEndpoint):
            stream = endpoint.stream
            item: dict[str, Any] = {
                "name": stream.name,
                "url": url,
                "direction": stream.direction.value,
            }
            if stream.direction is not RpcStreamDirection.CLIENT_TO_SERVER:
                item["contentType"] = stream.content_type
            if stream.input_content_type is not None:
                item["inputContentType"] = stream.input_content_type
            item["frameType"] = "binary"
            summary = stream.summary or endpoint.summary
            if summary:
                item["summary"] = summary
            if variable_docs:
                item["variables"] = variable_docs
            if endpoint.subprotocol:
                item["subprotocols"] = [endpoint.subprotocol]
            streams.append(item)
    unused = set(supplied) - used
    if unused:
        raise ProtocolDefinitionError(
            "RPC contract variables are not present in any endpoint URL: "
            + ", ".join(sorted(unused))
        )
    return RpcContract(
        service.freeze(), title, description, tuple(servers), tuple(streams)
    )
