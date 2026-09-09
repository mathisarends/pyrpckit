import asyncio

from pyrpckit import RpcApp, RpcError, RpcFailure, RpcRouter


class UpstreamUnavailable(Exception):
    pass


router = RpcRouter(prefix="reports", tags=("reports",))


class ReportsRpc:
    @router.method
    async def refresh(self) -> None:
        raise UpstreamUnavailable("warehouse timed out")


def map_foreign_error(error: Exception) -> RpcError | None:
    if isinstance(error, UpstreamUnavailable):
        return RpcError(str(error), code=-32002)
    return None


async def main() -> None:
    app = RpcApp()
    app.include_router(router)
    server = app.bind(ReportsRpc(), error_mapper=map_foreign_error)
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "reports.refresh"}
    )
    assert isinstance(response, RpcFailure)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
