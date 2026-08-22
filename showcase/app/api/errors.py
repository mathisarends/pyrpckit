import pyrpckit as rpc


class DivisionByZero(rpc.RpcError):
    code = -32001
    message = "Cannot divide by zero"
