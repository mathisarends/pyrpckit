from copy import deepcopy
from typing import Any

from pyrpckit.codegen import render_python_client, render_typescript_client
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.codegen.typescript import TypeScriptClientOptions


def test_nested_apis_share_top_level_namespace_modules(
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
        "namespaces/tasks.py",
    }
    assert "class TasksStatus:" in python_files["namespaces/tasks.py"]
    assert "class Tasks:" in python_files["namespaces/tasks.py"]
    assert "self.status = TasksStatus(rpc)" in python_files["namespaces/tasks.py"]
    assert "from task_client.namespaces.tasks import Tasks" in python_files["client.py"]
    assert "task_client.namespaces.tasks.status" not in "".join(python_files.values())
    assert {name for name in typescript_files if name.startswith("namespaces/")} == {
        "namespaces/index.ts",
        "namespaces/tasks.ts",
    }
    assert "export class TasksStatus" in typescript_files["namespaces/tasks.ts"]
    assert "export class Tasks" in typescript_files["namespaces/tasks.ts"]
    assert (
        "this.status = new TasksStatus(rpc);" in typescript_files["namespaces/tasks.ts"]
    )
    assert 'from "../core"' in typescript_files["namespaces/tasks.ts"]
    assert 'from "./status"' not in typescript_files["namespaces/tasks.ts"]
