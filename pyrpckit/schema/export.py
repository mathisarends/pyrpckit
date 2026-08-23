import importlib
import json
from typing import Any

from pyrpckit.protocol import RpcProtocol
from pyrpckit.schema.json_schema import render_json_schema
from pyrpckit.schema.openrpc import Server, render_openrpc

FORMATS = ("openrpc", "json-schema")


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
    schema_format: str,
    *,
    title: str,
    description: str | None = None,
    servers: tuple[Server, ...] = (),
) -> str:
    """Render a contract as the JSON text committed to the repository."""
    document = _document(
        protocol,
        schema_format,
        title=title,
        description=description,
        servers=servers,
    )
    return json.dumps(document, indent=2) + "\n"


def _document(
    protocol: RpcProtocol,
    schema_format: str,
    *,
    title: str,
    description: str | None,
    servers: tuple[Server, ...],
) -> dict[str, Any]:
    described = {} if description is None else {"description": description}
    if schema_format == "openrpc":
        return render_openrpc(protocol, title=title, servers=servers, **described)
    if schema_format == "json-schema":
        return render_json_schema(protocol, title=title, **described)
    raise ValueError(f"Unknown schema format: {schema_format}")
