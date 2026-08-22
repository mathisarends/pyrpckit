import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions

LANGUAGES = ("python",)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    document = json.loads(arguments.schema.read_text(encoding="utf-8"))
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
    if arguments.check:
        return _report_check(changed)
    for path in changed:
        print(f"Wrote {path}")
    if not changed:
        print("Client is up to date")
    return 0


def _report_check(changed: Sequence[Path]) -> int:
    if not changed:
        print("Client is up to date")
        return 0
    for path in changed:
        print(f"Out of date: {path}")
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyrpckit",
        description="Generate typed clients from an OpenRPC document.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
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
        "--client-name",
        default="RpcClient",
        help="Name of the generated client class (default: RpcClient).",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="Report out-of-date files instead of writing them.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
