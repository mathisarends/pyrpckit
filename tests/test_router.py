from collections.abc import AsyncIterator
from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc


class Params(BaseModel):
    value: str


def test_method_name_is_inferred_with_explicit_parentheses() -> None:
    router = rpc.RpcRouter(namespace="search")

    @router.method()
    async def run(params: Params) -> None: ...

    @router.method(summary="Preview.")
    async def preview(params: Params) -> None: ...

    assert [route.name for route in router.routes] == [
        "search.run",
        "search.preview",
    ]
    assert router.routes[1].summary == "Preview."


def test_bare_method_decorator_is_rejected() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="with parentheses"):

        @router.method
        async def run() -> None: ...


def test_explicit_method_name_overrides_the_function_name() -> None:
    router = rpc.RpcRouter(namespace="search")

    @router.method("run")
    async def execute_search(params: Params) -> None: ...

    assert router.routes[0].name == "search.run"


def test_class_methods_are_rejected() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="free function"):

        class Handler:
            @router.method()
            async def run(self) -> None: ...

    with pytest.raises(rpc.ProtocolDefinitionError, match="free function"):

        class StaticHandler:
            @router.method()
            async def run() -> None: ...


async def test_a_decorated_free_function_remains_callable() -> None:
    router = rpc.RpcRouter()

    @router.method()
    async def ping() -> None:
        return None

    assert await ping() is None


def test_router_tags_are_ordered_and_deduplicated() -> None:
    router = rpc.RpcRouter(
        namespace="browser",
        tags=("browser", "control", "browser"),
    )
    assert router.namespace == "browser"
    assert router.tags == ("browser", "control")


def test_router_rejects_duplicate_wire_names() -> None:
    router = rpc.RpcRouter(namespace="browser")

    @router.method("ping")
    async def first() -> None: ...

    with pytest.raises(rpc.ProtocolDefinitionError, match="browser.ping"):

        @router.method("ping")
        async def second() -> None: ...


@pytest.mark.parametrize("namespace", (".browser", "browser.", "browser..nav"))
def test_router_rejects_invalid_namespaces(namespace: str) -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="Invalid RPC namespace"):
        rpc.RpcRouter(namespace=namespace)


@pytest.mark.parametrize("name", ("", ".ping", "ping.", "browser..ping"))
def test_router_rejects_invalid_names(name: str) -> None:
    router = rpc.RpcRouter()
    with pytest.raises(rpc.ProtocolDefinitionError, match="Invalid RPC name"):
        router.method(name)


def test_notification_declaration_registers_its_async_source() -> None:
    class Changed(BaseModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = rpc.RpcRouter(namespace="browser", tags=("browser",))

    @router.notification(
        "changed",
        payload=Changed,
        summary="Browser state.",
    )
    async def changed() -> AsyncIterator[Changed]:
        yield Changed()

    assert router.notifications[0].payload is Changed
    assert router.notifications[0].function is changed
    assert router.notifications[0].summary == "Browser state."
    assert router.notifications[0].tags == ("browser",)


def test_router_assigns_its_server_to_routes() -> None:
    class Changed(BaseModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = rpc.RpcRouter(namespace="browser", server="control")

    @router.method("navigate")
    async def navigate() -> None: ...

    @router.notification("changed", payload=Changed)
    async def changed() -> AsyncIterator[Changed]:
        yield Changed()

    assert router.routes[0].server == "control"
    assert router.notifications[0].server == "control"


def test_router_rejects_an_empty_server_name() -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="server cannot be empty"):
        rpc.RpcRouter(server="")


def test_router_exposes_its_routes_and_resolver_scope() -> None:
    router = rpc.RpcRouter()

    @router.method()
    async def ping() -> None: ...

    assert router.routes[0].function is ping
    assert callable(router.resolver_scope)


def test_router_rejects_invalid_resolver_scopes_and_tags() -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="scope must be callable"):
        rpc.RpcRouter(resolver_scope="invalid")  # type: ignore[arg-type]
    with pytest.raises(rpc.ProtocolDefinitionError, match="tags cannot be empty"):
        rpc.RpcRouter(tags=("",))


def test_router_rejects_non_function_and_non_async_method_handlers() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="must decorate a function"):
        router.method()(object())  # type: ignore[arg-type]
    with pytest.raises(rpc.ProtocolDefinitionError, match="must be async"):

        @router.method()
        def sync() -> None: ...


def test_notification_sources_must_be_free_functions() -> None:
    class Changed(BaseModel):
        revision: int

    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="free function"):

        class Source:
            @router.notification("changed", payload=Changed)
            async def changed() -> AsyncIterator[Changed]:
                yield Changed(revision=1)


def test_notification_sources_must_be_async_generators_with_the_declared_payload() -> (
    None
):
    class Changed(BaseModel):
        revision: int

    class Other(BaseModel):
        message: str

    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="async generator"):

        @router.notification("changed", payload=Changed)
        async def not_a_generator() -> AsyncIterator[Changed]:
            return Changed(revision=1)  # type: ignore[return-value]

    with pytest.raises(rpc.ProtocolDefinitionError, match="expected"):

        @router.notification("changed", payload=Changed)
        async def wrong_payload() -> AsyncIterator[Other]:
            yield Other(message="wrong")

    with pytest.raises(rpc.ProtocolDefinitionError, match="needs a return annotation"):

        @router.notification("changed", payload=Changed)
        async def missing_annotation():
            yield Changed(revision=1)

    with pytest.raises(rpc.ProtocolDefinitionError, match="must return"):

        @router.notification("changed", payload=Changed)
        async def wrong_return_annotation() -> list[Changed]:
            yield Changed(revision=1)

    @router.notification("changed", payload=Changed)
    async def changed() -> AsyncIterator[Changed]:
        yield Changed(revision=1)

    assert router.notifications[0].function is changed


def test_notification_source_parameters_must_be_injected() -> None:
    class Changed(BaseModel):
        revision: int

    class SourceParams(BaseModel):
        revision: int

    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="parameters must use Inject"):

        @router.notification("changed", payload=Changed)
        async def changed(params: SourceParams) -> AsyncIterator[Changed]:
            yield Changed(revision=params.revision)
