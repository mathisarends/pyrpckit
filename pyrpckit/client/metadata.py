from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class RpcContractInfo:
    title: str
    version: str
    protocol_version: int | None = None


@dataclass(frozen=True, slots=True)
class RpcRouteInfo:
    method: str
    summary: str = ""
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    error_codes: tuple[int, ...] = ()
    server_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RpcServerVariable:
    default: str
    description: str = ""
    enum: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.enum and self.default not in self.enum:
            raise ValueError(
                "The server variable default must be one of its enum values"
            )


@dataclass(frozen=True, slots=True)
class RpcServerInfo:
    name: str
    url: str
    summary: str = ""
    description: str = ""
    variables: Mapping[str, RpcServerVariable] = field(default_factory=dict)
    transport: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", MappingProxyType(dict(self.variables)))

    def resolve(self, values: Mapping[str, str] | None = None, /, **kwargs: str) -> str:
        supplied = {**dict(values or {}), **kwargs}
        unknown = sorted(set(supplied) - set(self.variables))
        if unknown:
            raise ValueError(
                f"Unknown variables for server {self.name!r}: {', '.join(unknown)}"
            )
        resolved = self.url
        for name, variable in self.variables.items():
            value = supplied.get(name, variable.default)
            if variable.enum and value not in variable.enum:
                raise ValueError(
                    f"Invalid value {value!r} for server variable {name!r}; "
                    f"expected one of {variable.enum!r}"
                )
            resolved = resolved.replace(f"{{{name}}}", value)
        return resolved


type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
