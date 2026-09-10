import asyncio
from dataclasses import dataclass

from pyrpckit import Inject, RpcChannel, RpcModel, RpcModule


class GreetParams(RpcModel):
    name: str


class Greeting(RpcModel):
    text: str


@dataclass(frozen=True)
class Greeter:
    salutation: str


router = RpcModule(namespace="greeting", tags=("greeting",))


@router.method()
async def say(params: GreetParams, greeter: Inject[Greeter]) -> Greeting:
    return Greeting(text=f"{greeter.salutation}, {params.name}!")


async def main() -> None:
    app = RpcChannel()
    app.include(router)
    server = app.server(context={Greeter: Greeter("Hello")})
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
