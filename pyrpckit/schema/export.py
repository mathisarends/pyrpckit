import importlib
import json

from pyrpckit.app import RpcChannel
from pyrpckit.contract import RpcContract
from pyrpckit.protocol import RpcProtocol
from pyrpckit.schema.openrpc import Server, render_openrpc


class ProtocolReferenceError(Exception):
    """Raised when a ``module:attribute`` reference names no RPC source."""


type ContractSource = RpcChannel | RpcContract


def load_contract_source(reference: str) -> ContractSource:
    """Import a channel or contract named by ``module:attribute``."""
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ProtocolReferenceError(
            f"Expected a module:attribute reference, got {reference!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ProtocolReferenceError(f"Cannot import {module_name}: {error}") from error
    try:
        source = getattr(module, attribute_name)
    except AttributeError as error:
        raise ProtocolReferenceError(
            f"{module_name} has no attribute {attribute_name}"
        ) from error
    if not isinstance(source, RpcChannel | RpcContract):
        raise ProtocolReferenceError(
            f"{reference} is a {type(source).__name__}, not an RpcChannel or "
            "RpcContract"
        )
    return source


def load_protocol(reference: str) -> RpcProtocol:
    """Import a channel or contract and return its internal protocol."""
    source = load_contract_source(reference)
    return source.protocol


def render_contract(
    source: ContractSource,
    *,
    title: str | None = None,
    description: str | None = None,
    servers: tuple[Server, ...] | None = None,
) -> str:
    """Render a contract as the JSON text committed to the repository."""
    if isinstance(source, RpcContract):
        protocol = source.protocol
        resolved_title = source.title if title is None else title
        resolved_description = (
            source.description if description is None else description
        )
        resolved_servers = tuple(source.servers) if servers is None else servers
    else:
        protocol = source.protocol
        if title is None:
            raise ProtocolReferenceError(
                "A title is required when rendering an RpcChannel"
            )
        resolved_title = title
        resolved_description = (
            "Typed JSON-RPC API." if description is None else description
        )
        resolved_servers = () if servers is None else servers
    document = render_openrpc(
        protocol,
        title=resolved_title,
        description=resolved_description,
        servers=resolved_servers,
    )
    return json.dumps(document, indent=2) + "\n"
