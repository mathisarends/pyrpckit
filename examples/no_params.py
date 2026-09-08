import asyncio
import json

from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class NavigateParams(BaseModel):
    url: str


class NavigationState(BaseModel):
    url: str
    can_go_back: bool


router = rpc.RpcRouter(prefix="browser.nav", tags=("browser",))


class NavigationRpc:
    def __init__(self) -> None:
        self._history = ["about:blank"]

    @router.method("navigate")
    async def navigate(self, params: NavigateParams) -> None:
        self._history.append(params.url)

    @router.method("back")
    async def back(self) -> None:
        if len(self._history) > 1:
            self._history.pop()

    @router.method("state")
    async def state(self) -> NavigationState:
        return NavigationState(
            url=self._history[-1],
            can_go_back=len(self._history) > 1,
        )


async def main() -> None:
    app = rpc.RpcApp()
    app.include_router(router)
    server = app.bind(NavigationRpc())

    for request_id, (name, params) in enumerate(
        (
            ("browser.nav.navigate", {"url": "https://example.com"}),
            ("browser.nav.state", None),
            ("browser.nav.back", None),
            ("browser.nav.state", None),
            ("browser.nav.back", {"steps": 2}),
        ),
        start=1,
    ):
        request: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": name,
        }
        if params is not None:
            request["params"] = params
        response = await server.handle(request)
        assert response is not None
        print(f"{name} -> {response.model_dump_json()}")

    document = render_openrpc(server.protocol, title="Navigation")
    print()
    for method in document["methods"]:
        print(
            json.dumps(
                {
                    "name": method["name"],
                    "params": method["params"],
                    "result": method["result"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
