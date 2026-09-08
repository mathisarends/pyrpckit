import pyrpckit as rpc

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationResult,
)

router = rpc.RpcRouter(prefix="calculator", tags=("calculator",))


class CalculatorRpc:
    @router.method("add")
    async def add(self, params: BinaryOperationParams) -> CalculationResult:
        return CalculationResult(value=_rounded(params.left + params.right, params))

    @router.method("divide", errors=(DivisionByZero,))
    async def divide(self, params: BinaryOperationParams) -> CalculationResult:
        if params.right == 0:
            raise DivisionByZero()
        return CalculationResult(value=_rounded(params.left / params.right, params))


def _rounded(value: float, params: BinaryOperationParams) -> float:
    if params.decimal_places is None:
        return value
    return round(value, params.decimal_places)
