import json
from pathlib import Path

import pytest

from pyrpckit.codegen.cli import main
from pyrpckit.schema import render_openrpc
from pyrpckit.schema.export import (
    ProtocolReferenceError,
    load_protocol,
    render_contract,
)

from .conftest import GREETING_PROTOCOL

REFERENCE = "tests.conftest:GREETING_PROTOCOL"


def _schema(output: Path, *arguments: str) -> int:
    return main(
        ["schema", REFERENCE, "--output", str(output), "--title", "Greeting API"]
        + list(arguments)
    )


def test_a_reference_resolves_to_the_protocol() -> None:
    assert load_protocol(REFERENCE) is GREETING_PROTOCOL


@pytest.mark.parametrize(
    "reference",
    [
        "tests.conftest",
        ":GREETING_PROTOCOL",
        "tests.conftest:",
        "tests.nowhere:GREETING_PROTOCOL",
        "tests.conftest:GREETING_FEATURE",
    ],
)
def test_an_unusable_reference_is_reported(reference: str) -> None:
    with pytest.raises(ProtocolReferenceError):
        load_protocol(reference)


def test_the_contract_is_rendered_as_indented_json() -> None:
    contract = render_contract(GREETING_PROTOCOL, title="Greeting API")

    assert contract.endswith("\n")
    assert json.loads(contract) == render_openrpc(
        GREETING_PROTOCOL, title="Greeting API"
    )


def test_the_schema_command_writes_the_contract(tmp_path: Path) -> None:
    output = tmp_path / "spec" / "greeting.openrpc.json"

    assert _schema(output) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == render_openrpc(
        GREETING_PROTOCOL, title="Greeting API"
    )


def test_the_description_reaches_the_contract(tmp_path: Path) -> None:
    output = tmp_path / "greeting.openrpc.json"
    _schema(output, "--description", "Greets people by name.")

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["info"]["description"] == "Greets people by name."


def test_the_schema_command_records_the_servers(tmp_path: Path) -> None:
    output = tmp_path / "greeting.openrpc.json"
    _schema(output, "--server", "local=ws://127.0.0.1:8000/rpc")

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["servers"] == [{"name": "local", "url": "ws://127.0.0.1:8000/rpc"}]


def test_check_reports_a_missing_contract(tmp_path: Path) -> None:
    output = tmp_path / "greeting.openrpc.json"

    assert _schema(output, "--check") == 1
    assert not output.exists()


def test_check_accepts_an_up_to_date_contract(tmp_path: Path) -> None:
    output = tmp_path / "greeting.openrpc.json"
    _schema(output)

    assert _schema(output, "--check") == 0


def test_an_unusable_reference_fails_the_command(tmp_path: Path) -> None:
    arguments = [
        "schema",
        "tests.conftest",
        "--output",
        str(tmp_path / "greeting.openrpc.json"),
        "--title",
        "Greeting API",
    ]

    assert main(arguments) == 2
