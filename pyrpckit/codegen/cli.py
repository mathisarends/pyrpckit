import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from pyrpckit.codegen import generate_python_client, generate_typescript_client
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.codegen.typescript import TypeScriptClientOptions
from pyrpckit.codegen.writer import write_files
from pyrpckit.schema.export import (
    ProtocolReferenceError,
    load_contract_source,
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
        source = load_contract_source(arguments.source)
        contract = render_contract(
            source,
            title=arguments.title,
            description=arguments.description,
            servers=(
                tuple(_server(entry) for entry in arguments.server)
                if arguments.server
                else None
            ),
        )
    except ProtocolReferenceError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
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
    if arguments.config is not None:
        return _generate_config(arguments.config, check=arguments.check)
    if (
        arguments.schema is None
        or arguments.language is None
        or arguments.output is None
    ):
        print(
            "error: schema, --language, and --output are required without --config",
            file=sys.stderr,
        )
        return 2
    return _generate_one(arguments)


def _generate_one(arguments: argparse.Namespace) -> int:
    document = json.loads(arguments.schema.read_text(encoding="utf-8"))
    if arguments.language == "python":
        options = PythonClientOptions(
            package=arguments.package or arguments.output.name,
            client_name=arguments.client_name,
            api_root=arguments.api_root,
            api_names=dict(arguments.api_name),
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
            api_root=arguments.api_root,
            api_names=dict(arguments.api_name),
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


def _generate_config(path: Path, *, check: bool) -> int:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        if config.get("version") != 1:
            raise ValueError("version must be 1")
        clients = config["clients"]
        if not isinstance(clients, list) or not clients:
            raise ValueError("clients must be a non-empty array of tables")
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"error: Invalid client config {path}: {error}", file=sys.stderr)
        return 2

    failed = False
    for index, client in enumerate(clients, start=1):
        try:
            arguments = _config_arguments(path.parent, client, check=check)
        except (KeyError, TypeError, ValueError) as error:
            print(f"error: Invalid clients[{index}]: {error}", file=sys.stderr)
            failed = True
            continue
        failed = _generate_one(arguments) != 0 or failed
    return 1 if failed else 0


def _config_arguments(
    base: Path,
    client: object,
    *,
    check: bool,
) -> argparse.Namespace:
    if not isinstance(client, dict):
        raise TypeError("entry must be a table")
    language = client["language"]
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES!r}")
    api_names = client.get("api_names", {})
    if not isinstance(api_names, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in api_names.items()
    ):
        raise TypeError("api_names must be a string-to-string table")
    return argparse.Namespace(
        schema=base / _config_string(client, "schema"),
        language=language,
        output=base / _config_string(client, "output"),
        package=client.get("package"),
        client_name=client.get("client_name"),
        transport_module=client.get("transport_module", "../transport"),
        api_root=client.get("api_root"),
        api_name=list(api_names.items()),
        check=check,
    )


def _config_string(client: dict[str, object], key: str) -> str:
    value = client[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


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
            "Render an RpcApp or OpenRpcContract as the contract "
            "clients are generated from. The source is named as "
            "module:attribute and imported from the current directory."
        ),
    )
    schema.add_argument(
        "source", help="RpcApp or OpenRpcContract, as module:attribute."
    )
    schema.add_argument(
        "--output",
        type=Path,
        required=True,
        help="File the contract is written to.",
    )
    schema.add_argument(
        "--title",
        help="Title of the API (required unless the source is an OpenRpcContract).",
    )
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
    generate.add_argument(
        "schema", nargs="?", type=Path, help="OpenRPC document to read."
    )
    generate.add_argument(
        "--language",
        choices=LANGUAGES,
        help="Target language for the generated client.",
    )
    generate.add_argument(
        "--output",
        type=Path,
        help="Directory of the generated package.",
    )
    generate.add_argument(
        "--config",
        type=Path,
        help="TOML manifest describing multiple client generation jobs.",
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
        help="Name of the generated client class (default: derived from info.title).",
    )
    generate.add_argument(
        "--api-root",
        help="Explicit common wire prefix to omit from the public API tree.",
    )
    generate.add_argument(
        "--api-name",
        action="append",
        default=[],
        type=_name_mapping,
        metavar="PATH=NAME",
        help="Rename an API path or segment; repeatable.",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="Report out-of-date files instead of writing them.",
    )


def _name_mapping(value: str) -> tuple[str, str]:
    path, separator, name = value.partition("=")
    if not separator or not path or not name:
        raise argparse.ArgumentTypeError(f"Expected path=name, got {value!r}")
    return path, name


if __name__ == "__main__":
    raise SystemExit(main())
