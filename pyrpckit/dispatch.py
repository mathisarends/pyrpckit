from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from pyrpckit.decorators import RpcHandler, decorated_methods
from pyrpckit.envelopes import RpcRequestEnvelope
from pyrpckit.errors import ProtocolDefinitionError, RpcInvalidParamsError
from pyrpckit.protocol import RpcMethodDefinition, RpcProtocol

type BoundRpcMethod = Callable[..., Awaitable[BaseModel | None]]


@dataclass(frozen=True, slots=True)
class RpcInvocation:
    request: RpcRequestEnvelope
    method: RpcMethodDefinition
    params: BaseModel | None


class RpcDispatcher:
    """Resolves requests against a protocol and invokes the bound handlers."""

    def __init__(
        self,
        protocol: RpcProtocol,
        handlers: Iterable[RpcHandler] = (),
        *,
        bound_methods: Mapping[str, BoundRpcMethod] | None = None,
    ) -> None:
        self._protocol = protocol
        self._bound = (
            _bound_methods(handlers) if bound_methods is None else dict(bound_methods)
        )
        _assert_complete(protocol, self._bound)

    def parse_request(self, raw_request: object) -> RpcInvocation:
        """Validate a decoded JSON payload into an invocation.

        Raises ``ValidationError`` for a malformed envelope,
        ``RpcMethodNotFoundError`` for an unknown method, and
        ``RpcInvalidParamsError`` for params that do not match the method.
        """
        request = RpcRequestEnvelope.model_validate(raw_request)
        method = self._protocol.method(request.method)
        return RpcInvocation(
            request=request,
            method=method,
            params=_validated_params(method, request.params),
        )

    async def execute(self, invocation: RpcInvocation) -> BaseModel | None:
        bound = self._bound[invocation.method.name]
        if invocation.params is None:
            return await bound()
        return await bound(invocation.params)


def _validated_params(
    method: RpcMethodDefinition,
    raw_params: dict[str, Any],
) -> BaseModel | None:
    if method.params is None:
        if raw_params:
            raise RpcInvalidParamsError(
                _unexpected_params_error(method, sorted(raw_params))
            )
        return None
    try:
        return TypeAdapter(method.params).validate_python(raw_params)
    except ValidationError as error:
        raise RpcInvalidParamsError(error) from error


def _unexpected_params_error(
    method: RpcMethodDefinition,
    names: list[str],
) -> ValidationError:
    """A validation error shaped like the one a params model would have raised."""
    return ValidationError.from_exception_data(
        method.name,
        [
            {
                "type": "extra_forbidden",
                "loc": (name,),
                "input": None,
            }
            for name in names
        ],
    )


def _bound_methods(handlers: Iterable[RpcHandler]) -> dict[str, BoundRpcMethod]:
    bound: dict[str, BoundRpcMethod] = {}
    for owner in handlers:
        for decorated in decorated_methods(type(owner)):
            name = decorated.metadata.name
            if name in bound:
                raise ProtocolDefinitionError(f"Duplicate RPC handler: {name}")
            bound[name] = decorated.function.__get__(owner)
    return bound


def _assert_complete(protocol: RpcProtocol, bound: dict[str, BoundRpcMethod]) -> None:
    declared = {method.name for method in protocol.methods}
    missing = sorted(declared - set(bound))
    unexpected = sorted(set(bound) - declared)
    if missing or unexpected:
        raise ProtocolDefinitionError(
            f"RPC handlers do not match the protocol: "
            f"missing={missing}, unexpected={unexpected}"
        )
