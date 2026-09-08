from copy import deepcopy
from typing import Any

import pytest

from pyrpckit.codegen import build_ir
from pyrpckit.codegen.ir import (
    LiteralType,
    ModelDecl,
    NamedType,
    Primitive,
    PrimitiveType,
    UnionType,
    UnsupportedSchemaError,
    type_expression,
)


def test_methods_form_a_hierarchical_api_tree(document: dict[str, Any]) -> None:
    ir = build_ir(document)

    assert [node.path for node in ir.api] == [("greeting",)]
    assert [route.operation_name for route in ir.api[0].operations] == [
        "say",
        "forget",
        "greeted",
        "clear",
    ]
    assert ir.root_operations == ()


def test_an_operation_keeps_its_wire_name_and_models(document: dict[str, Any]) -> None:
    ir = build_ir(document)
    say = ir.operations[0]

    assert say.rpc_name == "greeting.say"
    assert say.path == ("greeting",)
    assert say.operation_name == "say"
    assert say.method_member == "GREETING_SAY"
    assert say.params_model == "SayParams"
    assert say.result == NamedType("SayResult")
    assert say.summary == "Greet someone by name."
    assert [parameter.name for parameter in say.params] == ["name"]


def test_a_method_without_a_result_lowers_to_null(document: dict[str, Any]) -> None:
    ir = build_ir(document)
    forget = ir.operations[1]

    assert forget.result == PrimitiveType(Primitive.NULL)


def test_envelope_schemas_are_pruned(document: dict[str, Any]) -> None:
    names = {declaration.name for declaration in build_ir(document).declarations}

    assert "SayRequest" not in names
    assert "RpcSuccess" not in names
    assert "RpcFailure" not in names


def test_types_reachable_from_the_api_surface_are_kept(
    document: dict[str, Any],
) -> None:
    names = {declaration.name for declaration in build_ir(document).declarations}

    assert {"SayParams", "SayResult", "ForgetParams"} <= names
    assert {"GreetingChangedNotification", "GreetingEvent"} <= names
    assert {"GreetingSaid", "GreetingForgotten"} <= names


def test_models_carry_their_fields(document: dict[str, Any]) -> None:
    ir = build_ir(document)
    say_params = next(
        declaration
        for declaration in ir.declarations
        if isinstance(declaration, ModelDecl) and declaration.name == "SayParams"
    )

    assert [(f.name, f.required) for f in say_params.fields] == [("name", True)]


def test_events_are_lowered(document: dict[str, Any]) -> None:
    ir = build_ir(document)

    assert len(ir.events) == 1
    assert ir.events[0].rpc_name == "greeting.changed"
    assert ir.events[0].message == NamedType("GreetingChangedNotification")
    assert ir.events[0].payload == NamedType("GreetingEvent")


def test_nested_routes_form_nested_api_nodes(document: dict[str, Any]) -> None:
    nested = deepcopy(document)
    route = deepcopy(nested["methods"][0])
    route["name"] = "browser.nav.navigate"
    nested["methods"] = [route]

    ir = build_ir(nested)

    assert ir.api[0].path == ("browser",)
    assert ir.api[0].children[0].path == ("browser", "nav")
    assert ir.api[0].children[0].operations[0].operation_name == "navigate"


def test_route_metadata_is_preserved(document: dict[str, Any]) -> None:
    enriched = deepcopy(document)
    method = enriched["methods"][0]
    method.update(
        {
            "description": "A longer explanation.",
            "deprecated": True,
            "servers": [{"name": "secondary", "url": "wss://secondary"}],
            "errors": [
                {
                    "code": -32004,
                    "message": "Missing",
                    "x-rpckit-name": "GreetingMissing",
                    "x-rpckit-data-schema": {
                        "$ref": "#/components/schemas/MissingData"
                    },
                }
            ],
        }
    )
    enriched["components"]["schemas"]["MissingData"] = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    route = build_ir(enriched).operations[0]

    assert route.description == "A longer explanation."
    assert route.deprecated is True
    assert route.tags == ("greeting",)
    assert route.server_names == ("secondary",)
    assert route.errors[0].name == "GreetingMissing"
    assert route.errors[0].data == NamedType("MissingData")


def test_contract_servers_are_lowered(document: dict[str, Any]) -> None:
    deployed = deepcopy(document)
    deployed["servers"] = [
        {
            "name": "greeting-api",
            "url": "wss://{host}/{tenantId}",
            "summary": "Production gateway",
            "variables": {
                "host": {"default": "api.example.com"},
                "tenantId": {
                    "default": "demo",
                    "enum": ["demo", "production"],
                },
            },
            "x-rpckit-transport": "websocket",
        }
    ]

    ir = build_ir(deployed)

    assert ir.protocol_version == 1
    assert ir.servers[0].name == "greeting-api"
    assert ir.servers[0].transport == "websocket"
    assert [variable.name for variable in ir.servers[0].variables] == [
        "host",
        "tenantId",
    ]


def test_a_foreign_openrpc_document_is_rejected(document: dict[str, Any]) -> None:
    foreign = {**document, "methods": [{"name": "a.b", "params": [], "result": {}}]}

    with pytest.raises(UnsupportedSchemaError, match="x-rpc-request-schema"):
        build_ir(foreign)


def test_an_operation_without_params_carries_no_params_model(
    document: dict[str, Any],
) -> None:
    ir = build_ir(document)
    greeted = ir.operations[2]

    assert greeted.params == ()
    assert greeted.params_model is None
    assert greeted.result == NamedType("GreetedResult")


def test_inline_enums_become_literal_unions() -> None:
    assert type_expression({"type": "string", "enum": ["left", "right"]}) == (
        UnionType((LiteralType("left"), LiteralType("right")))
    )
