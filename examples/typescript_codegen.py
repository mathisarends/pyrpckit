from enum import StrEnum
from pathlib import Path
from typing import Literal

import pyrpckit as rpc
from pyrpckit.codegen import generate_typescript_client
from pyrpckit.codegen.typescript import TypeScriptClientOptions
from pyrpckit.schema import render_openrpc


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class Task(rpc.RpcModel):
    id: str
    title: str
    status: TaskStatus


class TaskList(rpc.RpcModel):
    tasks: list[Task]


class CreateTaskParams(rpc.RpcModel):
    title: str


class SetTaskStatusParams(rpc.RpcModel):
    task_id: str
    status: TaskStatus


@rpc.event
class TaskCreated(rpc.RpcModel):
    type: Literal["task.created"] = "task.created"
    task: Task


@rpc.event
class TaskStatusChanged(rpc.RpcModel):
    type: Literal["task.status_changed"] = "task.status_changed"
    task: Task


type TaskEvent = TaskCreated | TaskStatusChanged


router = rpc.RpcRouter(prefix="tasks", tags=("tasks",))


class TaskRpc:
    @router.method("list")
    async def list_tasks(self) -> TaskList: ...

    @router.method("create")
    async def create_task(self, params: CreateTaskParams) -> Task: ...

    @router.method("status.set")
    async def set_status(self, params: SetTaskStatusParams) -> Task: ...


router.event("changed", TaskEvent)
APP = rpc.RpcApp()
APP.include_router(router)

OUTPUT = Path(__file__).parent / "typescript_client" / "generated"


def main() -> None:
    document = render_openrpc(APP.protocol, title="Task API")
    changed = generate_typescript_client(
        document,
        OUTPUT,
        TypeScriptClientOptions(
            client_name="TaskClient",
            transport_module="../transport",
            source="typescript_codegen.py",
        ),
    )
    for path in changed:
        print(f"Wrote {path.relative_to(Path(__file__).parent)}")
    if not changed:
        print("TypeScript client is up to date")


if __name__ == "__main__":
    main()
