from collections import Counter
from collections.abc import Iterable
from dataclasses import replace

from pyrpckit.dependencies import RpcResolver, RpcResolverScope
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    method_definition,
    notification_type_definitions,
)
from pyrpckit.router import (
    RpcRoute,
    RpcRouter,
    join_rpc_name,
    normalize_namespace,
    normalize_tags,
)
from pyrpckit.server import RpcErrorMapper, RpcServer


class RpcApp:
    """Compose router snapshots into one executable RPC protocol."""

    def __init__(self, *, version: int = 1) -> None:
        self._version = version
        self._routes: list[RpcRoute] = []
        self._notifications: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()
        self._protocol: RpcProtocol | None = None

    def include_router(
        self,
        router: RpcRouter,
        *,
        namespace: str = "",
        tags: Iterable[str] = (),
        resolver_scope: RpcResolverScope | None = None,
    ) -> None:
        """Include a snapshot of a router, optionally overriding mount metadata."""
        if self._protocol is not None:
            raise ProtocolDefinitionError(
                "RpcApp is frozen after its protocol has been accessed"
            )
        if not isinstance(router, RpcRouter):
            raise ProtocolDefinitionError(
                f"Expected an RpcRouter, got {type(router).__name__}"
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
            for route in router.routes
        )
        notifications = tuple(
            replace(
                notification,
                name=join_rpc_name(include_namespace, notification.name),
                tags=normalize_tags((*notification.tags, *include_tags)),
            )
            for notification in router.notifications
        )
        names = [route.name for route in routes]
        names.extend(notification.name for notification in notifications)
        seen = set(self._names)
        for name in names:
            if name in seen:
                raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
            seen.add(name)
        self._names = seen
        self._routes.extend(routes)
        self._notifications.extend(notifications)

    @property
    def protocol(self) -> RpcProtocol:
        if self._protocol is None:
            methods = _method_definitions(self._routes)
            notification_types = tuple(
                definition
                for notification in self._notifications
                for definition in notification_type_definitions(notification.payload)
            )
            self._protocol = RpcProtocol(
                methods=methods,
                notifications=self._notifications,
                notification_types=notification_types,
                version=self._version,
            )
        return self._protocol

    def server(
        self,
        *,
        resolver: RpcResolver | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer:
        """Create a transport-agnostic runtime for this app."""
        return RpcServer._from_app(
            self.protocol,
            resolver=resolver,
            error_mapper=error_mapper,
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
            server=route.server,
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
