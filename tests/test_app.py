from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class SetParams(BaseModel):
    value: str


class ValueResult(BaseModel):
    value: str


def _request(name: str, value: str = "value") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": name,
        "params": {"value": value},
    }


def test_app_composes_prefixes_tags_and_a_router_snapshot() -> None:
    router = rpc.RpcRouter(prefix="browser.nav", tags=("browser", "control"))

    @router.method("navigate")
    async def navigate(params: SetParams) -> ValueResult:
        return ValueResult(value=params.value)

    app = rpc.RpcApp(version=2)
    app.include_router(router, prefix="internal", tags=("admin", "browser"))

    @router.method("back")
    async def back() -> None: ...

    assert [method.name for method in app.protocol.methods] == [
        "internal.browser.nav.navigate"
    ]
    method = app.protocol.methods[0]
    assert method.tags == ("browser", "control", "admin")
    assert app.protocol.version == 2


def test_an_app_freezes_after_protocol_access() -> None:
    app = rpc.RpcApp()
    _ = app.protocol

    with pytest.raises(rpc.ProtocolDefinitionError, match="frozen"):
        app.include_router(rpc.RpcRouter())


def test_app_rejects_duplicate_names_while_including() -> None:
    router = rpc.RpcRouter()

    @router.method("ping")
    async def ping() -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)
    with pytest.raises(rpc.ProtocolDefinitionError, match="Duplicate RPC route: ping"):
        app.include_router(router)


async def test_free_functions_are_bound_without_a_handler() -> None:
    router = rpc.RpcRouter()

    @router.method("echo")
    async def echo(params: SetParams) -> ValueResult:
        return ValueResult(value=params.value)

    app = rpc.RpcApp()
    app.include_router(router)

    response = await app.bind().handle(_request("echo", "hello"))

    assert response is not None
    assert response.result == ValueResult(value="hello")


async def test_an_instance_binds_all_mounts_of_its_router() -> None:
    router = rpc.RpcRouter(prefix="value")

    class Methods:
        def __init__(self, suffix: str) -> None:
            self.suffix = suffix

        @router.method("get")
        async def get(self, params: SetParams) -> ValueResult:
            return ValueResult(value=params.value + self.suffix)

    app = rpc.RpcApp()
    app.include_router(router, prefix="primary")
    app.include_router(router, prefix="secondary")
    server = app.bind(Methods("!"))

    first = await server.handle(_request("primary.value.get", "one"))
    second = await server.handle(_request("secondary.value.get", "two"))

    assert first is not None and first.result == ValueResult(value="one!")
    assert second is not None and second.result == ValueResult(value="two!")
    request_names = [method.request_name for method in app.protocol.methods]
    assert len(request_names) == len(set(request_names))


async def test_mount_bindings_can_use_different_instances() -> None:
    router = rpc.RpcRouter()

    class Methods:
        def __init__(self, value: str) -> None:
            self.value = value

        @router.method("get")
        async def get(self) -> ValueResult:
            return ValueResult(value=self.value)

    app = rpc.RpcApp()
    primary = app.include_router(router, prefix="primary")
    secondary = app.include_router(router, prefix="secondary")
    server = app.bind(primary.bind(Methods("one")), secondary.bind(Methods("two")))

    first = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "primary.get"})
    second = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "secondary.get"})

    assert first is not None and first.result == ValueResult(value="one")
    assert second is not None and second.result == ValueResult(value="two")


def test_a_missing_handler_has_an_actionable_diagnostic() -> None:
    router = rpc.RpcRouter(prefix="browser.nav")

    class NavigationMethods:
        @router.method("navigate")
        async def navigate(self, params: SetParams) -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(rpc.ProtocolDefinitionError) as caught:
        app.bind()

    assert str(caught.value) == (
        "No handler instance for browser.nav.navigate.\n"
        "Declared by NavigationMethods.navigate.\n"
        "Pass a NavigationMethods instance to app.bind(...)."
    )


def test_duplicate_handlers_name_every_binding_origin() -> None:
    router = rpc.RpcRouter(prefix="browser.nav")

    class NavigationMethods:
        @router.method("navigate")
        async def navigate(self, params: SetParams) -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(rpc.ProtocolDefinitionError) as caught:
        app.bind(NavigationMethods(), object(), NavigationMethods())

    message = str(caught.value)
    assert "Multiple handler instances for browser.nav.navigate." in message
    assert "Declared by NavigationMethods.navigate." in message
    assert "- app.bind() argument 1: NavigationMethods" in message
    assert "- app.bind() argument 3: NavigationMethods" in message
    assert "Pass exactly one matching instance" in message


def test_a_foreign_decorated_handler_has_an_actionable_diagnostic() -> None:
    included = rpc.RpcRouter(prefix="included")
    foreign = rpc.RpcRouter(prefix="foreign")

    class IncludedMethods:
        @included.method("run")
        async def run(self) -> None: ...

    class ForeignMethods:
        @foreign.method("run")
        async def run(self) -> None: ...

    app = rpc.RpcApp()
    app.include_router(included)

    with pytest.raises(rpc.ProtocolDefinitionError) as caught:
        app.bind(IncludedMethods(), ForeignMethods())

    message = str(caught.value)
    assert "Unknown decorated RPC method foreign.run." in message
    assert "Declared by ForeignMethods.run." in message
    assert "Include its router in the app" in message


def test_a_mount_from_another_app_is_rejected() -> None:
    router = rpc.RpcRouter()
    first = rpc.RpcApp()
    mount = first.include_router(router)
    second = rpc.RpcApp()

    with pytest.raises(rpc.ProtocolDefinitionError, match="different RpcApp"):
        second.bind(mount.bind())


def test_router_events_reach_the_protocol_and_openrpc() -> None:
    @rpc.event
    class Changed(BaseModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = rpc.RpcRouter(prefix="browser", tags=("browser",))
    router.event("event", Changed, summary="Browser changes.")
    app = rpc.RpcApp()
    app.include_router(router)

    document = render_openrpc(app.protocol, title="Browser")

    assert [item.name for item in app.protocol.notifications] == ["browser.event"]
    assert [item.name for item in app.protocol.events] == ["browser.changed"]
    assert document["x-rpc-notifications"][0]["name"] == "browser.event"


def test_app_validates_free_function_signatures_when_building_protocol() -> None:
    router = rpc.RpcRouter()

    @router.method("invalid")
    async def invalid(first: SetParams, second: SetParams) -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(rpc.ProtocolDefinitionError, match="must accept only params"):
        _ = app.protocol
