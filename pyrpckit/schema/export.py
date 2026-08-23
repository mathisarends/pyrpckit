import importlib
import json

from pyrpckit.protocol import RpcProtocol
from pyrpckit.schema.openrpc import Server, render_openrpc


class ProtocolReferenceError(Exception):
    """Raised when a ``module:attribute`` reference names no protocol."""


def load_protocol(reference: str) -> RpcProtocol:
    """Import the ``RpcProtocol`` that a ``module:attribute`` reference names."""
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
        protocol = getattr(module, attribute_name)
    except AttributeError as error:
        raise ProtocolReferenceError(
            f"{module_name} has no attribute {attribute_name}"
        ) from error
    if not isinstance(protocol, RpcProtocol):
        raise ProtocolReferenceError(
            f"{reference} is a {type(protocol).__name__}, not an RpcProtocol"
        )
    return protocol


def render_contract(
    protocol: RpcProtocol,
    *,
    title: str,
    description: str | None = None,
    servers: tuple[Server, ...] = (),
) -> str:
    """Render a contract as the JSON text committed to the repository."""
    described = {} if description is None else {"description": description}
    document = render_openrpc(protocol, title=title, servers=servers, **described)
    return json.dumps(document, indent=2) + "\n"
