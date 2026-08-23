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


def test_methods_are_grouped_into_namespaces(document: dict[str, Any]) -> None:
    ir = build_ir(document)

    assert [namespace.name for namespace in ir.namespaces] == ["greeting"]
    assert [operation.name for operation in ir.namespaces[0].operations] == [
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
    assert say.method_member == "GREETING_SAY"
    assert say.params_model == "SayParams"
    assert say.result == NamedType("SayResult")
    assert say.summary == "Greet someone by name."
    assert [parameter.name for parameter in say.params] == ["name"]


def test_a_method_without_a_result_lowers_to_null(document: dict[str, Any]) -> None:
    ir = build_ir(document)
    forget = ir.operations[1]

    assert forget.result == PrimitiveType(Primitive.NULL)


def test_the_method_enum_covers_every_operation(document: dict[str, Any]) -> None:
    ir = build_ir(document)

    assert [(member.name, member.value) for member in ir.method_enum.members] == [
        ("GREETING_SAY", "greeting.say"),
        ("GREETING_FORGET", "greeting.forget"),
        ("GREETING_GREETED", "greeting.greeted"),
        ("GREETING_CLEAR", "greeting.clear"),
    ]


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


def test_notifications_are_lowered(document: dict[str, Any]) -> None:
    ir = build_ir(document)

    assert len(ir.notifications) == 1
    assert ir.notifications[0].rpc_name == "greeting.changed"
    assert ir.notifications[0].message == NamedType("GreetingChangedNotification")
    assert ir.notifications[0].payload == NamedType("GreetingEvent")


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
