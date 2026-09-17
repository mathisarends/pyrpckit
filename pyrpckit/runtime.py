import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, get_type_hints

from pydantic import TypeAdapter

from pyrpckit.codec import RpcCodec
from pyrpckit.connection import (
    ConnectionRejected,
    RpcConnection,
    RpcConnectionClose,
    RpcDisconnect,
    RpcLimits,
    RpcRejection,
    RpcSocket,
)
from pyrpckit.constants import LOGGER_NAME
from pyrpckit.dependencies import (
    ContextResolver,
    RpcResolverLike,
    as_resolver,
    call_scope,
    connection_scope,
    context_values,
)
from pyrpckit.envelopes import RpcNotification
from pyrpckit.server import RpcErrorMapper, RpcServer
from pyrpckit.service import RpcEndpoint, RpcStreamEndpoint, analyze_connect_hook

logger = logging.getLogger(LOGGER_NAME)


async def _prepare(endpoint, socket, resolver, context):
    endpoint.service.freeze()
    resolved = as_resolver(resolver)
    values = context_values(context)
    path_params = (
        socket.handshake.path_params or endpoint.match(socket.handshake.path) or {}
    )
    connection = RpcConnection._create(endpoint, socket, path_params)
    base_values = {**values, RpcConnection: connection}
    hook_result = None
    hook_type = None
    if endpoint.connect is not None:
        injected, hook_type = analyze_connect_hook(endpoint.connect)
        if hook_type is not None and hook_type in values:
            raise ValueError(
                f"connect hook provides {hook_type.__name__}, which is also "
                "passed as context"
            )
        try:
            async with call_scope(ContextResolver(resolved, base_values)) as scoped:
                hints = get_type_hints(endpoint.connect, include_extras=True)
                arguments = {}
                for parameter in endpoint.connect.__annotations__:
                    if parameter == "return":
                        continue
                    if hints[parameter] is RpcConnection:
                        arguments[parameter] = connection
                    else:
                        dependency = next(
                            item for item in injected if item.name == parameter
                        )
                        arguments[parameter] = await scoped.resolve(
                            dependency.dependency
                        )
                hook_result = await endpoint.connect(**arguments)
        except ConnectionRejected as error:
            await socket.reject(error.rejection, error.reason)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("RPC connect hook failed")
            await socket.reject(RpcRejection.INTERNAL_ERROR, "Internal error")
            return None
    if (
        endpoint.subprotocol is not None
        and endpoint.subprotocol not in socket.handshake.subprotocols
    ):
        await socket.reject(RpcRejection.PROTOCOL_ERROR, "Unsupported subprotocol")
        return None
    await socket.accept(endpoint.subprotocol)
    connection._accepted = True
    if hook_type is not None:
        base_values[hook_type] = hook_result
    return connection, resolved, base_values


async def serve_endpoint(
    endpoint: RpcEndpoint,
    socket: RpcSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
) -> None:
    limits = limits or RpcLimits()
    prepared = await _prepare(endpoint, socket, resolver, context)
    if prepared is None:
        return
    connection, resolved, values = prepared
    close_event = asyncio.Event()
    close_value = [RpcConnectionClose.NORMAL, ""]
    peer_closed = False

    def request_close(code, reason):
        if not close_event.is_set():
            close_value[:] = [code, reason]
            close_event.set()

    connection._on_close = request_close
    outgoing: asyncio.Queue[str] = asyncio.Queue(limits.max_queue_size)
    tasks: set[asyncio.Task] = set()
    try:
        async with connection_scope(resolved, values) as scoped:
            server = RpcServer._from_channel(
                endpoint.protocol, resolver=scoped, error_mapper=error_mapper
            )

            async def writer():
                while True:
                    await socket.send(await outgoing.get())

            async def invoke(frame):
                try:
                    response = await server.handle_json(frame)
                    if response is not None:
                        await outgoing.put(response)
                finally:
                    semaphore.release()

            async def reader():
                nonlocal peer_closed
                try:
                    while True:
                        frame = await socket.receive()
                        size = len(
                            frame if isinstance(frame, bytes) else frame.encode()
                        )
                        if (
                            limits.max_message_bytes is not None
                            and size > limits.max_message_bytes
                        ):
                            request_close(RpcConnectionClose.MESSAGE_TOO_BIG, "")
                            return
                        if isinstance(frame, bytes):
                            try:
                                frame = frame.decode()
                            except UnicodeDecodeError:
                                request_close(
                                    RpcConnectionClose.PROTOCOL_ERROR,
                                    "Invalid RPC frame",
                                )
                                return
                        await semaphore.acquire()
                        task = asyncio.create_task(invoke(frame))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                except RpcDisconnect:
                    peer_closed = True
                    close_event.set()

            async def events():
                try:
                    await asyncio.gather(
                        *(
                            _event_source(event, scoped, outgoing)
                            for event in endpoint.protocol.notifications
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("RPC event source failed")
                    request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

            semaphore = asyncio.Semaphore(limits.max_concurrency)
            background = [
                asyncio.create_task(writer()),
                asyncio.create_task(reader()),
                asyncio.create_task(events()),
            ]
            await close_event.wait()
            for task in (*background, *tasks):
                task.cancel()
            await asyncio.gather(*background, *tasks, return_exceptions=True)
            if close_value[0] in (
                RpcConnectionClose.NORMAL,
                RpcConnectionClose.SHUTDOWN,
            ):
                while not outgoing.empty():
                    with suppress(RpcDisconnect):
                        await socket.send(outgoing.get_nowait())
    except asyncio.CancelledError:
        close_value[:] = [RpcConnectionClose.SHUTDOWN, ""]
        if not peer_closed:
            with suppress(RpcDisconnect):
                await socket.close(*close_value)
        connection._closed = True
        raise
    finally:
        if not peer_closed and not connection.closed:
            with suppress(RpcDisconnect):
                await socket.close(*close_value)
        connection._closed = True


async def _event_source(event, resolver, outgoing):
    arguments = {
        parameter.name: await resolver.resolve(parameter.dependency)
        for parameter in event.injected_parameters
    }
    adapter = TypeAdapter(event.payload)
    async for payload in event.function(**arguments):
        value = adapter.validate_python(payload)
        await outgoing.put(
            RpcCodec().encode(
                RpcNotification._with_payload_annotation(
                    event.name, value, event.payload
                )
            )
        )


async def serve_stream_endpoint(
    endpoint: RpcStreamEndpoint,
    socket: RpcSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    limits: RpcLimits | None = None,
) -> None:
    prepared = await _prepare(endpoint, socket, resolver, context)
    if prepared is None:
        return
    connection, resolved, values = prepared
    close_event = asyncio.Event()
    close_value = [RpcConnectionClose.NORMAL, ""]
    peer_closed = False

    def request_close(code, reason):
        if not close_event.is_set():
            close_value[:] = [code, reason]
            close_event.set()

    connection._on_close = request_close
    generator = None
    try:
        async with (
            connection_scope(resolved, values) as scoped,
            endpoint.stream.resolver_scope(scoped) as stream_resolver,
        ):
            arguments = {
                p.name: await stream_resolver.resolve(p.dependency)
                for p in endpoint.stream.injected_parameters
            }
            generator = endpoint.stream.function(**arguments)

            async def write():
                try:
                    async for frame in generator:
                        if not isinstance(frame, bytes | bytearray | memoryview):
                            logger.error("Binary stream yielded a non-bytes value")
                            request_close(
                                RpcConnectionClose.INTERNAL_ERROR, "Internal error"
                            )
                            return
                        await socket.send_bytes(bytes(frame))
                    request_close(RpcConnectionClose.NORMAL, "")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("RPC binary stream failed")
                    request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")

            async def read():
                nonlocal peer_closed
                try:
                    await socket.receive()
                    request_close(
                        RpcConnectionClose.PROTOCOL_ERROR,
                        "Binary stream is server-to-client",
                    )
                except RpcDisconnect:
                    peer_closed = True
                    close_event.set()

            tasks = [asyncio.create_task(write()), asyncio.create_task(read())]
            await close_event.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        close_value[:] = [RpcConnectionClose.SHUTDOWN, ""]
        raise
    finally:
        if generator is not None:
            with suppress(Exception):
                await generator.aclose()
        if not peer_closed:
            with suppress(RpcDisconnect):
                await socket.close(*close_value)
        connection._closed = True
