import json

from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class SearchParams(BaseModel):
    project_id: str
    query: str
    max_results: int = 10


class SearchResult(BaseModel):
    items: list[str]
    next_page_token: str | None = None


router = rpc.RpcRouter(prefix="search", tags=("search",))


class SearchRpc:
    @router.method
    async def run(self, params: SearchParams) -> SearchResult:
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
