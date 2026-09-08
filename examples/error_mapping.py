import asyncio

from pydantic import BaseModel

import pyrpckit as rpc


class EmptyParams(BaseModel):
    pass


class UpstreamUnavailable(Exception):
    pass


router = rpc.RpcRouter(prefix="reports", tags=("reports",))


class ReportsRpc:
    @router.method("refresh")
    async def refresh(self, params: EmptyParams) -> None:
        raise UpstreamUnavailable("warehouse timed out")


def map_foreign_error(error: Exception) -> rpc.RpcError | None:
    if isinstance(error, UpstreamUnavailable):
        return rpc.RpcError(str(error), code=-32002)
    return None


async def main() -> None:
    app = rpc.RpcApp()
    app.include_router(router)
    server = app.bind(ReportsRpc(), error_mapper=map_foreign_error)
    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "reports.refresh"}
    )
    assert isinstance(response, rpc.RpcFailure)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
