import json
from pathlib import Path
from typing import Any

import pytest

from pyrpckit.codegen.cli import main

from .conftest import PACKAGE


@pytest.fixture
def schema(document: dict[str, Any], tmp_path: Path) -> Path:
    path = tmp_path / "greeting.openrpc.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_generate_writes_the_package(schema: Path, tmp_path: Path) -> None:
    output = tmp_path / PACKAGE

    assert main(["generate", "python", str(schema), "--output", str(output)]) == 0
    assert (output / "client.py").exists()
    assert (output / "namespaces" / "greeting.py").exists()


def test_the_package_name_defaults_to_the_output_directory(
    schema: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / PACKAGE
    main(["generate", "python", str(schema), "--output", str(output)])

    assert f"from {PACKAGE}.models import" in (output / "namespaces" / "greeting.py").read_text(
        encoding="utf-8"
    )


def test_the_client_class_can_be_named(schema: Path, tmp_path: Path) -> None:
    output = tmp_path / PACKAGE
    main(
        [
            "generate",
            "python",
            str(schema),
            "--output",
            str(output),
            "--client-name",
            "GreetingClient",
        ]
    )

    assert "class GreetingClient:" in (output / "client.py").read_text(encoding="utf-8")


def test_check_fails_when_the_client_is_missing(schema: Path, tmp_path: Path) -> None:
    output = tmp_path / PACKAGE

    assert main(["generate", "python", str(schema), "--output", str(output), "--check"]) == 1
    assert not output.exists()


def test_check_passes_once_the_client_is_generated(
    schema: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / PACKAGE
    main(["generate", "python", str(schema), "--output", str(output)])

    assert main(["generate", "python", str(schema), "--output", str(output), "--check"]) == 0
