import pyrpckit as rpc

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationResult,
    CalculatorMethod,
)


class CalculatorRpc:
    @rpc.method(CalculatorMethod.ADD)
    async def add(self, params: BinaryOperationParams) -> CalculationResult:
        """Add two numbers."""
        return CalculationResult(value=params.left + params.right)

    @rpc.method(CalculatorMethod.DIVIDE, errors=(DivisionByZero,))
    async def divide(self, params: BinaryOperationParams) -> CalculationResult:
        """Divide the left number by the right number."""
        if params.right == 0:
            raise DivisionByZero()
        return CalculationResult(value=params.left / params.right)
