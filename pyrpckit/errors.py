from enum import IntEnum
from typing import Any

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
    """An error that is part of the protocol contract.

    Subclasses declare their wire ``code`` and default ``message`` as class
    attributes, so a method can list them in ``@method(errors=...)`` and the
    server can serialise them without a mapper::

        class AutomationNotFound(RpcError):
            code = -32004
            message = "Automation not found"

    A one-off error can pass ``code`` directly instead of declaring a subclass.
    """

    code: int
    message: str

    def __init__(self, message: str | None = None, *, code: int | None = None) -> None:
        declared_code = code if code is not None else getattr(type(self), "code", None)
        if declared_code is None:
            raise ProtocolDefinitionError(
                f"RPC error {type(self).__name__} declares no code"
            )
        self.code = int(declared_code)
        self.message = (
            message or getattr(type(self), "message", None) or error_message(self.code)
        )
        super().__init__(self.message)


class RpcParseError(RpcError):
    code = RpcErrorCode.PARSE_ERROR
    message = "Parse error"


class RpcInvalidRequestError(RpcError):
    code = RpcErrorCode.INVALID_REQUEST
    message = "Invalid request"


class RpcMethodNotFoundError(RpcError):
    code = RpcErrorCode.METHOD_NOT_FOUND
    message = "Method not found"

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


class RpcInvalidParamsError(RpcError):
    code = RpcErrorCode.INVALID_PARAMS
    message = "Invalid params"

    def __init__(self, error: ValidationError) -> None:
        super().__init__(_invalid_params_message(error))
        self.validation_error = error


class RpcInternalError(RpcError):
    code = RpcErrorCode.INTERNAL_ERROR
    message = "Internal error"


class ProtocolDefinitionError(Exception):
    """A handler, event, or feature is not a valid protocol definition."""


def error_message(code: int) -> str:
    member = code if isinstance(code, IntEnum) else _reserved_member(code)
    if member is None:
        return f"Error {int(code)}"
    return member.name.replace("_", " ").capitalize()


def declared_error(error: Any) -> type[RpcError]:
    """Validate that ``error`` is an ``RpcError`` subclass carrying a code."""
    if not (isinstance(error, type) and issubclass(error, RpcError)):
        raise ProtocolDefinitionError(
            f"Declared RPC error must be an RpcError subclass: {error!r}"
        )
    if getattr(error, "code", None) is None:
        raise ProtocolDefinitionError(f"RPC error {error.__name__} declares no code")
    return error


def _reserved_member(code: int) -> RpcErrorCode | None:
    try:
        return RpcErrorCode(code)
    except ValueError:
        return None


def _invalid_params_message(error: ValidationError) -> str:
    issue = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in ("params", *issue["loc"]))
    return f"Invalid params at {location}: {issue['msg']}"
