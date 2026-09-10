import json

from pyrpckit import RpcApp, RpcModel, RpcRouter
from pyrpckit.schema import render_openrpc


class SearchParams(RpcModel):
    project_id: str
    query: str
    max_results: int = 10


class SearchResult(RpcModel):
    items: list[str]
    next_page_token: str | None = None


router = RpcRouter(namespace="search", tags=("search",))


@router.method()
async def run(params: SearchParams) -> SearchResult:
    return SearchResult(items=[])


APP = RpcApp(version=3)
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
