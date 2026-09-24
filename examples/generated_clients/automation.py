import asyncio
from collections.abc import AsyncIterator

from pyrpckit import (
    Inject,
    RpcBinaryInput,
    RpcBinaryOutput,
    RpcChannel,
    RpcConnectedClient,
    RpcError,
    RpcModel,
    RpcService,
    ServerVariable,
)


class Task(RpcModel):
    id: str
    title: str
    done: bool = False


class TaskList(RpcModel):
    tasks: list[Task]


class CreateTaskParams(RpcModel):
    title: str


class DeleteTaskParams(RpcModel):
    task_id: str


class TaskUpdated(RpcModel):
    task: Task


class ApprovalRequest(RpcModel):
    task_id: str
    action: str


class ApprovalDecision(RpcModel):
    approved: bool
    comment: str | None = None


class ApprovalUnavailableError(RpcError):
    rpc_code = -32030
    code = "approval_unavailable"


class Tab(RpcModel):
    id: str
    url: str


class OpenTabParams(RpcModel):
    url: str


class DialogRequest(RpcModel):
    tab_id: str
    message: str


class DialogAnswer(RpcModel):
    accepted: bool


class StartScreencastParams(RpcModel):
    quality: int = 80


tasks = RpcChannel("tasks")
browser = RpcChannel("browser")
tabs = browser.child("tabs")
dialogs = browser.child("dialogs")
screencast = browser.child("screencast")

_tasks: dict[str, Task] = {}
_updates: asyncio.Queue[Task] = asyncio.Queue()


@tasks.server.method("list", summary="List all tasks.")
async def list_tasks() -> TaskList:
    return TaskList(tasks=list(_tasks.values()))


@tasks.server.method(summary="Create a task.")
async def create(params: CreateTaskParams) -> Task:
    task = Task(id=str(len(_tasks) + 1), title=params.title)
    _tasks[task.id] = task
    await _updates.put(task)
    return task


@tasks.server.method(summary="Delete a task once the operator approves it.")
async def delete(params: DeleteTaskParams, client: Inject[RpcConnectedClient]) -> bool:
    decision = await client.call(
        approve,
        ApprovalRequest(task_id=params.task_id, action="delete"),
        timeout=30.0,
    )
    if decision.approved:
        _tasks.pop(params.task_id, None)
    return decision.approved


@tasks.server.event(summary="Stream task updates.")
async def updated() -> AsyncIterator[TaskUpdated]:
    while True:
        yield TaskUpdated(task=await _updates.get())


approve = tasks.client.method(
    "approve",
    params=ApprovalRequest,
    result=ApprovalDecision,
    raises=(ApprovalUnavailableError,),
    summary="Ask the operator to approve a task action.",
)


@tabs.server.method(summary="Open a browser tab.")
async def open(params: OpenTabParams) -> Tab:
    return Tab(id="tab-1", url=params.url)


confirm = dialogs.client.method(
    "confirm",
    params=DialogRequest,
    result=DialogAnswer,
    summary="Let the operator answer a confirm() dialog of a tab.",
)


@screencast.server.method(summary="Start the browser screencast.")
async def start(params: StartScreencastParams) -> None:
    return None


@screencast.server.stream(
    content_type="image/jpeg",
    summary="Raw screencast frames as binary WebSocket messages.",
)
async def frames() -> AsyncIterator[bytes]:
    yield b"\xff\xd8\xff\xd9"


@screencast.server.stream(
    input_content_type="video/webm",
    summary="Upload a recorded screencast as binary WebSocket messages.",
)
async def upload(recording: Inject[RpcBinaryInput]) -> None:
    async for _chunk in recording:
        pass


@screencast.server.stream(
    content_type="image/jpeg",
    input_content_type="application/x-input-event",
    summary="Receive frames while sending input events back.",
)
async def control(
    events: Inject[RpcBinaryInput],
    output: Inject[RpcBinaryOutput],
) -> None:
    async for _event in events:
        await output.send(b"\xff\xd8\xff\xd9")


service = RpcService()
service.socket("/automation/rpc", channels=(tasks,), name="production")
service.socket("/browser/rpc", channels=(tabs, dialogs), name="browser")
service.socket(
    "/browser/stream",
    channels=(screencast,),
    name="streaming",
    subprotocol="pyrpckit.jsonrpc",
)
service.stream("/browser/screencast", frames)
service.stream("/browser/screencast/upload", upload)
service.stream("/browser/screencast/control", control)

contract = service.contract(
    title="Automation",
    base_url="wss://{host}",
    variables={"host": ServerVariable(default="api.example.com")},
)
