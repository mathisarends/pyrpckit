from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc


class Params(BaseModel):
    value: str


def test_method_name_is_inferred_with_and_without_options() -> None:
    router = rpc.RpcRouter(namespace="search")

    @router.method
    async def run(params: Params) -> None: ...

    @router.method(summary="Preview.")
    async def preview(params: Params) -> None: ...

    @router.method()
    async def inspect(params: Params) -> None: ...

    assert [route.name for route in router.routes] == [
        "search.run",
        "search.preview",
        "search.inspect",
    ]
    assert router.routes[1].summary == "Preview."


def test_explicit_method_name_overrides_the_function_name() -> None:
    router = rpc.RpcRouter(namespace="search")

    @router.method("run")
    async def execute_search(params: Params) -> None: ...

    assert router.routes[0].name == "search.run"


def test_router_collects_class_methods_with_namespace_tags_and_metadata() -> None:
    router = rpc.RpcRouter(namespace="browser.nav", tags=("browser", "control"))

    class NavigationMethods:
        @router.method(summary="Navigate.")
        async def navigate(self, params: Params) -> None: ...

    route = router.routes[0]
    assert route.name == "browser.nav.navigate"
    assert route.tags == ("browser", "control")
    assert route.summary == "Navigate."
    assert route.binding.owner is NavigationMethods
    assert route.binding.attribute_name == "navigate"


async def test_a_decorated_free_function_remains_callable() -> None:
    router = rpc.RpcRouter()

    @router.method
    async def ping() -> None:
        return None

    assert await ping() is None
    assert router.routes[0].binding.owner is None


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


def test_router_collects_notifications() -> None:
    class Changed(BaseModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = rpc.RpcRouter(namespace="browser", tags=("browser",))

    @router.notification("changed")
    def changed() -> Changed:
        """Browser state."""

    assert changed.__name__ == "changed"
    assert router.notifications[0].name == "browser.changed"
    assert router.notifications[0].payload is Changed
    assert router.notifications[0].summary == "Browser state."
    assert router.notifications[0].tags == ("browser",)


def test_a_notification_declaration_must_not_take_parameters() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="must not take parameters"):

        @router.notification("changed")
        def changed(value: str) -> str: ...


def test_a_notification_declaration_needs_a_return_annotation() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(rpc.ProtocolDefinitionError, match="return annotation"):

        @router.notification("changed")
        def changed(): ...
