from pyrpckit import RpcModel


class BinaryOperationParams(RpcModel):
    left: float
    right: float
    decimal_places: int | None = None


class CalculationResult(RpcModel):
    value: float
