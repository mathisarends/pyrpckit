from typing import Literal

from pyrpckit import RpcModel


class BinaryOperationParams(RpcModel):
    left: float
    right: float
    decimal_places: int | None = None


class CalculationResult(RpcModel):
    value: float


class CalculationStarted(RpcModel):
    type: Literal["calculation.started"] = "calculation.started"
    operation: Literal["add", "divide"]


class CalculationCompleted(RpcModel):
    type: Literal["calculation.completed"] = "calculation.completed"
    operation: Literal["add", "divide"]
    result: CalculationResult


type CalculationUpdate = CalculationStarted | CalculationCompleted
