from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pyrpckit.errors import RpcErrorCode

JSONRPC_VERSION = "2.0"

type RpcRequestId = str | int | None


class RpcSchema(BaseModel):
    """Base model for protocol payloads: immutable and strict about extra keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RpcRequestEnvelope(RpcSchema):
    jsonrpc: Literal["2.0"]
    id: RpcRequestId = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def expects_response(self) -> bool:
        return "id" in self.model_fields_set


class RpcErrorData(RpcSchema):
    code: int
    message: str


class RpcSuccess(RpcSchema):
    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RpcRequestId
    result: Any


class RpcFailure(RpcSchema):
    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RpcRequestId
    error: RpcErrorData

    @classmethod
    def of(
        cls,
        request_id: RpcRequestId,
        code: int | RpcErrorCode,
        message: str,
    ) -> "RpcFailure":
        return cls(id=request_id, error=RpcErrorData(code=int(code), message=message))


class RpcNotification(RpcSchema):
    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    method: str
    params: Any
