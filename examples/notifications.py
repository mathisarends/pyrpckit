from collections.abc import AsyncIterator
from typing import Literal

from pyrpckit import Inject, RpcApp, RpcModel, RpcRouter


class JobStarted(RpcModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


class JobFinished(RpcModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobUpdate = JobStarted | JobFinished

router = RpcRouter(namespace="jobs", tags=("jobs",))


class JobEvents:
    async def subscribe(self) -> AsyncIterator[JobUpdate]:
        yield JobStarted(job_id="job-42")


@router.notification(
    "changed",
    payload=JobUpdate,
    summary="Publish job lifecycle changes.",
)
async def job_changes(events: Inject[JobEvents]) -> AsyncIterator[JobUpdate]:
    async for event in events.subscribe():
        yield event


APP = RpcApp()
APP.include_router(router)


def main() -> None:
    notification = APP.protocol.notifications[0]
    print(f"Notification source: {notification.name} -> {notification.payload}")
    names = [item.name for item in APP.protocol.notification_types]
    print(f"Known notification types: {names}")


if __name__ == "__main__":
    main()
