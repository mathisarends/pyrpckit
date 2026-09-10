from typing import Literal

from pyrpckit import RpcApp, RpcModel, RpcRouter


class JobStarted(RpcModel):
    type: Literal["job.started"] = "job.started"
    job_id: str


class JobFinished(RpcModel):
    type: Literal["job.finished"] = "job.finished"
    job_id: str
    succeeded: bool


type JobUpdate = JobStarted | JobFinished

router = RpcRouter(namespace="jobs", tags=("jobs",))


job_changed = router.notification(
    "changed",
    payload=JobUpdate,
    summary="Publish job lifecycle changes.",
)


APP = RpcApp()
APP.include_router(router)


def main() -> None:
    message = job_changed(JobStarted(job_id="job-42"))
    print(message.model_dump_json(indent=2))
    names = [item.name for item in APP.protocol.notification_types]
    print(f"Known notification types: {names}")


if __name__ == "__main__":
    main()
