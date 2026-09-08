from pydantic import BaseModel


class BinaryOperationParams(BaseModel):
    left: float
    right: float


class CalculationResult(BaseModel):
    value: float
