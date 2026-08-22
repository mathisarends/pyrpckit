import pyrpckit as rpc

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.handler import CalculatorRpc
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationResult,
    CalculatorMethod,
)

CALCULATOR = rpc.feature("calculator", handlers=(CalculatorRpc,))
PROTOCOL = rpc.RpcProtocol(CALCULATOR, version=1)

__all__ = [
    "CALCULATOR",
    "PROTOCOL",
    "BinaryOperationParams",
    "CalculationResult",
    "CalculatorMethod",
    "CalculatorRpc",
    "DivisionByZero",
]
