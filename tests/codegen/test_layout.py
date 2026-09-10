from copy import deepcopy
from typing import Any

from pyrpckit.codegen import render_python_client, render_typescript_client
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.codegen.typescript import TypeScriptClientOptions


def test_nested_typescript_api_files_mirror_the_python_package_structure(
    document: dict[str, Any],
) -> None:
    nested = deepcopy(document)
    first = nested["methods"][0]
    second = deepcopy(first)
    first["name"] = "tasks.list"
    second["name"] = "tasks.status.set"
    nested["methods"] = [first, second]
    nested["x-rpc-notifications"] = []

    python_files = render_python_client(
        nested,
        PythonClientOptions(package="task_client"),
    )
    typescript_files = render_typescript_client(
        nested,
        TypeScriptClientOptions(client_name="TaskClient"),
    )

    assert {name for name in python_files if name.startswith("namespaces/")} == {
        "namespaces/tasks/__init__.py",
        "namespaces/tasks/status.py",
    }
    assert {name for name in typescript_files if name.startswith("namespaces/")} == {
        "namespaces/index.ts",
        "namespaces/tasks/index.ts",
        "namespaces/tasks/status.ts",
    }
