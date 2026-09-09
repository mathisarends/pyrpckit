from pyrpckit import RpcError


class DivisionByZero(RpcError):
    code = -32001
    message = "Cannot divide by zero"
