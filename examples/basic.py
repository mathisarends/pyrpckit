import asyncio

from pydantic import BaseModel

import pyrpckit as rpc


class GreetParams(BaseModel):
    name: str


class Greeting(BaseModel):
    text: str


router = rpc.RpcRouter(prefix="greeting", tags=("greeting",))


class GreetingRpc:
    @router.method
    async def say(self, params: GreetParams) -> Greeting:
        return Greeting(text=f"Hello, {params.name}!")


async def main() -> None:
    app = rpc.RpcApp()
    app.include_router(router)
    server = app.bind(GreetingRpc())
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "greeting.say",
            "params": {"name": "World"},
        }
    )
    assert response is not None
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
