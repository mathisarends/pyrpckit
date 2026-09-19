import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from functools import cache
from typing import Any, Self

from pydantic import BaseModel, TypeAdapter, ValidationError

from pyrpckit.connection import RpcConnection, RpcLimits
from pyrpckit.constants import LOGGER_NAME
from pyrpckit.errors import RpcError
from pyrpckit.protocol import RpcClientMethod

logger = logging.getLogger(LOGGER_NAME)

REQUEST_ID_PREFIX = "server:"


class RpcClientMethodError(Exception):
    """Base error for a client method the server could not complete."""


class RpcClientMethodFailedError(RpcClientMethodError):
    """The client answered with an error the client method does not declare."""

    def __init__(
        self,
        method: str,
        rpc_code: int,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        super().__init__(
            f"RPC client method {method!r} failed ({code or rpc_code}): {message}"
        )
        self.method = method
        self.rpc_code = rpc_code
        self.code = code
        self.message = message
        self.details = details


class RpcClientMethodTimeoutError(RpcClientMethodError, TimeoutError):
    def __init__(self, method: str, timeout: float | None) -> None:
        super().__init__(f"RPC client method {method!r} timed out after {timeout}s")
        self.method = method
        self.timeout = timeout


class RpcClientMethodResultError(RpcClientMethodError):
    def __init__(self, method: str, error: ValidationError) -> None:
        super().__init__(f"Invalid result for RPC client method {method!r}: {error}")
        self.method = method
        self.validation_error = error


class RpcPeerClosedError(RpcClientMethodError):
    """The connection closed before the client answered."""


class RpcPeer:
    """The connected client, seen from the server: it answers client methods."""

    __slots__ = (
        "_client_methods",
        "_closed",
        "_connection",
        "_limits",
        "_next_id",
        "_pending",
        "_semaphore",
        "_send",
    )

    def __init__(self) -> None:
        raise TypeError("RpcPeer instances are created by pyrpckit")

    @classmethod
    def _create(
        cls,
        connection: RpcConnection,
        client_methods: Iterable[RpcClientMethod[Any, Any]],
        send: Callable[[str], Awaitable[None]],
        limits: RpcLimits,
    ) -> Self:
        self = cls.__new__(cls)
        self._connection = connection
        self._client_methods = {
            client_method.name: client_method for client_method in client_methods
        }
        self._send = send
        self._limits = limits
        self._semaphore = asyncio.Semaphore(limits.max_concurrency)
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._closed = False
        return self

    connection = property(lambda self: self._connection)
    closed = property(lambda self: self._closed)

    async def call[ParamsT: BaseModel | None, ResultT](
        self,
        client_method: RpcClientMethod[ParamsT, ResultT],
        params: ParamsT | None = None,
        *,
        timeout: float | None = None,
    ) -> ResultT:
        """Send a client method to the client and wait for its result."""
        definition = self._client_methods.get(client_method.name)
        if definition is None:
            raise ValueError(
                f"RPC client method {client_method.name!r} is not declared on endpoint "
                f"{self._connection.endpoint!r}"
            )
        if self._closed:
            raise RpcPeerClosedError("The RPC connection is closed")
        self._next_id += 1
        request_id = f"{REQUEST_ID_PREFIX}{self._next_id}"
        frame = _request_frame(request_id, definition, params)
        limit = self._limits.max_message_bytes
        if limit is not None and len(frame.encode()) > limit:
            raise ValueError(
                f"RPC client method {definition.name!r} request exceeds "
                f"max_message_bytes ({limit})"
            )
        response = asyncio.get_running_loop().create_future()
        try:
            async with asyncio.timeout(timeout), self._semaphore:
                if self._closed:
                    raise RpcPeerClosedError("The RPC connection is closed")
                self._pending[request_id] = response
                await self._send_until_closed(frame, response)
                message = await response
        except TimeoutError as error:
            raise RpcClientMethodTimeoutError(definition.name, timeout) from error
        finally:
            self._pending.pop(request_id, None)
        return _result(definition, message)

    async def _send_until_closed(
        self,
        frame: str,
        response: asyncio.Future[dict[str, Any]],
    ) -> None:
        sending = asyncio.ensure_future(self._send(frame))
        try:
            await asyncio.wait((sending, response), return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not sending.done():
                sending.cancel()
        if sending.done() and not sending.cancelled():
            sending.result()

    def _resolve(self, message: object) -> bool:
        """Take a response to a client method call; return whether it was one."""
        if (
            not isinstance(message, dict)
            or "method" in message
            or ("result" not in message and "error" not in message)
        ):
            return False
        request_id = message.get("id")
        response = (
            self._pending.get(request_id) if isinstance(request_id, str) else None
        )
        if response is None or response.done():
            logger.debug(
                "Dropped a response to unknown client_method id %r", request_id
            )
        else:
            response.set_result(message)
        return True

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for response in self._pending.values():
            if not response.done():
                response.set_exception(RpcPeerClosedError("The RPC connection closed"))


def _request_frame(
    request_id: str,
    client_method: RpcClientMethod[Any, Any],
    params: BaseModel | None,
) -> str:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": client_method.name,
    }
    if client_method.params is None:
        if params is not None:
            raise TypeError(f"RPC client method {client_method.name!r} takes no params")
    else:
        if params is None:
            raise TypeError(f"RPC client method {client_method.name!r} needs params")
        adapter = _adapter(client_method.params)
        value = adapter.validate_python(
            params.model_dump() if isinstance(params, BaseModel) else params
        )
        message["params"] = adapter.dump_python(value, mode="json", by_alias=True)
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False)


def _result(client_method: RpcClientMethod[Any, Any], message: dict[str, Any]) -> Any:
    if "error" in message:
        raise _error(client_method, message["error"])
    try:
        return _adapter(client_method.result).validate_python(message["result"])
    except ValidationError as error:
        raise RpcClientMethodResultError(client_method.name, error) from error


def _error(client_method: RpcClientMethod[Any, Any], error: object) -> Exception:
    if not isinstance(error, dict):
        return RpcClientMethodFailedError(
            client_method.name, 0, "", "Invalid error response"
        )
    data = error.get("data")
    code = data.get("code") if isinstance(data, dict) else None
    details = data.get("details") if isinstance(data, dict) else None
    rpc_code = error.get("code")
    rpc_code = rpc_code if isinstance(rpc_code, int) else 0
    message = error.get("message")
    message = message if isinstance(message, str) else "Remote RPC error"
    declared = _declared_error(client_method.raises, code, rpc_code)
    if declared is not None:
        try:
            if declared.details_type is None:
                return declared(message=message)
            return declared(
                declared.details_type.model_validate(details), message=message
            )
        except (TypeError, ValidationError):
            pass
    return RpcClientMethodFailedError(
        client_method.name,
        rpc_code,
        code if isinstance(code, str) else "",
        message,
        details,
    )


def _declared_error(
    errors: tuple[type[RpcError], ...],
    code: object,
    rpc_code: int,
) -> type[RpcError] | None:
    if isinstance(code, str) and code:
        return next((error for error in errors if error.code == code), None)
    matches = [error for error in errors if error.rpc_code == rpc_code]
    return matches[0] if len(matches) == 1 else None


@cache
def _adapter(annotation: Any) -> TypeAdapter[Any]:
    return TypeAdapter(annotation)
