from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pyrpckit.codegen.ir import ClientIr, UnsupportedSchemaError, build_ir
from pyrpckit.codegen.python import PythonClientOptions, render_files
from pyrpckit.codegen.writer import write_files

__all__ = [
    "ClientIr",
    "PythonClientOptions",
    "UnsupportedSchemaError",
    "build_ir",
    "generate_python_client",
    "render_python_client",
    "write_files",
]


def render_python_client(
    document: dict[str, Any],
    options: PythonClientOptions,
) -> Mapping[str, str]:
    """Render a Python client package as relative path to file content."""
    return render_files(build_ir(document), options)


def generate_python_client(
    document: dict[str, Any],
    output_dir: Path,
    options: PythonClientOptions,
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    """Write a Python client package and report which files differed."""
    return write_files(output_dir, render_python_client(document, options), check=check)
