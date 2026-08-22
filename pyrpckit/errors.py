from enum import IntEnum

from pydantic import ValidationError


class RpcErrorCode(IntEnum):
    """JSON-RPC 2.0 reserved error codes.

    Applications add their own codes in the implementation-defined server range
    (-32099 to -32000) by declaring a separate ``IntEnum``.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RpcMethodNotFoundError(RpcError):
    def __init__(self, name: str) -> None:
        super().__init__(RpcErrorCode.METHOD_NOT_FOUND, "Method not found")
        self.name = name


class RpcInvalidRequestError(RpcError):
    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(RpcErrorCode.INVALID_REQUEST, message)


class RpcInvalidParamsError(RpcError):
    def __init__(self, error: ValidationError) -> None:
        super().__init__(RpcErrorCode.INVALID_PARAMS, _invalid_params_message(error))
        self.validation_error = error


class ProtocolDefinitionError(Exception):
    """A handler, event, or feature is not a valid protocol definition."""


def error_message(code: int) -> str:
    member = code if isinstance(code, IntEnum) else _reserved_member(code)
    if member is None:
        return f"Error {int(code)}"
    return member.name.replace("_", " ").capitalize()


def _reserved_member(code: int) -> RpcErrorCode | None:
    try:
        return RpcErrorCode(code)
    except ValueError:
        return None


def _invalid_params_message(error: ValidationError) -> str:
    issue = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in ("params", *issue["loc"]))
    return f"Invalid params at {location}: {issue['msg']}"
