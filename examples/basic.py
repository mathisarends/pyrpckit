import asyncio

from pyrpckit import RpcApp, RpcModel, RpcRouter


class GreetParams(RpcModel):
    name: str


class Greeting(RpcModel):
    text: str


router = RpcRouter(namespace="greeting", tags=("greeting",))


class GreetingRpc:
    @router.method
    async def say(self, params: GreetParams) -> Greeting:
        return Greeting(text=f"Hello, {params.name}!")


async def main() -> None:
    app = RpcApp()
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
