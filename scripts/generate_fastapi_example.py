import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from examples.fastapi_app.api import PROTOCOL
from pyrpckit.codegen import PythonClientOptions, generate_python_client
from pyrpckit.schema import render_openrpc

EXAMPLE = Path(__file__).parents[1] / "examples" / "fastapi_app"
SCHEMA = EXAMPLE / "calculator.openrpc.json"
CLIENT = EXAMPLE.parent / "calculator_client"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the FastAPI example contract and client."
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)

    document = render_openrpc(
        PROTOCOL,
        title="Calculator API",
        description="A small pyrpckit API served through FastAPI.",
        servers=({"name": "local", "url": "http://127.0.0.1:8000/rpc"},),
    )
    rendered_schema = json.dumps(document, indent=2) + "\n"
    schema_changed = not SCHEMA.exists() or SCHEMA.read_text(encoding="utf-8") != rendered_schema
    if schema_changed and not arguments.check:
        SCHEMA.write_text(rendered_schema, encoding="utf-8")

    changed = generate_python_client(
        document,
        CLIENT,
        PythonClientOptions(
            package="examples.calculator_client",
            client_name="CalculatorClient",
            source=SCHEMA.name,
        ),
        check=arguments.check,
    )
    paths = ((SCHEMA,) if schema_changed else ()) + changed
    if arguments.check:
        for path in paths:
            print(f"Out of date: {path.relative_to(EXAMPLE.parent)}")
    else:
        for path in paths:
            print(f"Wrote {path.relative_to(EXAMPLE.parent)}")
    if not paths:
        print("Example contract and client are up to date")
    return int(arguments.check and bool(paths))


if __name__ == "__main__":
    raise SystemExit(main())
