from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.codegen import generate_typescript_client
from pyrpckit.codegen.typescript import TypeScriptClientOptions
from pyrpckit.schema import render_openrpc


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class Task(BaseModel):
    id: str
    title: str
    status: TaskStatus


class TaskList(BaseModel):
    tasks: list[Task]


class CreateTaskParams(BaseModel):
    title: str


class SetTaskStatusParams(BaseModel):
    task_id: str
    status: TaskStatus


@rpc.event
class TaskCreated(BaseModel):
    type: Literal["task.created"] = "task.created"
    task: Task


@rpc.event
class TaskStatusChanged(BaseModel):
    type: Literal["task.status_changed"] = "task.status_changed"
    task: Task


type TaskEvent = TaskCreated | TaskStatusChanged


class TaskRpc(rpc.RpcHandler):
    @rpc.method("tasks.list")
    async def list_tasks(self) -> TaskList: ...

    @rpc.method("tasks.create")
    async def create_task(self, params: CreateTaskParams) -> Task: ...

    @rpc.method("tasks.status.set")
    async def set_status(self, params: SetTaskStatusParams) -> Task: ...


TASKS = rpc.feature(
    "tasks",
    handlers=(TaskRpc,),
    notifications=(rpc.notification("tasks.changed", TaskEvent),),
)

OUTPUT = Path(__file__).parent / "typescript_client" / "generated"


def main() -> None:
    document = render_openrpc(rpc.RpcProtocol(TASKS), title="Task API")
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
