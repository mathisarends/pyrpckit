import asyncio

from showcase.client import CalculatorClient
from showcase.client.transport import HttpJsonRpcTransport


async def call_api() -> None:
    transport = HttpJsonRpcTransport("http://127.0.0.1:8000/rpc")
    async with CalculatorClient(transport) as client:
        result = await client.calculator.divide(left=84, right=2)
        print(result.value)


if __name__ == "__main__":
    asyncio.run(call_api())
