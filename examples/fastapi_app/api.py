from enum import StrEnum

import pyrpckit as rpc
from pydantic import BaseModel


class CalculatorMethod(StrEnum):
    ADD = "calculator.add"
    DIVIDE = "calculator.divide"


class BinaryOperationParams(BaseModel):
    left: float
    right: float


class CalculationResult(BaseModel):
    value: float


class DivisionByZero(rpc.RpcError):
    code = -32001
    message = "Cannot divide by zero"


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


CALCULATOR = rpc.feature("calculator", handlers=(CalculatorRpc,))
PROTOCOL = rpc.RpcProtocol(CALCULATOR, version=1)
