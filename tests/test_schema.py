import json

from pyrpckit import RpcProtocol
from pyrpckit.schema import render_json_schema, render_openrpc

from .conftest import GreetingNotificationMethod, GreetingRpcMethod


def test_the_json_schema_lists_every_frame_of_the_protocol(
    protocol: RpcProtocol,
) -> None:
    document = render_json_schema(protocol, title="Greeting Protocol")

    assert [frame["$ref"] for frame in document["oneOf"]] == [
        "#/$defs/SayRequest",
        "#/$defs/ForgetRequest",
        "#/$defs/GreetedNamesRequest",
        "#/$defs/ClearRequest",
        "#/$defs/RpcSuccess",
        "#/$defs/RpcFailure",
        "#/$defs/GreetingChangedNotification",
    ]


def test_request_schemas_pin_the_method_name(protocol: RpcProtocol) -> None:
    document = render_json_schema(protocol, title="Greeting Protocol")
    request = document["$defs"]["SayRequest"]

    assert request["properties"]["method"]["const"] == GreetingRpcMethod.SAY
    assert request["properties"]["params"] == {"$ref": "#/$defs/SayParams"}
    assert request["required"] == ["jsonrpc", "method", "params"]


def test_params_are_optional_when_the_model_has_no_required_fields(
    protocol: RpcProtocol,
) -> None:
    document = render_json_schema(protocol, title="Greeting Protocol")

    assert document["$defs"]["SayParams"]["required"] == ["name"]
    assert "params" in document["$defs"]["SayRequest"]["required"]


def test_events_and_notifications_are_indexed_as_extensions(
    protocol: RpcProtocol,
) -> None:
    document = render_json_schema(protocol, title="Greeting Protocol")

    assert document["x-rpc-protocol-version"] == protocol.version
    assert [event["name"] for event in document["x-rpc-events"]] == [
        "greeting.said",
        "greeting.forgotten",
    ]
    notification = document["x-rpc-notifications"][0]
    assert notification["name"] == GreetingNotificationMethod.CHANGED
    assert notification["message"] == {"$ref": "#/$defs/GreetingChangedNotification"}


def test_the_json_schema_is_serialisable(protocol: RpcProtocol) -> None:
    document = render_json_schema(
        protocol,
        title="Greeting Protocol",
        schema_id="https://example.test/greeting.schema.json",
    )

    assert json.loads(json.dumps(document))["$id"] == (
        "https://example.test/greeting.schema.json"
    )


def test_openrpc_describes_methods_by_name(protocol: RpcProtocol) -> None:
    document = render_openrpc(protocol, title="Greeting")
    say = next(
        method
        for method in document["methods"]
        if method["name"] == GreetingRpcMethod.SAY
    )

    assert say["summary"] == "Greet someone by name."
    assert say["paramStructure"] == "by-name"
    assert say["params"] == [
        {
            "name": "name",
            "required": True,
            "schema": {"title": "Name", "type": "string"},
        }
    ]
    assert say["result"]["schema"] == {"$ref": "#/components/schemas/SayResult"}


def test_openrpc_documents_the_declared_errors(protocol: RpcProtocol) -> None:
    document = render_openrpc(protocol, title="Greeting")
    say = next(
        method
        for method in document["methods"]
        if method["name"] == GreetingRpcMethod.SAY
    )
    forget = next(
        method
        for method in document["methods"]
        if method["name"] == GreetingRpcMethod.FORGET
    )

    assert "errors" not in say
    assert forget["errors"] == [{"code": -32001, "message": "Unknown greeting"}]


def test_openrpc_tags_each_method_with_its_feature(protocol: RpcProtocol) -> None:
    document = render_openrpc(protocol, title="Greeting")
    say = next(
        method
        for method in document["methods"]
        if method["name"] == GreetingRpcMethod.SAY
    )

    assert say["tags"] == [{"name": "greeting"}]


def test_openrpc_takes_a_missing_summary_from_the_docstring(
    protocol: RpcProtocol,
) -> None:
    document = render_openrpc(protocol, title="Greeting")
    forget = next(
        method
        for method in document["methods"]
        if method["name"] == GreetingRpcMethod.FORGET
    )

    assert forget["summary"] == "Forget a greeted name."


def test_openrpc_rewrites_every_reference_into_components(
    protocol: RpcProtocol,
) -> None:
    document = render_openrpc(
        protocol,
        title="Greeting",
        servers=({"name": "local", "url": "ws://127.0.0.1:8000/rpc"},),
    )

    assert "#/$defs/" not in json.dumps(document)
    assert document["servers"] == [{"name": "local", "url": "ws://127.0.0.1:8000/rpc"}]
    assert "SayParams" in document["components"]["schemas"]


def test_a_request_without_params_accepts_an_omitted_or_empty_member(
    protocol: RpcProtocol,
) -> None:
    document = render_json_schema(protocol, title="Greeting Protocol")
    request = document["$defs"]["ClearRequest"]

    assert request["required"] == ["jsonrpc", "method"]
    assert request["properties"]["params"] == {
        "additionalProperties": False,
        "type": "object",
    }


def test_openrpc_describes_a_method_without_params_as_taking_none(
    protocol: RpcProtocol,
) -> None:
    document = render_openrpc(protocol, title="Greeting")
    greeted = _method(document, GreetingRpcMethod.GREETED)

    assert greeted["params"] == []
    assert "x-rpc-params-schema" not in greeted
    assert greeted["result"]["schema"] == {"$ref": "#/components/schemas/GreetedResult"}


def test_openrpc_still_names_the_request_schema_of_a_method_without_params(
    protocol: RpcProtocol,
) -> None:
    document = render_openrpc(protocol, title="Greeting")

    assert _method(document, GreetingRpcMethod.CLEAR)["x-rpc-request-schema"] == {
        "$ref": "#/components/schemas/ClearRequest"
    }


def _method(document: dict[str, object], name: str) -> dict[str, object]:
    methods: list[dict[str, object]] = document["methods"]
    return next(method for method in methods if method["name"] == name)
