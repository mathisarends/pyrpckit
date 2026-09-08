from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace

from pyrpckit.dispatch import BoundRpcMethod
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import (
    RpcMethodDefinition,
    RpcNotificationDefinition,
    RpcProtocol,
    event_definitions,
    method_definition,
)
from pyrpckit.router import (
    RpcRoute,
    RpcRouter,
    join_rpc_name,
    normalize_prefix,
    normalize_tags,
    router_methods,
)
from pyrpckit.server import RpcErrorMapper, RpcServer


@dataclass(frozen=True, slots=True)
class RpcMountBinding:
    mount: "RpcRouterMount"
    handlers: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class RpcRouterMount:
    _app: "RpcApp"
    _identifier: int
    routes: tuple[RpcRoute, ...]

    def bind(self, *handlers: object) -> RpcMountBinding:
        """Associate handler instances with only this router mount."""
        return RpcMountBinding(self, handlers)


@dataclass(frozen=True, slots=True)
class _BindingCandidate:
    method: BoundRpcMethod
    origin: str


class RpcApp:
    """Compose routers into one protocol and bind its runtime handlers."""

    def __init__(self, *, version: int = 1) -> None:
        self._version = version
        self._routes: list[RpcRoute] = []
        self._events: list[RpcNotificationDefinition] = []
        self._names: set[str] = set()
        self._mounts: list[RpcRouterMount] = []
        self._protocol: RpcProtocol | None = None

    def include_router(
        self,
        router: RpcRouter,
        *,
        prefix: str = "",
        tags: Iterable[str] = (),
    ) -> RpcRouterMount:
        """Include a snapshot of a router and return its immutable mount."""
        if self._protocol is not None:
            raise ProtocolDefinitionError(
                "RpcApp is frozen after its protocol has been accessed"
            )
        if not isinstance(router, RpcRouter):
            raise ProtocolDefinitionError(
                f"Expected an RpcRouter, got {type(router).__name__}"
            )
        include_prefix = normalize_prefix(prefix)
        include_tags = normalize_tags(tags)
        routes = tuple(
            replace(
                route,
                name=join_rpc_name(include_prefix, route.name),
                tags=normalize_tags((*route.tags, *include_tags)),
            )
            for route in router.routes
        )
        events = tuple(
            replace(
                event,
                name=join_rpc_name(include_prefix, event.name),
                tags=normalize_tags((*event.tags, *include_tags)),
            )
            for event in router.events
        )
        names = [route.name for route in routes]
        names.extend(event.name for event in events)
        seen = set(self._names)
        for name in names:
            if name in seen:
                raise ProtocolDefinitionError(f"Duplicate RPC route: {name}")
            seen.add(name)
        self._names = seen
        mount = RpcRouterMount(self, len(self._mounts) + 1, routes)
        self._mounts.append(mount)
        self._routes.extend(routes)
        self._events.extend(events)
        return mount

    @property
    def protocol(self) -> RpcProtocol:
        if self._protocol is None:
            methods = _method_definitions(self._routes)
            events = tuple(
                definition
                for notification in self._events
                for definition in event_definitions(notification.payload)
            )
            self._protocol = RpcProtocol(
                methods=methods,
                notifications=self._events,
                events=events,
                version=self._version,
            )
        return self._protocol

    def bind(
        self,
        *handlers: object | RpcMountBinding,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer:
        """Bind every declared instance method exactly once."""
        protocol = self.protocol
        candidates: dict[str, list[_BindingCandidate]] = {
            route.name: [] for route in self._routes
        }
        for route in self._routes:
            if route.binding.owner is None:
                candidates[route.name].append(
                    _BindingCandidate(route.function, "declared free function")
                )

        problems: list[str] = []
        for position, argument in enumerate(handlers, 1):
            if isinstance(argument, RpcMountBinding):
                if argument.mount._app is not self:
                    problems.append(
                        f"Router mount in app.bind() argument {position} belongs to "
                        "a different RpcApp. Use the mount returned by this app."
                    )
                    continue
                allowed = argument.mount.routes
                for handler in argument.handlers:
                    problems.extend(
                        _add_handler_candidates(
                            candidates,
                            allowed,
                            handler,
                            origin=(
                                f"app.bind() argument {position}, router mount "
                                f"{argument.mount._identifier}"
                            ),
                        )
                    )
            else:
                problems.extend(
                    _add_handler_candidates(
                        candidates,
                        tuple(self._routes),
                        argument,
                        origin=f"app.bind() argument {position}",
                    )
                )

        for route in self._routes:
            matched = candidates[route.name]
            if not matched:
                problems.append(_missing_handler(route))
            elif len(matched) > 1:
                problems.append(_duplicate_handlers(route, matched))
        if problems:
            raise ProtocolDefinitionError("\n\n".join(problems))

        bound = {name: values[0].method for name, values in candidates.items()}
        return RpcServer._from_bound_methods(
            protocol,
            bound,
            error_mapper=error_mapper,
        )


def _method_definitions(routes: list[RpcRoute]) -> tuple[RpcMethodDefinition, ...]:
    definitions = [
        method_definition(
            name=route.name,
            function=route.function,
            handler_name=route.binding.attribute_name or route.function.__name__,
            owner=route.binding.owner,
            summary=route.summary,
            errors=route.errors,
            tags=route.tags,
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


def _add_handler_candidates(
    candidates: dict[str, list[_BindingCandidate]],
    routes: tuple[RpcRoute, ...],
    handler: object,
    *,
    origin: str,
) -> list[str]:
    handler_type = type(handler)
    matched = [
        route
        for route in routes
        if route.binding.owner is not None
        and isinstance(handler, route.binding.owner)
        and route.function in {item.function for item in router_methods(handler_type)}
    ]
    for route in matched:
        candidates[route.name].append(
            _BindingCandidate(
                route.function.__get__(handler, handler_type),
                f"{origin}: {handler_type.__name__}",
            )
        )

    known_functions = {route.function for route in routes}
    unknown = [
        route
        for route in router_methods(handler_type)
        if route.function not in known_functions
    ]
    problems = [
        (
            f"Unknown decorated RPC method {route.name}.\n"
            f"Declared by {_declared_by(route)}.\n"
            f"Provided by {origin}: {handler_type.__name__}.\n"
            "Include its router in the app or remove this handler from app.bind(...)."
        )
        for route in unknown
    ]
    if not matched and not unknown:
        problems.append(
            f"{origin}: {handler_type.__name__} matches no RPC methods.\n"
            "Pass an instance whose decorated methods belong to this app."
        )
    return problems


def _missing_handler(route: RpcRoute) -> str:
    owner = route.binding.owner
    if owner is None:
        return f"No callable for free RPC function {route.name}."
    return (
        f"No handler instance for {route.name}.\n"
        f"Declared by {_declared_by(route)}.\n"
        f"Pass a {owner.__name__} instance to app.bind(...)."
    )


def _duplicate_handlers(route: RpcRoute, candidates: list[_BindingCandidate]) -> str:
    origins = "\n".join(f"- {candidate.origin}" for candidate in candidates)
    return (
        f"Multiple handler instances for {route.name}.\n"
        f"Declared by {_declared_by(route)}.\n"
        f"Matched handlers:\n{origins}\n"
        "Pass exactly one matching instance or use an explicit router-mount binding."
    )


def _declared_by(route: RpcRoute) -> str:
    owner = route.binding.owner
    attribute = route.binding.attribute_name or route.function.__name__
    return (
        f"{owner.__name__}.{attribute}"
        if owner is not None
        else route.function.__qualname__
    )
