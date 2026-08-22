import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pyrpckit.codegen import PythonClientOptions, generate_python_client
from pyrpckit.schema import render_openrpc
from showcase.app.api import PROTOCOL

SHOWCASE = Path(__file__).parents[2] / "showcase"
SCHEMA = SHOWCASE / "spec" / "calculator.openrpc.json"
CLIENT = SHOWCASE / "client"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the FastAPI showcase contract and client."
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
            package="showcase.client",
            client_name="CalculatorClient",
            source=SCHEMA.name,
        ),
        check=arguments.check,
    )
    paths = ((SCHEMA,) if schema_changed else ()) + changed
    if arguments.check:
        for path in paths:
            print(f"Out of date: {path.relative_to(SHOWCASE)}")
    else:
        for path in paths:
            print(f"Wrote {path.relative_to(SHOWCASE)}")
    if not paths:
        print("Showcase contract and client are up to date")
    return int(arguments.check and bool(paths))


if __name__ == "__main__":
    raise SystemExit(main())
