import asyncio
import inspect
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, Protocol

from fastapi import APIRouter, Depends, WebSocket
from pydantic import TypeAdapter

from pyrpckit.app import RpcChannel
from pyrpckit.codec import RpcCodec
from pyrpckit.contract import RpcContract, ServerVariable
from pyrpckit.dependencies import (
    EmptyResolver,
    RpcResolver,
    RpcResolverScope,
    connection_scope,
)
from pyrpckit.envelopes import RpcNotification
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.protocol import RpcNotificationDefinition, RpcProtocol
from pyrpckit.router import normalize_tags
from pyrpckit.server import RpcErrorMapper

__all__ = ["RpcAPIRouter"]


class WebSocketConnection(Protocol):
    async def accept(self, subprotocol: str | None = None) -> None: ...

    async def receive(self) -> Mapping[str, Any]: ...

    async def send_text(self, data: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _ChannelBinding:
    path: str
    channel: RpcChannel
    subprotocol: str | None


class RpcAPIRouter(APIRouter):
    """Bind colocated RPC channels to FastAPI WebSocket routes."""

    def __init__(
        self,
        *,
        prefix: str = "",
        resolver: RpcResolver | None = None,
        tags: Iterable[str] = (),
        version: int = 1,
        error_mapper: RpcErrorMapper | None = None,
        max_concurrency: int = 32,
        max_queue_size: int = 128,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be at least 1")
        super().__init__(prefix=prefix)
        self._resolver = resolver or EmptyResolver()
        self._rpc_tags = normalize_tags(tags)
        self._version = version
        self._error_mapper = error_mapper
        self._max_concurrency = max_concurrency
        self._max_queue_size = max_queue_size
        self._connection_factory: Callable[..., Any] | None = None
        self._bindings: list[_ChannelBinding] = []
        self._rpc_routes: list[Any] = []

    @property
    def channels(self) -> tuple[RpcChannel, ...]:
        return tuple(binding.channel for binding in self._bindings)

    def websocket(
        self,
        path: str,
        *,
        name: str,
        namespace: str = "",
        tags: Iterable[str] = (),
        resolver_scope: RpcResolverScope | None = None,
        subprotocol: str | None = None,
    ) -> RpcChannel:
        """Create one RPC channel at a FastAPI WebSocket path."""
        if any(binding.channel.name == name for binding in self._bindings):
            raise ProtocolDefinitionError(f"Duplicate RPC channel name: {name}")
        arguments: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "tags": normalize_tags((*self._rpc_tags, *tags)),
            "version": self._version,
        }
        if resolver_scope is not None:
            arguments["resolver_scope"] = resolver_scope
        channel = RpcChannel(**arguments)
        channel._set_change_callback(self._sync_routes)
        self._bindings.append(_ChannelBinding(path, channel, subprotocol))
        self._sync_routes()
        return channel

    def connection(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Declare the default connection factory for channels in this router."""

        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            if not callable(function):
                raise ProtocolDefinitionError("RPC connection factory must be callable")
            if self._connection_factory is not None:
                raise ProtocolDefinitionError(
                    "RPC API router already has a connection factory"
                )
            self._connection_factory = function
            self._sync_routes()
            return function

        return decorate

    def contract(
        self,
        *,
        title: str,
        public_base_url: str,
        description: str = "Typed JSON-RPC API.",
        variables: Mapping[str, ServerVariable] | None = None,
    ) -> RpcContract:
        """Create the OpenRPC input used by the existing client generators."""
        if not public_base_url:
            raise ProtocolDefinitionError("RPC public base URL cannot be empty")
        raw_variables = dict(variables or {})
        if any(
            not isinstance(name, str) or not isinstance(value, ServerVariable)
            for name, value in raw_variables.items()
        ):
            raise ProtocolDefinitionError(
                "RPC contract variables must map names to ServerVariable objects"
            )
        supplied_variables = {
            _camel_case(name): value for name, value in raw_variables.items()
        }
        if len(supplied_variables) != len(raw_variables):
            raise ProtocolDefinitionError(
                "RPC contract variable names must be unique after camelCase conversion"
            )
        protocol = _combined_protocol(self._bindings, version=self._version)
        servers = tuple(
            _server_document(
                binding,
                prefix=self.prefix,
                public_base_url=public_base_url,
                variables=supplied_variables,
            )
            for binding in self._bindings
        )
        used_variables = {
            name for server in servers for name in server.get("variables", {})
        }
        unused_variables = sorted(set(supplied_variables) - used_variables)
        if unused_variables:
            raise ProtocolDefinitionError(
                "RPC contract variables are not present in any channel URL: "
                + ", ".join(unused_variables)
            )
        return RpcContract(
            protocol=protocol,
            title=title,
            description=description,
            servers=servers,
        )

    def _sync_routes(self) -> None:
        rpc_route_ids = {id(route) for route in self._rpc_routes}
        self.routes[:] = [
            route for route in self.routes if id(route) not in rpc_route_ids
        ]
        self._rpc_routes.clear()
        for binding in self._bindings:
            factory = binding.channel.connection_factory or self._connection_factory
            endpoint = _websocket_endpoint(self, binding, factory)
            previous = len(self.routes)
            super().add_api_websocket_route(
                binding.path,
                endpoint,
                name=binding.channel.name,
            )
            self._rpc_routes.extend(self.routes[previous:])


def _websocket_endpoint(
    router: RpcAPIRouter,
    binding: _ChannelBinding,
    factory: Callable[..., Any] | None,
) -> Callable[..., Any]:
    async def endpoint(**values: Any) -> None:
        websocket = values["websocket"]
        context: dict[type[Any], object] = {WebSocket: websocket}
        connection = values.get("rpc_connection")
        if isinstance(connection, Mapping):
            context.update(connection)
        elif connection is not None:
            context[type(connection)] = connection
        runtime = _RpcWebSocketRuntime(
            binding.channel,
            resolver=router._resolver,
            error_mapper=router._error_mapper,
            max_concurrency=router._max_concurrency,
            max_queue_size=router._max_queue_size,
            subprotocol=binding.subprotocol,
        )
        await runtime.serve(websocket, context=context)

    parameters = [
        inspect.Parameter(
            "websocket",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=WebSocket,
        )
    ]
    if factory is not None:
        parameters.append(
            inspect.Parameter(
                "rpc_connection",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Any,
                default=Depends(factory),
            )
        )
    endpoint.__name__ = f"{_python_identifier(binding.channel.name)}_rpc"
    endpoint.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    return endpoint


class _RpcWebSocketRuntime:
    def __init__(
        self,
        channel: RpcChannel,
        *,
        resolver: RpcResolver,
        error_mapper: RpcErrorMapper | None,
        max_concurrency: int,
        max_queue_size: int,
        subprotocol: str | None,
    ) -> None:
        self._channel = channel
        self._resolver = resolver
        self._error_mapper = error_mapper
        self._max_concurrency = max_concurrency
        self._max_queue_size = max_queue_size
        self._subprotocol = subprotocol
        self._codec = RpcCodec()

    async def serve(
        self,
        websocket: WebSocketConnection,
        *,
        context: object | Mapping[type[Any], object] | None = None,
    ) -> None:
        async with connection_scope(self._resolver, context) as resolver:
            server = self._channel.server(
                resolver=resolver,
                error_mapper=self._error_mapper,
            )
            await websocket.accept(subprotocol=self._subprotocol)
            outgoing: asyncio.Queue[str] = asyncio.Queue(self._max_queue_size)
            concurrency = asyncio.Semaphore(self._max_concurrency)
            tasks: set[asyncio.Task[None]] = set()
            writer = asyncio.create_task(_send_messages(websocket, outgoing))
            sources = [
                asyncio.create_task(
                    _send_events(event, resolver, outgoing, self._codec)
                )
                for event in self._channel.protocol.notifications
            ]
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    payload = message.get("text")
                    if payload is None:
                        payload = message.get("bytes")
                    if not isinstance(payload, str | bytes | bytearray):
                        continue
                    await concurrency.acquire()
                    task = asyncio.create_task(
                        _serve_message(server, payload, outgoing, concurrency)
                    )
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            finally:
                for task in (*tasks, *sources, writer):
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(*tasks, *sources, writer)


async def _send_events(
    event: RpcNotificationDefinition,
    resolver: RpcResolver,
    outgoing: asyncio.Queue[str],
    codec: RpcCodec,
) -> None:
    if event.function is None:
        raise TypeError(f"Event {event.name!r} has no source")
    arguments = {
        parameter.name: await resolver.resolve(parameter.dependency)
        for parameter in event.injected_parameters
    }
    adapter = TypeAdapter(event.payload)
    async for payload in event.function(**arguments):
        validated = adapter.validate_python(payload)
        message = RpcNotification._with_payload_annotation(
            event.name,
            validated,
            event.payload,
        )
        await outgoing.put(codec.encode(message))


async def _serve_message(
    server: Any,
    payload: str | bytes | bytearray,
    outgoing: asyncio.Queue[str],
    concurrency: asyncio.Semaphore,
) -> None:
    try:
        response = await server.handle_json(payload)
        if response is not None:
            await outgoing.put(response)
    finally:
        concurrency.release()


async def _send_messages(
    websocket: WebSocketConnection,
    outgoing: asyncio.Queue[str],
) -> None:
    while True:
        await websocket.send_text(await outgoing.get())


def _combined_protocol(
    bindings: Iterable[_ChannelBinding],
    *,
    version: int,
) -> RpcProtocol:
    methods = [
        replace(method, server=binding.channel.name)
        for binding in bindings
        for method in binding.channel.protocol.methods
    ]
    duplicate_requests = {
        name
        for name, count in Counter(method.request_name for method in methods).items()
        if count > 1
    }
    methods = [
        replace(
            method,
            request_name=(
                _request_name(method.name)
                if method.request_name in duplicate_requests
                else method.request_name
            ),
        )
        for method in methods
    ]
    events = tuple(
        replace(event, server=binding.channel.name)
        for binding in bindings
        for event in binding.channel.protocol.notifications
    )
    event_types = tuple(
        event_type
        for binding in bindings
        for event_type in binding.channel.protocol.notification_types
    )
    return RpcProtocol(
        methods=methods,
        notifications=events,
        notification_types=event_types,
        version=version,
    )


def _server_document(
    binding: _ChannelBinding,
    *,
    prefix: str,
    public_base_url: str,
    variables: Mapping[str, ServerVariable],
) -> dict[str, Any]:
    url = public_base_url.rstrip("/") + _client_path(prefix + binding.path)
    names = tuple(dict.fromkeys(re.findall(r"{([^{}]+)}", url)))
    declared = {
        name: variables.get(name, ServerVariable(default=f"{{{name}}}")).document()
        for name in names
    }
    transport: dict[str, Any] = {
        "type": "websocket",
        "messageEncoding": "json",
        "frameType": "text",
    }
    if binding.subprotocol is not None:
        transport["subprotocols"] = [binding.subprotocol]
    document: dict[str, Any] = {
        "name": binding.channel.name,
        "url": url,
        "x-rpckit-transport": transport,
    }
    if declared:
        document["variables"] = declared
    return document


def _client_path(path: str) -> str:
    return re.sub(
        r"{([^{}]+)}",
        lambda match: "{" + _camel_case(match.group(1)) + "}",
        path,
    )


def _camel_case(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest)


def _python_identifier(value: str) -> str:
    identifier = re.sub(r"\W", "_", value)
    return f"_{identifier}" if identifier[:1].isdigit() else identifier


def _request_name(wire_name: str) -> str:
    return "".join(part.capitalize() for part in wire_name.split(".")) + "Request"
