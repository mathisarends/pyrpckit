import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pyrpckit.codegen import generate_python_client, generate_typescript_client
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.codegen.typescript import TypeScriptClientOptions
from pyrpckit.codegen.writer import write_files
from pyrpckit.schema.export import (
    ProtocolReferenceError,
    load_protocol,
    render_contract,
)

LANGUAGES = ("python", "typescript")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "schema":
        return _schema(arguments)
    return _generate(arguments)


def _schema(arguments: argparse.Namespace) -> int:
    """Render the contract of a protocol into the repository."""
    sys.path.insert(0, str(Path.cwd()))
    try:
        protocol = load_protocol(arguments.protocol)
    except ProtocolReferenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    contract = render_contract(
        protocol,
        title=arguments.title,
        description=arguments.description,
        servers=tuple(_server(entry) for entry in arguments.server),
    )
    output = arguments.output
    changed = write_files(output.parent, {output.name: contract}, check=arguments.check)
    if arguments.check:
        return _report_check(changed, subject="Contract")
    for path in changed:
        print(f"Wrote {path}")
    if not changed:
        print("Contract is up to date")
    return 0


def _generate(arguments: argparse.Namespace) -> int:
    document = json.loads(arguments.schema.read_text(encoding="utf-8"))
    if arguments.language == "python":
        options = PythonClientOptions(
            package=arguments.package or arguments.output.name,
            client_name=arguments.client_name,
            source=arguments.schema.name,
        )
        changed = generate_python_client(
            document,
            arguments.output,
            options,
            check=arguments.check,
        )
    else:
        options = TypeScriptClientOptions(
            client_name=arguments.client_name,
            transport_module=arguments.transport_module,
            source=arguments.schema.name,
        )
        changed = generate_typescript_client(
            document,
            arguments.output,
            options,
            check=arguments.check,
        )
    if arguments.check:
        return _report_check(changed)
    for path in changed:
        print(f"Wrote {path}")
    if not changed:
        print("Client is up to date")
    return 0


def _server(entry: str) -> dict[str, str]:
    name, separator, url = entry.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError(f"Expected name=url, got {entry!r}")
    return {"name": name, "url": url}


def _report_check(changed: Sequence[Path], subject: str = "Client") -> int:
    if not changed:
        print(f"{subject} is up to date")
        return 0
    for path in changed:
        print(f"Out of date: {path}")
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyrpckit",
        description="Render RPC contracts and generate typed clients from them.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _add_schema_command(commands)
    _add_generate_command(commands)
    return parser


def _add_schema_command(commands: argparse._SubParsersAction) -> None:
    schema = commands.add_parser(
        "schema",
        help="Render the contract of a protocol.",
        description=(
            "Render an RpcProtocol as the contract clients are generated from. "
            "The protocol is named as module:attribute and imported from the "
            "current directory."
        ),
    )
    schema.add_argument("protocol", help="Protocol to render, as module:attribute.")
    schema.add_argument(
        "--output",
        type=Path,
        required=True,
        help="File the contract is written to.",
    )
    schema.add_argument("--title", required=True, help="Title of the API.")
    schema.add_argument("--description", help="Description of the API.")
    schema.add_argument(
        "--server",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="Server the API is reachable at; repeatable.",
    )
    schema.add_argument(
        "--check",
        action="store_true",
        help="Report an out-of-date contract instead of writing it.",
    )


def _add_generate_command(commands: argparse._SubParsersAction) -> None:
    generate = commands.add_parser("generate", help="Generate a client package.")
    generate.add_argument("language", choices=LANGUAGES)
    generate.add_argument("schema", type=Path, help="OpenRPC document to read.")
    generate.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory of the generated package.",
    )
    generate.add_argument(
        "--package",
        help="Import root of the generated package (default: the output directory).",
    )
    generate.add_argument(
        "--transport-module",
        default="../transport",
        help=('TypeScript module exporting RpcTransport (default: "../transport").'),
    )
    generate.add_argument(
        "--client-name",
        default="RpcClient",
        help="Name of the generated client class (default: RpcClient).",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="Report out-of-date files instead of writing them.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
