from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from types import FunctionType
from typing import Any

from pyrpckit.dependencies import RpcResolver, RpcResolverScope, call_scope
from pyrpckit.errors import ProtocolDefinitionError, RpcError
from pyrpckit.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    method_definition,
    notification_type_definitions,
)
from pyrpckit.router import (
    RpcModule,
    RpcRoute,
    join_rpc_name,
    normalize_namespace,
    normalize_tags,
)
from pyrpckit.server import RpcErrorMapper, RpcServer


class RpcChannel:
    """Define the operations sharing one transport connection."""

    def __init__(
        self,
        *,
        name: str = "default",
        namespace: str = "",
        tags: Iterable[str] = (),
        resolver_scope: RpcResolverScope = call_scope,
        version: int = 1,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ProtocolDefinitionError("RPC channel name cannot be empty")
        self._name = name
        self._version = version
        self._operations = RpcModule(
            namespace=namespace,
            tags=tags,
            resolver_scope=resolver_scope,
        )
        self._routes: list[RpcRoute] = []
        self._events: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()
        self._protocol: RpcProtocol | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return self._operations.namespace

    @property
    def tags(self) -> tuple[str, ...]:
        return self._operations.tags

    @property
    def resolver_scope(self) -> RpcResolverScope:
        return self._operations.resolver_scope

    @property
    def routes(self) -> tuple[RpcRoute, ...]:
        return (*self._routes, *self._operations.routes)

    @property
    def events(self) -> tuple[RpcNotificationDefinition, ...]:
        return (*self._events, *self._operations.events)

    def method(
        self,
        name: str | None = None,
        *,
        summary: str | None = None,
        errors: Iterable[type[RpcError]] = (),
    ) -> Callable[[FunctionType], FunctionType]:
        self._ensure_mutable()
        if name is not None and not isinstance(name, str):
            raise ProtocolDefinitionError(
                "RPC methods must use @channel.method() with parentheses"
            )
        decorate = self._operations.method(name, summary=summary, errors=errors)

        def register(function: FunctionType) -> FunctionType:
            self._ensure_mutable()
            result = decorate(function)
            return result

        return register

    def event(
        self,
        name: str,
        *,
        payload: Any,
        summary: str | None = None,
    ) -> Callable[[FunctionType], FunctionType]:
        self._ensure_mutable()
        decorate = self._operations.event(name, payload=payload, summary=summary)

        def register(function: FunctionType) -> FunctionType:
            self._ensure_mutable()
            result = decorate(function)
            return result

        return register

    def include(
        self,
        module: RpcModule,
        *,
        namespace: str = "",
        tags: Iterable[str] = (),
        resolver_scope: RpcResolverScope | None = None,
    ) -> None:
        """Include a snapshot of an endpoint-independent module."""
        self._ensure_mutable()
        if not isinstance(module, RpcModule):
            raise ProtocolDefinitionError(
                f"Expected an RpcModule, got {type(module).__name__}"
            )
        if resolver_scope is not None and not callable(resolver_scope):
            raise ProtocolDefinitionError("RPC resolver scope must be callable")
        include_namespace = normalize_namespace(namespace)
        include_tags = normalize_tags(tags)
        routes = tuple(
            replace(
                route,
                name=join_rpc_name(include_namespace, route.name),
                tags=normalize_tags((*route.tags, *include_tags)),
                resolver_scope=(
                    route.resolver_scope if resolver_scope is None else resolver_scope
                ),
            )
            for route in module.routes
        )
        events = tuple(
            replace(
                event,
                name=join_rpc_name(include_namespace, event.name),
                tags=normalize_tags((*event.tags, *include_tags)),
            )
            for event in module.events
        )
        self._reserve(
            *(route.name for route in routes),
            *(event.name for event in events),
        )
        self._routes.extend(routes)
        self._events.extend(events)

    @property
    def protocol(self) -> RpcProtocol:
        if self._protocol is None:
            self._reserve(
                *(route.name for route in self._operations.routes),
                *(event.name for event in self._operations.events),
            )
            methods = _method_definitions(list(self.routes))
            event_types = tuple(
                definition
                for event in self.events
                for definition in notification_type_definitions(event.payload)
            )
            self._protocol = RpcProtocol(
                methods=methods,
                notifications=self.events,
                notification_types=event_types,
                version=self._version,
            )
        return self._protocol

    def server(
        self,
        *,
        resolver: RpcResolver | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer:
        """Create a transport-agnostic runtime for this channel."""
        return RpcServer._from_channel(
            self.protocol,
            resolver=resolver,
            error_mapper=error_mapper,
        )

    def _reserve(self, *names: str) -> None:
        seen = set(self._names)
        for name in names:
            if name in seen:
                raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
            seen.add(name)
        self._names = seen

    def _ensure_mutable(self) -> None:
        if self._protocol is not None:
            raise ProtocolDefinitionError(
                "RpcChannel is frozen after its protocol has been accessed"
            )


def _method_definitions(routes: list[RpcRoute]) -> tuple[RpcMethodDefinition, ...]:
    definitions = [
        method_definition(
            name=route.name,
            function=route.function,
            handler_name=route.function.__name__,
            summary=route.summary,
            errors=route.errors,
            tags=route.tags,
            server=None,
            resolver_scope=route.resolver_scope,
        )
        for route in routes
    ]
    duplicate_requests = {
        name
        for name, count in Counter(item.request_name for item in definitions).items()
        if count > 1
    }
    return tuple(
        replace(
            definition,
            request_name=(
                _request_name(definition.name)
                if definition.request_name in duplicate_requests
                else definition.request_name
            ),
        )
        for definition in definitions
    )


def _request_name(wire_name: str) -> str:
    return "".join(part.capitalize() for part in wire_name.split(".")) + "Request"
