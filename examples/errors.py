import asyncio

import pyrpckit as rpc


class DivideParams(rpc.RpcModel):
    dividend: float
    divisor: float


class Quotient(rpc.RpcModel):
    value: float


class DivisionByZero(rpc.RpcError):
    code = -32001
    message = "Division by zero"


router = rpc.RpcRouter(prefix="calculator", tags=("calculator",))


class CalculatorRpc:
    @router.method(errors=(DivisionByZero,))
    async def divide(self, params: DivideParams) -> Quotient:
        if params.divisor == 0:
            raise DivisionByZero()
        return Quotient(value=params.dividend / params.divisor)


async def main() -> None:
    app = rpc.RpcApp()
    app.include_router(router)
    server = app.bind(CalculatorRpc())
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
