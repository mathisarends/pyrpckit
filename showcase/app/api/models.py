from enum import StrEnum

from pydantic import BaseModel


class CalculatorMethod(StrEnum):
    ADD = "calculator.add"
    DIVIDE = "calculator.divide"


class BinaryOperationParams(BaseModel):
    left: float
    right: float


class CalculationResult(BaseModel):
    value: float
