import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pyrpckit.codegen import PythonClientOptions, generate_python_client
from pyrpckit.codegen.writer import write_files
from pyrpckit.schema.export import render_contract
from showcase.app.api import PROTOCOL

SHOWCASE = Path(__file__).parents[2] / "showcase"
SCHEMA = SHOWCASE / "spec" / "calculator.openrpc.json"
CLIENT = SHOWCASE / "client"
TITLE = "Calculator API"
DESCRIPTION = "A small pyrpckit API served through FastAPI."
SERVERS = ({"name": "local", "url": "http://127.0.0.1:8000/rpc"},)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the FastAPI showcase contract and client."
    )
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)

    contract = render_contract(
        PROTOCOL,
        title=TITLE,
        description=DESCRIPTION,
        servers=SERVERS,
    )
    paths = write_files(
        SCHEMA.parent,
        {SCHEMA.name: contract},
        check=arguments.check,
    )
    paths += generate_python_client(
        json.loads(contract),
        CLIENT,
        PythonClientOptions(
            package="showcase.client",
            client_name="CalculatorClient",
            source=SCHEMA.name,
        ),
        check=arguments.check,
    )
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
