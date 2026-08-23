import json

from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.schema import render_json_schema, render_openrpc


class SearchParams(BaseModel):
    query: str
    limit: int = 10


class SearchResult(BaseModel):
    items: list[str]


class SearchRpc(rpc.RpcHandler):
    @rpc.method("search.run")
    async def search(self, params: SearchParams) -> SearchResult:
        return SearchResult(items=[])


PROTOCOL = rpc.RpcProtocol(
    rpc.feature("search", handlers=(SearchRpc,)),
    version=3,
)


def main() -> None:
    json_schema = render_json_schema(
        PROTOCOL,
        title="Search Protocol",
        schema_id="https://example.test/search.schema.json",
    )
    openrpc = render_openrpc(
        PROTOCOL,
        title="Search API",
        servers=({"name": "local", "url": "ws://localhost:8000/rpc"},),
    )
    print("JSON Schema frames:")
    print(json.dumps(json_schema["oneOf"], indent=2))
    print("\nOpenRPC method:")
    print(json.dumps(openrpc["methods"][0], indent=2))


if __name__ == "__main__":
    main()
