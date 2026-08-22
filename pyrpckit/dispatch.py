from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from pydantic import BaseModel, TypeAdapter, ValidationError

from pyrpckit.decorators import RpcHandler, decorated_methods
from pyrpckit.envelopes import RpcRequestEnvelope
from pyrpckit.errors import ProtocolDefinitionError, RpcInvalidParamsError
from pyrpckit.protocol import RpcMethodDefinition, RpcProtocol

type BoundRpcMethod = Callable[[BaseModel], Awaitable[BaseModel | None]]


@dataclass(frozen=True, slots=True)
class RpcInvocation:
    request: RpcRequestEnvelope
    method: RpcMethodDefinition
    params: BaseModel


class RpcDispatcher:
    """Resolves requests against a protocol and invokes the bound handlers."""

    def __init__(
        self,
        protocol: RpcProtocol,
        handlers: Iterable[RpcHandler],
    ) -> None:
        self._protocol = protocol
        self._bound = _bound_methods(handlers)
        _assert_complete(protocol, self._bound)

    def parse_request(self, raw_request: object) -> RpcInvocation:
        """Validate a decoded JSON payload into an invocation.

        Raises ``ValidationError`` for a malformed envelope,
        ``RpcMethodNotFoundError`` for an unknown method, and
        ``RpcInvalidParamsError`` for params that do not match the method.
        """
        request = RpcRequestEnvelope.model_validate(raw_request)
        method = self._protocol.method(request.method)
        try:
            params = TypeAdapter(method.params).validate_python(request.params)
        except ValidationError as error:
            raise RpcInvalidParamsError(error) from error
        return RpcInvocation(request=request, method=method, params=params)

    async def execute(self, invocation: RpcInvocation) -> BaseModel | None:
        return await self._bound[invocation.method.name](invocation.params)


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
