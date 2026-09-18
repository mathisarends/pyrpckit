import asyncio
from dataclasses import dataclass

from pyrpckit import Inject, RpcChannel, RpcModel, RpcService
from pyrpckit.testing import RpcTestClient


class GreetParams(RpcModel):
    name: str


class Greeting(RpcModel):
    text: str


@dataclass(frozen=True)
class Greeter:
    salutation: str


router = RpcChannel("greeting")


@router.method()
async def say(params: GreetParams, greeter: Inject[Greeter]) -> Greeting:
    return Greeting(text=f"{greeter.salutation}, {params.name}!")


async def main() -> None:
    rpc = RpcService()
    rpc.socket("/rpc", channels=(router,))
    async with RpcTestClient(
        rpc, "/rpc", context={Greeter: Greeter("Hello")}
    ) as client:
        print(await client.request("greeting.say", {"name": "World"}))


if __name__ == "__main__":
    asyncio.run(main())
