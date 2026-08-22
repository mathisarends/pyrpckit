from typing import Any


class RpcClientError(Exception):
    """Base error raised by a generated client."""


class RpcRemoteError(RpcClientError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RpcTransportError(RpcClientError):
    """Raised when the connection cannot serve a request."""
