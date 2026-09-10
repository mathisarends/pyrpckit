from pyrpckit import OpenRpcContract, OpenRpcServer, RpcApp

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.handler import CalculatorRpc, router
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationCompleted,
    CalculationResult,
    CalculationStarted,
    CalculationUpdate,
)

CALCULATOR_RPC = RpcApp(version=1)
CALCULATOR_RPC.include_router(router)
CALCULATOR_RPC_CONTRACT = OpenRpcContract(
    app=CALCULATOR_RPC,
    title="Calculator API",
    description="A small pyrpckit API served through FastAPI.",
    servers=(OpenRpcServer(name="local", url="http://127.0.0.1:8000/rpc"),),
)

__all__ = [
    "CALCULATOR_RPC",
    "CALCULATOR_RPC_CONTRACT",
    "BinaryOperationParams",
    "CalculationCompleted",
    "CalculationResult",
    "CalculationStarted",
    "CalculationUpdate",
    "CalculatorRpc",
    "DivisionByZero",
]
