from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

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


@dataclass(frozen=True, slots=True)
class RpcContract:
    protocol: RpcProtocol
    title: str
    description: str = "Typed JSON-RPC API."
    servers: tuple[Mapping[str, Any], ...] = ()

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
        object.__setattr__(self, "servers", servers)
