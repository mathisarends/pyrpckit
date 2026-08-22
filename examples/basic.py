import asyncio

from pydantic import BaseModel

import pyrpckit as rpc


class GreetParams(BaseModel):
    name: str


class Greeting(BaseModel):
    text: str


class GreetingRpc(rpc.RpcHandler):
    @rpc.method("greeting.say")
    async def say(self, params: GreetParams) -> Greeting:
        """Greet someone by name."""
        return Greeting(text=f"Hello, {params.name}!")


async def main() -> None:
    server = rpc.RpcServer(GreetingRpc())
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
