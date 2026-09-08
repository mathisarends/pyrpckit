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
