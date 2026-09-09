from typing import Literal

import pyrpckit as rpc
from pyrpckit import RpcApp, RpcModel, RpcNotification, RpcRouter


@rpc.event
class JobStarted(RpcModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


@rpc.event
class JobFinished(RpcModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobEvent = JobStarted | JobFinished

router = RpcRouter(namespace="jobs", tags=("jobs",))
router.event(
    "changed",
    JobEvent,
    summary="Publish job lifecycle changes.",
)
APP = RpcApp()
APP.include_router(router)


def main() -> None:
    message = RpcNotification(
        method="jobs.changed",
        params=JobStarted(job_id="job-42"),
    )
    print(message.model_dump_json(indent=2))
    print(f"Known events: {[event.name for event in APP.protocol.events]}")


if __name__ == "__main__":
    main()
