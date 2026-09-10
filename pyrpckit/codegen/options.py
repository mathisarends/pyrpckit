from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PythonClientOptions:
    package: str
    client_name: str | None = None
    base_model_name: str = "RpcModel"
    api_root: str | None = None
    api_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"
    with_transport: str | None = None


@dataclass(frozen=True, slots=True)
class TypeScriptClientOptions:
    client_name: str | None = None
    transport_module: str = "../transport"
    api_root: str | None = None
    api_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"
    with_transport: str | None = None
