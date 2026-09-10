from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from pyrpckit.dependencies import EmptyResolver, RpcResolver
from pyrpckit.envelopes import RpcRequestEnvelope
from pyrpckit.errors import ProtocolDefinitionError, RpcInvalidParamsError
from pyrpckit.protocol import RpcMethodDefinition, RpcProtocol


@dataclass(frozen=True, slots=True)
class RpcInvocation:
    request: RpcRequestEnvelope
    method: RpcMethodDefinition
    params: BaseModel | None


class RpcDispatcher:
    """Resolve, scope, and invoke the free functions in a protocol."""

    def __init__(
        self,
        protocol: RpcProtocol,
        *,
        resolver: RpcResolver | None = None,
    ) -> None:
        self._protocol = protocol
        self._resolver = resolver or EmptyResolver()
        _assert_executable(protocol)

    def parse_request(self, raw_request: object) -> RpcInvocation:
        request = RpcRequestEnvelope.model_validate(raw_request)
        method = self._protocol.method(request.method)
        return RpcInvocation(
            request=request,
            method=method,
            params=_validated_params(method, request.params),
        )

    async def execute(self, invocation: RpcInvocation) -> Any:
        method = invocation.method
        function = method.function
        scope = method.scope
        if function is None or scope is None:
            raise ProtocolDefinitionError(f"RPC method {method.name} is not executable")

        async with scope(self._resolver) as resolver:
            arguments = {
                parameter.name: await resolver.resolve(parameter.dependency)
                for parameter in method.injected_parameters
            }
            if invocation.params is not None:
                if method.params_parameter is None:
                    raise ProtocolDefinitionError(
                        f"RPC method {method.name} has no params parameter"
                    )
                arguments[method.params_parameter] = invocation.params
            return await function(**arguments)


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


def _assert_executable(protocol: RpcProtocol) -> None:
    missing = sorted(
        method.name for method in protocol.methods if method.function is None
    )
    if missing:
        raise ProtocolDefinitionError(
            f"RPC protocol has no functions for methods: {missing}"
        )
