import asyncio

from pydantic import BaseModel

import pyrpckit as rpc


class DivideParams(BaseModel):
    dividend: float
    divisor: float


class Quotient(BaseModel):
    value: float


class DivisionByZero(rpc.RpcError):
    code = -32001
    message = "Division by zero"


class CalculatorRpc(rpc.RpcHandler):
    @rpc.method("calculator.divide", errors=(DivisionByZero,))
    async def divide(self, params: DivideParams) -> Quotient:
        if params.divisor == 0:
            raise DivisionByZero()
        return Quotient(value=params.dividend / params.divisor)


async def main() -> None:
    server = rpc.RpcServer(CalculatorRpc())
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "calculator.divide",
            "params": {"dividend": 10, "divisor": 0},
        }
    )
    assert isinstance(response, rpc.RpcFailure)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
