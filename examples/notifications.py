from typing import Literal

from pyrpckit import RpcApp, RpcModel, RpcNotification, RpcRouter


class JobStarted(RpcModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


class JobFinished(RpcModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobUpdate = JobStarted | JobFinished

router = RpcRouter(namespace="jobs", tags=("jobs",))


@router.notification("changed")
def job_changed() -> JobUpdate:
    """Publish job lifecycle changes."""


APP = RpcApp()
APP.include_router(router)


def main() -> None:
    message = RpcNotification(
        method="jobs.changed",
        params=JobStarted(job_id="job-42"),
    )
    print(message.model_dump_json(indent=2))
    names = [item.name for item in APP.protocol.notification_types]
    print(f"Known notification types: {names}")


if __name__ == "__main__":
    main()
