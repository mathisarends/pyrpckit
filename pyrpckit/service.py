import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import FunctionType
from typing import Any
from urllib.parse import unquote

from pyrpckit.channel import RpcChannel
from pyrpckit.connection import RpcLimits, RpcSocket
from pyrpckit.dependencies import RpcResolverLike
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.observer import RpcObserver
from pyrpckit.protocol import RpcProtocol, RpcStreamDefinition
from pyrpckit.server import (
    RpcErrorMapper,
    RpcServer,
)


@dataclass(frozen=True, slots=True, eq=False)
class RpcEndpoint:
    service: "RpcService"
    name: str
    path: str
    channels: tuple[RpcChannel, ...]
    error_mapper: RpcErrorMapper | None
    observer: RpcObserver | None
    limits: RpcLimits
    subprotocol: str | None
    summary: str | None
    path_variables: tuple[str, ...]

    @property
    def protocol(self) -> RpcProtocol:
        self.service.freeze()
        methods = tuple(
            m for m in self.service.protocol.methods if m.server == self.name
        )
        notifications = tuple(
            n for n in self.service.protocol.notifications if n.server == self.name
        )
        return RpcProtocol(
            methods=methods,
            notifications=notifications,
            notification_types=self.service.protocol.notification_types,
            version=self.service.version,
        )

    def match(self, path: str) -> dict[str, str] | None:
        return _match(self.path, path)

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
    ) -> None:
        from pyrpckit.runtime import serve_endpoint

        await serve_endpoint(
            self,
            socket,
            resolver=resolver,
            context=context,
            error_mapper=error_mapper or self.error_mapper,
            limits=limits or self.limits,
        )

    def server(
        self,
        *,
        context: object | Mapping[type[Any], object] | None = None,
        resolver: RpcResolverLike | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer:
        from pyrpckit.dependencies import ContextResolver, as_resolver, context_values

        resolved = as_resolver(resolver)
        values = context_values(context)
        if values:
            resolved = ContextResolver(resolved, values)
        return RpcServer._from_channel(
            self.protocol,
            resolver=resolved,
            error_mapper=error_mapper or self.error_mapper,
            observer=self.observer,
        )


@dataclass(frozen=True, slots=True, eq=False)
class RpcStreamEndpoint:
    service: "RpcService"
    name: str
    path: str
    stream: RpcStreamDefinition
    observer: RpcObserver | None
    limits: RpcLimits
    subprotocol: str | None
    summary: str | None
    path_variables: tuple[str, ...]

    def match(self, path: str) -> dict[str, str] | None:
        return _match(self.path, path)

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        limits: RpcLimits | None = None,
    ) -> None:
        from pyrpckit.runtime import serve_stream_endpoint

        await serve_stream_endpoint(
            self,
            socket,
            resolver=resolver,
            context=context,
            limits=limits or self.limits,
        )


class RpcService:
    def __init__(
        self,
        *,
        version: int = 1,
        error_mapper: RpcErrorMapper | None = None,
        observer: RpcObserver | None = None,
        limits: RpcLimits | None = None,
    ) -> None:
        if not isinstance(version, int) or version < 1:
            raise ProtocolDefinitionError(
                "RPC service version must be a positive integer"
            )
        self._version = version
        self._error_mapper = error_mapper
        self._observer = observer
        self._limits = limits or RpcLimits()
        self._endpoints: list[RpcEndpoint | RpcStreamEndpoint] = []
        self._channels: set[RpcChannel] = set()
        self._mounted_streams: set[FunctionType] = set()
        self._protocol: RpcProtocol | None = None

    version = property(lambda self: self._version)
    endpoints = property(lambda self: tuple(self._endpoints))
    protocol = property(lambda self: self.freeze())

    def socket(
        self,
        path: str,
        /,
        *,
        channels: Sequence[RpcChannel],
        name: str | None = None,
        error_mapper: RpcErrorMapper | None = None,
        observer: RpcObserver | None = None,
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
    ) -> RpcEndpoint:
        self._ensure_mutable()
        variables = _validate_endpoint(self._endpoints, path, name, subprotocol)
        if not channels:
            raise ProtocolDefinitionError("RPC sockets need at least one channel")
        if any(not isinstance(channel, RpcChannel) for channel in channels):
            raise ProtocolDefinitionError("RPC sockets accept RpcChannel objects")
        if any(channel in self._channels for channel in channels):
            raise ProtocolDefinitionError("An RPC channel can only be mounted once")
        names = [c.name for c in (*self._channels, *channels)]
        if len(names) != len(set(names)):
            raise ProtocolDefinitionError("RPC channel names must be unique")
        endpoint = RpcEndpoint(
            self,
            name or _endpoint_name(path),
            path,
            tuple(channels),
            error_mapper or self._error_mapper,
            observer or self._observer,
            limits or self._limits,
            subprotocol,
            summary,
            variables,
        )
        self._endpoints.append(endpoint)
        self._channels.update(channels)
        return endpoint

    def stream(
        self,
        path: str,
        stream: Callable[..., Any],
        /,
        *,
        name: str | None = None,
        observer: RpcObserver | None = None,
        limits: RpcLimits | None = None,
        subprotocol: str | None = None,
        summary: str | None = None,
    ) -> RpcStreamEndpoint:
        self._ensure_mutable()
        variables = _validate_endpoint(self._endpoints, path, name, subprotocol)
        marker = getattr(stream, "__pyrpckit_stream__", None)
        if not isinstance(stream, FunctionType) or marker is None:
            raise ProtocolDefinitionError(
                "rpc.stream() expects a function decorated with @channel.stream()"
            )
        if stream in self._mounted_streams:
            raise ProtocolDefinitionError("An RPC stream can only be mounted once")
        channel, _ = marker
        definition = next(item for item in channel.streams if item.function is stream)
        missing = [p for p in definition.path_parameters if p not in variables]
        if missing:
            raise ProtocolDefinitionError(
                f"RPC binary stream {definition.name} parameters "
                f"{', '.join(missing)} are not path variables of {path!r}; "
                "annotate dependencies as Inject[T]"
            )
        endpoint = RpcStreamEndpoint(
            self,
            name or _endpoint_name(path),
            path,
            definition,
            observer or self._observer,
            limits or self._limits,
            subprotocol,
            summary,
            variables,
        )
        self._endpoints.append(endpoint)
        self._channels.add(channel)
        self._mounted_streams.add(stream)
        return endpoint

    def endpoint(self, name: str) -> RpcEndpoint | RpcStreamEndpoint:
        for endpoint in self.endpoints:
            if endpoint.name == name:
                return endpoint
        raise KeyError(
            f"Unknown RPC endpoint {name!r}; known endpoints: "
            + ", ".join(e.name for e in self.endpoints)
        )

    def match(self, path: str):
        ordered = sorted(
            enumerate(self.endpoints), key=lambda item: ("{" in item[1].path, item[0])
        )
        for _, endpoint in ordered:
            values = endpoint.match(path)
            if values is not None:
                return endpoint, values
        return None

    def freeze(self) -> RpcProtocol:
        if self._protocol is not None:
            return self._protocol
        methods = []
        notifications = []
        types = []
        streams = []
        owners: dict[str, str] = {}
        errors: dict[str, type] = {}
        for endpoint in self.endpoints:
            if isinstance(endpoint, RpcEndpoint):
                for channel in endpoint.channels:
                    protocol = channel.freeze()
                    methods.extend(
                        replace(item, server=endpoint.name) for item in protocol.methods
                    )
                    notifications.extend(
                        replace(item, server=endpoint.name)
                        for item in protocol.notifications
                    )
                    types.extend(protocol.notification_types)
                    for item in (*protocol.methods, *protocol.notifications):
                        _unique_name(owners, item.name, channel.name)
                    for method in protocol.methods:
                        for error in method.raises:
                            previous = errors.get(error.code)
                            if previous is not None and previous is not error:
                                raise ProtocolDefinitionError(
                                    f"Duplicate RPC error code: {error.code}"
                                )
                            errors[error.code] = error
            else:
                channel = endpoint.stream.function.__pyrpckit_stream__[0]
                channel.freeze()
                _unique_name(owners, endpoint.stream.name, channel.name)
                streams.append(replace(endpoint.stream, server=endpoint.name))
        all_names = set(owners)
        for name in all_names:
            parts = name.split(".")
            for index in range(1, len(parts)):
                prefix = ".".join(parts[:index])
                if prefix in all_names:
                    raise ProtocolDefinitionError(
                        f"RPC name {prefix!r} is both an operation and a namespace"
                    )
        duplicate_requests = {
            name
            for name, count in Counter(m.request_name for m in methods).items()
            if count > 1
        }
        methods = [
            replace(
                m,
                request_name="".join(p.capitalize() for p in m.name.split("."))
                + "Request",
            )
            if m.request_name in duplicate_requests
            else m
            for m in methods
        ]
        self._protocol = RpcProtocol(
            methods=methods,
            notifications=notifications,
            notification_types=types,
            streams=streams,
            version=self.version,
        )
        return self._protocol

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
        root_path: str = "",
    ) -> None:
        path = socket.handshake.path
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :] or "/"
        matched = self.match(path)
        if matched is None:
            from pyrpckit.connection import RpcRejection

            await socket.reject(RpcRejection.NOT_FOUND, "Not found")
            return
        endpoint, _ = matched
        if isinstance(endpoint, RpcEndpoint):
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                error_mapper=error_mapper,
                limits=limits,
            )
        else:
            await endpoint.serve(
                socket, resolver=resolver, context=context, limits=limits
            )

    def contract(
        self,
        *,
        title: str,
        base_url: str,
        description: str = "Typed JSON-RPC API.",
        variables=None,
    ):
        from pyrpckit.contract import contract_from_service

        return contract_from_service(
            self,
            title=title,
            base_url=base_url,
            description=description,
            variables=variables,
        )

    def _ensure_mutable(self) -> None:
        if self._protocol is not None:
            raise ProtocolDefinitionError(
                "RpcService is frozen because its protocol was already materialized"
            )


def _validate_endpoint(
    endpoints, path: str, name: str | None, subprotocol: str | None
) -> tuple[str, ...]:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or (path != "/" and path.endswith("/"))
        or "?" in path
        or "#" in path
        or "//" in path
    ):
        raise ProtocolDefinitionError(f"Invalid RPC endpoint path: {path!r}")
    variables = tuple(re.findall(r"{([^{}]+)}", path))
    if (
        len(variables) != len(set(variables))
        or any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) is None for v in variables)
        or re.sub(r"{[^{}]+}", "", path).find("{") >= 0
    ):
        raise ProtocolDefinitionError(f"Invalid RPC path variables: {path!r}")
    endpoint_name = name or _endpoint_name(path)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", endpoint_name) is None:
        raise ProtocolDefinitionError(f"Invalid RPC endpoint name: {endpoint_name!r}")
    if subprotocol is not None and (
        not isinstance(subprotocol, str) or not subprotocol
    ):
        raise ProtocolDefinitionError("RPC subprotocol must be a non-empty string")
    normalized = re.sub(r"{[^{}]+}", "{}", path)
    if any(e.name == endpoint_name for e in endpoints):
        raise ProtocolDefinitionError(f"Duplicate RPC endpoint name: {endpoint_name}")
    if any(re.sub(r"{[^{}]+}", "{}", e.path) == normalized for e in endpoints):
        raise ProtocolDefinitionError(f"Duplicate RPC endpoint path: {path}")
    return variables


def _endpoint_name(path: str) -> str:
    for segment in reversed(path.split("/")):
        if segment and "{" not in segment:
            return segment
    raise ProtocolDefinitionError("Cannot derive endpoint name; pass name=...")


def _match(template: str, path: str) -> dict[str, str] | None:
    names = re.findall(r"{([^{}]+)}", template)
    pattern = (
        "^"
        + re.sub(
            r"{[^{}]+}",
            r"([^/]+)",
            re.escape(template).replace(r"\{", "{").replace(r"\}", "}"),
        )
        + "$"
    )
    match = re.match(pattern, path)
    return (
        None
        if match is None
        else {
            name: unquote(value)
            for name, value in zip(names, match.groups(), strict=True)
        }
    )


def _unique_name(owners: dict[str, str], name: str, channel: str) -> None:
    if name in owners:
        raise ProtocolDefinitionError(
            f"Duplicate RPC name: {name} (channels {owners[name]!r} and {channel!r})"
        )
    owners[name] = channel
