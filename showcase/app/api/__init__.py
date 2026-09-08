import pyrpckit as rpc

from showcase.app.api.errors import DivisionByZero
from showcase.app.api.handler import CalculatorRpc, router
from showcase.app.api.models import (
    BinaryOperationParams,
    CalculationResult,
)

CALCULATOR_RPC = rpc.RpcApp(version=1)
CALCULATOR_RPC.include_router(router)
CALCULATOR_RPC_CONTRACT = rpc.OpenRpcContract(
    app=CALCULATOR_RPC,
    title="Calculator API",
    description="A small pyrpckit API served through FastAPI.",
    servers=(rpc.OpenRpcServer(name="local", url="http://127.0.0.1:8000/rpc"),),
)

__all__ = [
    "CALCULATOR_RPC",
    "CALCULATOR_RPC_CONTRACT",
    "BinaryOperationParams",
    "CalculationResult",
    "CalculatorRpc",
    "DivisionByZero",
]
