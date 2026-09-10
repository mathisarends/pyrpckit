from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, Field

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class SetParams(BaseModel):
    value: str


class ValueResult(BaseModel):
    value: str


class SearchParams(BaseModel):
    project_id: str
    max_results: int = 10


class SearchItem(BaseModel):
    item_id: str


class SearchResult(BaseModel):
    found_items: list[SearchItem]


class AliasedParams(BaseModel):
    project_id: str = Field(alias="projectKey")


def _request(name: str, value: str = "value") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": name,
        "params": {"value": value},
    }


async def test_router_adapts_plain_models_to_the_rpc_wire_contract() -> None:
    router = rpc.RpcModule(namespace="search")
    received: list[SearchParams] = []

    @router.method()
    async def run(params: SearchParams) -> SearchResult:
        received.append(params)
        return SearchResult(found_items=[SearchItem(item_id=params.project_id)])

    app = rpc.RpcChannel()
    app.include(router)
    response = await app.server().handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "search.run",
            "params": {"projectId": "p1", "maxResults": 2},
        }
    )

    assert received[0].project_id == "p1"
    assert response is not None and not isinstance(response, list)
    assert response.result == SearchResult(found_items=[SearchItem(item_id="p1")])
    assert response.model_dump(mode="json")["result"] == {
        "foundItems": [{"itemId": "p1"}]
    }
    schemas = render_openrpc(app.protocol, title="Search")["components"]["schemas"]
    assert set(schemas["SearchParams"]["properties"]) == {
        "projectId",
        "maxResults",
    }


def test_keyword_only_wire_parameters_are_rejected() -> None:
    router = rpc.RpcModule(namespace="search")

    @router.method()
    async def run(*, query: str, max_results: int = 10) -> list[str]:
        return [query] * max_results

    app = rpc.RpcChannel()
    app.include(router)

    with pytest.raises(rpc.ProtocolDefinitionError, match="Pydantic params model"):
        _ = app.protocol


def test_explicit_model_aliases_override_wire_names() -> None:
    router = rpc.RpcModule()

    @router.method()
    async def inspect(params: AliasedParams) -> None: ...

    app = rpc.RpcChannel()
    app.include(router)
    schema = render_openrpc(app.protocol, title="Aliased")["components"]["schemas"]

    assert set(schema["AliasedParams"]["properties"]) == {"projectKey"}


def test_app_composes_namespaces_tags_and_a_router_snapshot() -> None:
    router = rpc.RpcModule(namespace="browser.nav", tags=("browser", "control"))

    @router.method("navigate")
    async def navigate(params: SetParams) -> ValueResult:
        return ValueResult(value=params.value)

    app = rpc.RpcChannel(version=2)
    app.include(router, namespace="internal", tags=("admin", "browser"))

    @router.method("back")
    async def back() -> None: ...

    assert [method.name for method in app.protocol.methods] == [
        "internal.browser.nav.navigate"
    ]
    assert app.protocol.methods[0].tags == ("browser", "control", "admin")


async def test_include_can_override_a_router_resolver_scope() -> None:
    entered: list[str] = []

    @asynccontextmanager
    async def router_scope(
        resolver: rpc.RpcResolver,
    ) -> AsyncIterator[rpc.RpcResolver]:
        entered.append("router")
        yield resolver

    @asynccontextmanager
    async def mount_scope(
        resolver: rpc.RpcResolver,
    ) -> AsyncIterator[rpc.RpcResolver]:
        entered.append("mount")
        yield resolver

    router = rpc.RpcModule(resolver_scope=router_scope)

    @router.method()
    async def ping() -> str:
        return "pong"

    app = rpc.RpcChannel()
    app.include(router, resolver_scope=mount_scope)
    response = await app.server().handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert response is not None and not isinstance(response, list)
    assert response.result == "pong"
    assert entered == ["mount"]


def test_an_app_freezes_after_protocol_access() -> None:
    app = rpc.RpcChannel()
    _ = app.protocol

    with pytest.raises(rpc.ProtocolDefinitionError, match="frozen"):
        app.include(rpc.RpcModule())


def test_app_rejects_duplicate_names_while_including() -> None:
    router = rpc.RpcModule()

    @router.method("ping")
    async def ping() -> None: ...

    app = rpc.RpcChannel()
    app.include(router)
    with pytest.raises(rpc.ProtocolDefinitionError, match="Duplicate RPC route: ping"):
        app.include(router)


async def test_dependencies_are_injected_and_absent_from_openrpc() -> None:
    @dataclass
    class Service:
        suffix: str

    class Resolver:
        calls = 0

        async def resolve(self, dependency: type[Service]) -> Service:
            self.calls += 1
            assert dependency is Service
            return Service("!")

    router = rpc.RpcModule(namespace="value")

    @router.method("get")
    async def get(
        params: SetParams,
        service: rpc.Inject[Service],
    ) -> ValueResult:
        return ValueResult(value=params.value + service.suffix)

    app = rpc.RpcChannel()
    app.include(router)
    resolver = Resolver()
    response = await app.server(resolver=resolver).handle(_request("value.get", "one"))

    assert response is not None and not isinstance(response, list)
    assert response.result == ValueResult(value="one!")
    assert resolver.calls == 1
    method = render_openrpc(app.protocol, title="Value")["methods"][0]
    assert [parameter["name"] for parameter in method["params"]] == ["value"]


async def test_server_accepts_typed_context_without_a_custom_resolver() -> None:
    @dataclass
    class Service:
        suffix: str

    app = rpc.RpcChannel()

    @app.method()
    async def get(service: rpc.Inject[Service]) -> str:
        return "value" + service.suffix

    response = await app.server(context={Service: Service("!")}).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "get"}
    )

    assert response is not None and not isinstance(response, list)
    assert response.result == "value!"


def test_class_handlers_are_rejected_at_declaration() -> None:
    router = rpc.RpcModule()

    with pytest.raises(rpc.ProtocolDefinitionError, match="free function"):

        class Handler:
            @router.method()
            async def get(self) -> None: ...


def test_app_validates_free_function_signatures() -> None:
    router = rpc.RpcModule()

    @router.method("invalid")
    async def invalid(first: SetParams, second: SetParams) -> None: ...

    app = rpc.RpcChannel()
    app.include(router)

    with pytest.raises(rpc.ProtocolDefinitionError, match="Pydantic params model"):
        _ = app.protocol
