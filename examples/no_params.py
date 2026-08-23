import asyncio
import json
from enum import StrEnum

from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class NavigationMethod(StrEnum):
    NAVIGATE = "browser.nav.navigate"
    BACK = "browser.nav.back"
    STATE = "browser.nav.state"


class NavigateParams(BaseModel):
    url: str


class NavigationState(BaseModel):
    url: str
    can_go_back: bool


class NavigationRpc(rpc.RpcHandler):
    def __init__(self) -> None:
        self._history = ["about:blank"]

    @rpc.method(NavigationMethod.NAVIGATE)
    async def navigate(self, params: NavigateParams) -> None:
        """Open a URL, answering with an empty result."""
        self._history.append(params.url)

    @rpc.method(NavigationMethod.BACK)
    async def back(self) -> None:
        """Go back, taking no params and answering with an empty result."""
        if len(self._history) > 1:
            self._history.pop()

    @rpc.method(NavigationMethod.STATE)
    async def state(self) -> NavigationState:
        """Report where the browser stands, taking no params."""
        return NavigationState(
            url=self._history[-1],
            can_go_back=len(self._history) > 1,
        )


async def main() -> None:
    server = rpc.RpcServer(NavigationRpc())

    for request_id, (name, params) in enumerate(
        (
            (NavigationMethod.NAVIGATE, {"url": "https://example.com"}),
            (NavigationMethod.STATE, None),
            (NavigationMethod.BACK, None),
            (NavigationMethod.STATE, None),
            (NavigationMethod.BACK, {"steps": 2}),
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
