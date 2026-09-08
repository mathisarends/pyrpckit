import json

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class SearchParams(rpc.RpcModel):
    project_id: str
    query: str
    max_results: int = 10


class SearchResult(rpc.RpcModel):
    items: list[str]
    next_page_token: str | None = None


router = rpc.RpcRouter(prefix="search", tags=("search",))


class SearchRpc:
    @router.method("run")
    async def search(self, params: SearchParams) -> SearchResult:
        return SearchResult(items=[])


APP = rpc.RpcApp(version=3)
APP.include_router(router)


def main() -> None:
    openrpc = render_openrpc(
        APP.protocol,
        title="Search API",
        servers=({"name": "local", "url": "ws://localhost:8000/rpc"},),
    )
    print("OpenRPC method:")
    print(json.dumps(openrpc["methods"][0], indent=2))


if __name__ == "__main__":
    main()
