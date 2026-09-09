import asyncio

from pyrpckit import RpcApp, RpcError, RpcFailure, RpcModel, RpcRouter


class DivideParams(RpcModel):
    dividend: float
    divisor: float


class Quotient(RpcModel):
    value: float


class DivisionByZero(RpcError):
    code = -32001
    message = "Division by zero"


router = RpcRouter(namespace="calculator", tags=("calculator",))


class CalculatorRpc:
    @router.method(errors=(DivisionByZero,))
    async def divide(self, params: DivideParams) -> Quotient:
        if params.divisor == 0:
            raise DivisionByZero()
        return Quotient(value=params.dividend / params.divisor)


async def main() -> None:
    app = RpcApp()
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
    assert isinstance(response, RpcFailure)
    print(response.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
