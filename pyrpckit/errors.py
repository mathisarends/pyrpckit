import inspect
import re
from contextlib import suppress
from enum import IntEnum
from typing import Any, ClassVar, get_type_hints

from pydantic import BaseModel, ValidationError

from pyrpckit.models import RpcModel


def _error_code(name: str) -> str:
    name = name.lstrip("_").removesuffix("RpcError").removesuffix("Error")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


class RpcErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_ERROR = -32000


class ProtocolDefinitionError(Exception):
    """A handler, notification, or feature is not a valid protocol definition."""


class RpcValidationIssue(RpcModel):
    loc: list[str | int]
    message: str
    type: str


class RpcMethodNotFound(RpcModel):
    method: str


class RpcInvalidParams(RpcModel):
    issues: list[RpcValidationIssue]


class RpcError(Exception):
    code: ClassVar[str]
    message: ClassVar[str]
    rpc_code: ClassVar[int] = RpcErrorCode.SERVER_ERROR
    details_type: ClassVar[type[BaseModel] | None] = None
    _builtin: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        code = cls.__dict__.get("code", _error_code(cls.__name__))
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]*", code) is None:
            raise ProtocolDefinitionError(f"Invalid RPC error code: {code!r}")
        rpc_code = cls.__dict__.get("rpc_code", RpcErrorCode.SERVER_ERROR)
        if not isinstance(rpc_code, int):
            raise ProtocolDefinitionError("RPC error rpc_code must be an integer")
        standard = {-32700, -32600, -32601, -32602, -32603}
        if int(rpc_code) in standard and not cls.__dict__.get("_builtin", False):
            raise ProtocolDefinitionError(
                "Reserved JSON-RPC codes are only for built-in errors"
            )
        if -32768 <= int(rpc_code) < -32099 and not cls.__dict__.get("_builtin", False):
            raise ProtocolDefinitionError(f"RPC error code {rpc_code} is reserved")
        details_type = cls.__dict__.get("details_type", None)
        annotation = inspect.get_annotations(cls, eval_str=True).get("details")
        if annotation is not None:
            with suppress(NameError, TypeError):
                annotation = get_type_hints(cls).get("details", annotation)
            if not isinstance(annotation, type) or not issubclass(
                annotation, BaseModel
            ):
                raise ProtocolDefinitionError(
                    "RPC error details must be a Pydantic model"
                )
            details_type = annotation
        elif "details_type" not in cls.__dict__:
            details_type = getattr(cls, "details_type", None)
        cls.code = code
        cls.message = cls.__dict__.get("message", code.replace("_", " ").capitalize())
        cls.rpc_code = int(rpc_code)
        cls.details_type = details_type

    def __init__(
        self,
        details: BaseModel | None = None,
        /,
        *,
        message: str | None = None,
        **fields: Any,
    ) -> None:
        if type(self) is RpcError:
            raise TypeError("RpcError cannot be instantiated directly")
        details_type = type(self).details_type
        if details_type is None:
            if details is not None or fields:
                suggestion = (
                    "; did you mean to pass message=...?"
                    if isinstance(details, str)
                    else ""
                )
                raise TypeError(
                    f"{type(self).__name__} does not accept details{suggestion}"
                )
            self.details = None
        else:
            if details is not None and fields:
                raise TypeError("Pass either a details instance or fields, not both")
            if details is None and not fields:
                raise TypeError(f"{type(self).__name__} requires details")
            if details is not None and not isinstance(details, details_type):
                raise TypeError(f"details must be {details_type.__name__}")
            self.details = details if details is not None else details_type(**fields)
        self.message = message or type(self).message
        super().__init__(self.message)


class RpcParseError(RpcError):
    _builtin = True
    code = "parse_error"
    rpc_code = RpcErrorCode.PARSE_ERROR


class RpcInvalidRequestError(RpcError):
    _builtin = True
    code = "invalid_request"
    rpc_code = RpcErrorCode.INVALID_REQUEST


class RpcMethodNotFoundError(RpcError):
    _builtin = True
    code = "method_not_found"
    rpc_code = RpcErrorCode.METHOD_NOT_FOUND
    details: RpcMethodNotFound

    def __init__(self, name: str) -> None:
        super().__init__(method=name)
        self.name = name


class RpcInvalidParamsError(RpcError):
    _builtin = True
    code = "invalid_params"
    rpc_code = RpcErrorCode.INVALID_PARAMS
    details: RpcInvalidParams

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> "RpcInvalidParamsError":
        issues = [
            RpcValidationIssue(
                loc=list(issue["loc"]), message=issue["msg"], type=issue["type"]
            )
            for issue in error.errors(include_url=False)
        ]
        first = issues[0]
        location = ".".join(str(part) for part in ("params", *first.loc))
        return cls(
            issues=issues, message=f"Invalid params at {location}: {first.message}"
        )

    def __init__(self, error: ValidationError | None = None, **fields: Any) -> None:
        if error is not None:
            converted = type(self).from_validation_error(error)
            self.details = converted.details
            self.message = converted.message
            Exception.__init__(self, self.message)
            return
        fields.setdefault("issues", [])
        super().__init__(**fields)


class RpcInternalError(RpcError):
    _builtin = True
    code = "internal_error"
    rpc_code = RpcErrorCode.INTERNAL_ERROR


def error_message(code: str | int) -> str:
    if isinstance(code, str):
        return code.replace("_", " ").capitalize()
    try:
        return RpcErrorCode(code).name.replace("_", " ").capitalize()
    except ValueError:
        return f"Error {int(code)}"


def declared_error(error: Any) -> type[RpcError]:
    if not (isinstance(error, type) and issubclass(error, RpcError)):
        raise ProtocolDefinitionError(
            f"Declared RPC error must be an RpcError subclass: {error!r}"
        )
    if error is RpcError or getattr(error, "_builtin", False):
        raise ProtocolDefinitionError(
            "Built-in RPC errors are implicit and cannot be declared"
        )
    return error
