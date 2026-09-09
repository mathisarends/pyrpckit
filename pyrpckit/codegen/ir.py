from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

type TypeExpr = (
    PrimitiveType
    | LiteralType
    | EnumLiteralType
    | NamedType
    | ListType
    | MapType
    | UnionType
)
type Declaration = EnumDecl | ModelDecl | AliasDecl


class UnsupportedSchemaError(Exception):
    """The OpenRPC document contains something the generator cannot express."""


class Primitive(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"
    UUID = "uuid"
    DATETIME = "datetime"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class PrimitiveType:
    primitive: Primitive


@dataclass(frozen=True, slots=True)
class LiteralType:
    value: str | int | bool


@dataclass(frozen=True, slots=True)
class EnumLiteralType:
    enum: str
    member: str


@dataclass(frozen=True, slots=True)
class NamedType:
    name: str


@dataclass(frozen=True, slots=True)
class ListType:
    item: TypeExpr


@dataclass(frozen=True, slots=True)
class MapType:
    value: TypeExpr


@dataclass(frozen=True, slots=True)
class UnionType:
    members: tuple[TypeExpr, ...]
    discriminator: str | None = None


@dataclass(frozen=True, slots=True)
class EnumMember:
    name: str
    value: str | int


@dataclass(frozen=True, slots=True)
class EnumDecl:
    name: str
    members: tuple[EnumMember, ...]
    integral: bool = False


@dataclass(frozen=True, slots=True)
class FieldDecl:
    name: str
    type: TypeExpr
    required: bool
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True, slots=True)
class ModelDecl:
    name: str
    fields: tuple[FieldDecl, ...]
    closed: bool = False


@dataclass(frozen=True, slots=True)
class AliasDecl:
    name: str
    target: TypeExpr


@dataclass(frozen=True, slots=True)
class ParamDecl:
    name: str
    type: TypeExpr
    required: bool
    default: Any = None
    has_default: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecl:
    rpc_name: str
    operation_name: str
    path: tuple[str, ...]
    method_member: str
    params: tuple[ParamDecl, ...]
    params_model: str | None
    result: TypeExpr
    summary: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    errors: tuple[ErrorDecl, ...] = ()
    server_names: tuple[str, ...] = ()
    deprecated: bool = False

    @property
    def name(self) -> str:
        return self.operation_name


@dataclass(frozen=True, slots=True)
class ErrorDecl:
    code: int
    message: str
    name: str | None = None
    data: TypeExpr | None = None


@dataclass(frozen=True, slots=True)
class ServerVariableDecl:
    name: str
    default: str
    description: str = ""
    enum: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ServerDecl:
    name: str
    url: str
    summary: str = ""
    description: str = ""
    variables: tuple[ServerVariableDecl, ...] = ()
    transport: str | None = None


@dataclass(frozen=True, slots=True)
class ApiNode:
    segment: str
    path: tuple[str, ...]
    operations: tuple[RouteDecl, ...] = ()
    children: tuple[ApiNode, ...] = ()


@dataclass(frozen=True, slots=True)
class NotificationDecl:
    rpc_name: str
    payload: TypeExpr
    message: TypeExpr
    summary: str = ""
    server_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClientIr:
    title: str
    version: str
    protocol_version: int | None = None
    servers: tuple[ServerDecl, ...] = ()
    declarations: tuple[Declaration, ...] = ()
    root_operations: tuple[RouteDecl, ...] = ()
    api: tuple[ApiNode, ...] = ()
    notifications: tuple[NotificationDecl, ...] = ()

    @property
    def models(self) -> tuple[ModelDecl, ...]:
        return tuple(
            declaration
            for declaration in self.declarations
            if isinstance(declaration, ModelDecl)
        )

    @property
    def operations(self) -> tuple[RouteDecl, ...]:
        return self.root_operations + tuple(
            operation for node in _walk_api(self.api) for operation in node.operations
        )


def build_ir(document: dict[str, Any]) -> ClientIr:
    """Lower an OpenRPC document into the language-neutral client model."""
    schemas: dict[str, Any] = document.get("components", {}).get("schemas", {})
    methods: list[dict[str, Any]] = document.get("methods", [])
    discriminator_enums, discriminator_fields = _discriminators(schemas)
    info = document.get("info", {})
    routes = tuple(_route(method) for method in methods)
    root_operations = tuple(route for route in routes if not route.path)
    api = _api_tree(route for route in routes if route.path)
    notifications = _notifications(document)
    declarations = (
        *discriminator_enums,
        *_schema_declarations(schemas, discriminator_fields),
    )
    return ClientIr(
        title=info.get("title", "RPC"),
        version=str(info.get("version", "0.0.0")),
        protocol_version=document.get("x-rpc-protocol-version"),
        servers=_servers(document),
        declarations=_reachable(
            declarations,
            _roots(routes, notifications),
        ),
        root_operations=root_operations,
        api=api,
        notifications=notifications,
    )


def named_types(expression: TypeExpr) -> set[str]:
    """The named declarations one type expression depends on."""
    if isinstance(expression, NamedType):
        return {expression.name}
    if isinstance(expression, EnumLiteralType):
        return {expression.enum}
    if isinstance(expression, ListType):
        return named_types(expression.item)
    if isinstance(expression, MapType):
        return named_types(expression.value)
    if isinstance(expression, UnionType):
        return {name for member in expression.members for name in named_types(member)}
    return set()


def type_expression(schema: dict[str, Any] | bool) -> TypeExpr:
    """Lower one JSON Schema node into a type expression."""
    if isinstance(schema, bool):
        return PrimitiveType(Primitive.ANY)
    if "$ref" in schema:
        return NamedType(ref_name(schema))
    if "const" in schema:
        return LiteralType(schema["const"])
    if "enum" in schema:
        literals = tuple(LiteralType(value) for value in schema["enum"])
        return literals[0] if len(literals) == 1 else UnionType(literals)
    variants = schema.get("anyOf") or schema.get("oneOf")
    if variants:
        return UnionType(
            tuple(type_expression(variant) for variant in variants),
            discriminator=schema.get("discriminator", {}).get("propertyName"),
        )
    return _primitive(schema)


def ref_name(schema: dict[str, Any]) -> str:
    return schema["$ref"].rsplit("/", 1)[-1]


def _roots(
    operations: tuple[RouteDecl, ...],
    notifications: tuple[NotificationDecl, ...],
) -> set[str]:
    roots = {
        operation.params_model
        for operation in operations
        if operation.params_model is not None
    }
    for operation in operations:
        roots.update(named_types(operation.result))
        for parameter in operation.params:
            roots.update(named_types(parameter.type))
        for error in operation.errors:
            if error.data is not None:
                roots.update(named_types(error.data))
    for notification in notifications:
        roots.update(named_types(notification.payload))
        roots.update(named_types(notification.message))
    return roots


def _reachable(
    declarations: tuple[Declaration, ...],
    roots: set[str],
) -> tuple[Declaration, ...]:
    """Keep only the declarations a client reaches from its own API surface."""
    by_name = {declaration.name: declaration for declaration in declarations}
    keep: set[str] = set()
    pending = [name for name in roots if name in by_name]
    while pending:
        name = pending.pop()
        if name in keep:
            continue
        keep.add(name)
        pending.extend(
            dependency
            for dependency in _dependencies(by_name[name])
            if dependency in by_name and dependency not in keep
        )
    return tuple(
        declaration for declaration in declarations if declaration.name in keep
    )


def _dependencies(declaration: Declaration) -> set[str]:
    if isinstance(declaration, ModelDecl):
        return {
            name
            for model_field in declaration.fields
            for name in named_types(model_field.type)
        }
    if isinstance(declaration, AliasDecl):
        return named_types(declaration.target)
    return set()


def _schema_declarations(
    schemas: dict[str, Any],
    discriminator_fields: dict[str, EnumLiteralType],
) -> tuple[Declaration, ...]:
    declarations: list[Declaration] = []
    aliases: list[Declaration] = []
    for name, schema in schemas.items():
        if "enum" in schema:
            declarations.append(_enum_declaration(name, schema))
        elif schema.get("type") == "object":
            declarations.append(_model(name, schema, discriminator_fields))
        else:
            aliases.append(AliasDecl(name, type_expression(schema)))
    return (*declarations, *aliases)


def _enum_declaration(name: str, schema: dict[str, Any]) -> EnumDecl:
    values = schema["enum"]
    return EnumDecl(
        name,
        tuple(EnumMember(_member_name(value), value) for value in values),
        integral=all(isinstance(value, int) for value in values),
    )


def _model(
    name: str,
    schema: dict[str, Any],
    discriminator_fields: dict[str, EnumLiteralType],
) -> ModelDecl:
    required = set(schema.get("required", ()))
    discriminator = discriminator_fields.get(name)
    fields = tuple(
        _field(field_name, field_schema, field_name in required, discriminator)
        for field_name, field_schema in _type_field_first(schema.get("properties", {}))
    )
    return ModelDecl(name, fields, closed=schema.get("additionalProperties") is False)


def _field(
    name: str,
    schema: dict[str, Any],
    required: bool,
    discriminator: EnumLiteralType | None,
) -> FieldDecl:
    if discriminator is not None and name == "type":
        return FieldDecl(name, discriminator, required=True, has_default=True)
    if "default" in schema:
        return FieldDecl(
            name,
            type_expression(schema),
            required=required,
            default=schema["default"],
            has_default=True,
        )
    return FieldDecl(name, type_expression(schema), required=required)


def _type_field_first(properties: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(properties.items(), key=lambda item: item[0] != "type")


def _primitive(schema: dict[str, Any]) -> TypeExpr:
    schema_type = schema.get("type")
    if schema_type == "string":
        return PrimitiveType(
            _STRING_FORMATS.get(schema.get("format", ""), Primitive.STRING)
        )
    if schema_type == "array":
        return ListType(type_expression(schema.get("items", {})))
    if schema_type == "object":
        return MapType(type_expression(schema.get("additionalProperties", {})))
    if schema_type in _SCALARS:
        return PrimitiveType(_SCALARS[schema_type])
    return PrimitiveType(Primitive.ANY)


def _discriminators(
    schemas: dict[str, Any],
) -> tuple[tuple[EnumDecl, ...], dict[str, EnumLiteralType]]:
    enums: list[EnumDecl] = []
    fields: dict[str, EnumLiteralType] = {}
    for alias_name, schema in schemas.items():
        discriminator = schema.get("discriminator")
        if not discriminator or discriminator.get("propertyName") != "type":
            continue
        enum_name = f"{alias_name}Type"
        members: list[EnumMember] = []
        for value, reference in discriminator["mapping"].items():
            member = _member_name(value)
            members.append(EnumMember(member, value))
            fields[reference.rsplit("/", 1)[-1]] = EnumLiteralType(enum_name, member)
        enums.append(EnumDecl(enum_name, tuple(members)))
    return tuple(enums), fields


def _route(method: dict[str, Any]) -> RouteDecl:
    if "x-rpc-request-schema" not in method:
        raise UnsupportedSchemaError(
            f"Method {method['name']} carries no x-rpc-request-schema; "
            "the document was not rendered by pyrpckit"
        )
    *path, operation_name = method["name"].split(".")
    params_schema = method.get("x-rpc-params-schema")
    return RouteDecl(
        rpc_name=method["name"],
        operation_name=operation_name,
        path=tuple(path),
        method_member=_member_name(method["name"]),
        params=tuple(_parameter(parameter) for parameter in method.get("params", ())),
        params_model=None if params_schema is None else ref_name(params_schema),
        result=type_expression(method["result"]["schema"]),
        summary=method.get("summary", ""),
        description=method.get("description", ""),
        tags=tuple(tag["name"] for tag in method.get("tags", ())),
        errors=tuple(_error(error) for error in method.get("errors", ())),
        server_names=tuple(server["name"] for server in method.get("servers", ())),
        deprecated=bool(method.get("deprecated", False)),
    )


def _parameter(parameter: dict[str, Any]) -> ParamDecl:
    schema = parameter["schema"]
    return ParamDecl(
        parameter["name"],
        type_expression(schema),
        required=bool(parameter.get("required")),
        default=schema.get("default"),
        has_default="default" in schema,
    )


def _error(error: dict[str, Any]) -> ErrorDecl:
    data_schema = error.get("x-rpckit-data-schema")
    return ErrorDecl(
        code=error["code"],
        message=error["message"],
        name=error.get("x-rpckit-name"),
        data=None if data_schema is None else type_expression(data_schema),
    )


def _notifications(document: dict[str, Any]) -> tuple[NotificationDecl, ...]:
    return tuple(
        NotificationDecl(
            rpc_name=notification["name"],
            payload=type_expression(notification["payload"]),
            message=type_expression(notification["message"]),
            summary=notification.get("summary", ""),
            server_names=tuple(
                server["name"] for server in notification.get("servers", ())
            ),
        )
        for notification in document.get("x-rpc-notifications", ())
    )


def _servers(document: dict[str, Any]) -> tuple[ServerDecl, ...]:
    return tuple(
        ServerDecl(
            name=server["name"],
            url=server["url"],
            summary=server.get("summary", ""),
            description=server.get("description", ""),
            variables=tuple(
                ServerVariableDecl(
                    name=name,
                    default=variable["default"],
                    description=variable.get("description", ""),
                    enum=tuple(variable.get("enum", ())),
                )
                for name, variable in server.get("variables", {}).items()
            ),
            transport=server.get("x-rpckit-transport"),
        )
        for server in document.get("servers", ())
    )


def _api_tree(routes: Iterable[RouteDecl]) -> tuple[ApiNode, ...]:
    tree: dict[str, Any] = {}
    for route in routes:
        cursor = tree
        for segment in route.path:
            cursor = cursor.setdefault(segment, {"$operations": []})
        cursor["$operations"].append(route)
    return tuple(_api_node(segment, node, ()) for segment, node in tree.items())


def _api_node(segment: str, value: dict[str, Any], parent: tuple[str, ...]) -> ApiNode:
    path = (*parent, segment)
    return ApiNode(
        segment=segment,
        path=path,
        operations=tuple(value.get("$operations", ())),
        children=tuple(
            _api_node(child_segment, child, path)
            for child_segment, child in value.items()
            if child_segment != "$operations"
        ),
    )


def _walk_api(nodes: tuple[ApiNode, ...]) -> Iterable[ApiNode]:
    for node in nodes:
        yield node
        yield from _walk_api(node.children)


def _member_name(value: str | int) -> str:
    if isinstance(value, int):
        sign = "NEG_" if value < 0 else ""
        return f"VALUE_{sign}{abs(value)}"
    identifier = re.sub(r"\W", "_", value)
    if identifier[:1].isdigit():
        identifier = f"_{identifier}"
    return identifier.upper()


_SCALARS = {
    "integer": Primitive.INTEGER,
    "number": Primitive.NUMBER,
    "boolean": Primitive.BOOLEAN,
    "null": Primitive.NULL,
}

_STRING_FORMATS = {
    "uuid": Primitive.UUID,
    "date-time": Primitive.DATETIME,
}
