from typing import Literal

from pydantic import BaseModel

import pyrpckit as rpc


@rpc.event
class JobStarted(BaseModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


@rpc.event
class JobFinished(BaseModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobEvent = JobStarted | JobFinished

router = rpc.RpcRouter(prefix="jobs", tags=("jobs",))
router.event(
    "changed",
    JobEvent,
    summary="Publish job lifecycle changes.",
)
APP = rpc.RpcApp()
APP.include_router(router)


def main() -> None:
    message = rpc.RpcNotification(
        method="jobs.changed",
        params=JobStarted(job_id="job-42"),
    )
    print(message.model_dump_json(indent=2))
    print(f"Known events: {[event.name for event in APP.protocol.events]}")


if __name__ == "__main__":
    main()
