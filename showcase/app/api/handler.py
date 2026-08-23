import pyrpckit as rpc

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationResult,
    CalculatorMethod,
)


class CalculatorRpc(rpc.RpcHandler):
    @rpc.method(CalculatorMethod.ADD)
    async def add(self, params: BinaryOperationParams) -> CalculationResult:
        return CalculationResult(value=params.left + params.right)

    @rpc.method(CalculatorMethod.DIVIDE, errors=(DivisionByZero,))
    async def divide(self, params: BinaryOperationParams) -> CalculationResult:
        if params.right == 0:
            raise DivisionByZero()
        return CalculationResult(value=params.left / params.right)
