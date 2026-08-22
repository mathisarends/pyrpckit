import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

type TypeExpr = (
    PrimitiveType | LiteralType | EnumLiteralType | NamedType | ListType | MapType | UnionType
)
type Declaration = EnumDecl | ModelDecl | AliasDecl

METHOD_ENUM_NAME = "RpcMethod"


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


@dataclass(frozen=True, slots=True)
class OperationDecl:
    rpc_name: str
    name: str
    method_member: str
    params: tuple[ParamDecl, ...]
    params_model: str
    result: TypeExpr
    summary: str = ""


@dataclass(frozen=True, slots=True)
class NamespaceDecl:
    name: str
    operations: tuple[OperationDecl, ...]


@dataclass(frozen=True, slots=True)
class NotificationDecl:
    rpc_name: str
    payload: TypeExpr
    message: TypeExpr
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ClientIr:
    title: str
    version: str
    declarations: tuple[Declaration, ...] = ()
    method_enum: EnumDecl = field(default_factory=lambda: EnumDecl(METHOD_ENUM_NAME, ()))
    namespaces: tuple[NamespaceDecl, ...] = ()
    root_operations: tuple[OperationDecl, ...] = ()
    notifications: tuple[NotificationDecl, ...] = ()

    @property
    def models(self) -> tuple[ModelDecl, ...]:
        return tuple(
            declaration for declaration in self.declarations if isinstance(declaration, ModelDecl)
        )

    @property
    def operations(self) -> tuple[OperationDecl, ...]:
        return self.root_operations + tuple(
            operation for namespace in self.namespaces for operation in namespace.operations
        )


def build_ir(document: dict[str, Any]) -> ClientIr:
    """Lower an OpenRPC document into the language-neutral client model."""
    schemas: dict[str, Any] = document.get("components", {}).get("schemas", {})
    methods: list[dict[str, Any]] = document.get("methods", [])
    discriminator_enums, discriminator_fields = _discriminators(schemas)
    grouped = _grouped_operations(methods)
    info = document.get("info", {})
    namespaces = tuple(
        NamespaceDecl(name, operations) for name, operations in grouped.items() if name
    )
    root_operations = grouped.get("", ())
    notifications = _notifications(document)
    declarations = (
        *discriminator_enums,
        *_schema_declarations(schemas, discriminator_fields),
    )
    return ClientIr(
        title=info.get("title", "RPC"),
        version=str(info.get("version", "0.0.0")),
        declarations=_reachable(
            declarations,
            _roots(namespaces, root_operations, notifications),
        ),
        method_enum=EnumDecl(
            METHOD_ENUM_NAME,
            tuple(EnumMember(_member_name(method["name"]), method["name"]) for method in methods),
        ),
        namespaces=namespaces,
        root_operations=root_operations,
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
    namespaces: tuple[NamespaceDecl, ...],
    root_operations: tuple[OperationDecl, ...],
    notifications: tuple[NotificationDecl, ...],
) -> set[str]:
    operations = root_operations + tuple(
        operation for namespace in namespaces for operation in namespace.operations
    )
    roots = {operation.params_model for operation in operations}
    for operation in operations:
        roots.update(named_types(operation.result))
        for parameter in operation.params:
            roots.update(named_types(parameter.type))
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
    return tuple(declaration for declaration in declarations if declaration.name in keep)


def _dependencies(declaration: Declaration) -> set[str]:
    if isinstance(declaration, ModelDecl):
        return {
            name for model_field in declaration.fields for name in named_types(model_field.type)
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
        return PrimitiveType(_STRING_FORMATS.get(schema.get("format", ""), Primitive.STRING))
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


def _grouped_operations(
    methods: list[dict[str, Any]],
) -> dict[str, tuple[OperationDecl, ...]]:
    grouped: dict[str, list[OperationDecl]] = {}
    for method in methods:
        namespace, _, operation = method["name"].rpartition(".")
        grouped.setdefault(namespace, []).append(_operation(method, operation))
    return {name: tuple(operations) for name, operations in grouped.items()}


def _operation(method: dict[str, Any], name: str) -> OperationDecl:
    params_schema = method.get("x-rpc-params-schema")
    if params_schema is None:
        raise UnsupportedSchemaError(
            f"Method {method['name']} carries no x-rpc-params-schema; "
            "the document was not rendered by pyrpckit"
        )
    return OperationDecl(
        rpc_name=method["name"],
        name=name,
        method_member=_member_name(method["name"]),
        params=tuple(
            ParamDecl(
                parameter["name"],
                type_expression(parameter["schema"]),
                required=bool(parameter.get("required")),
            )
            for parameter in method.get("params", ())
        ),
        params_model=ref_name(params_schema),
        result=type_expression(method["result"]["schema"]),
        summary=method.get("summary", ""),
    )


def _notifications(document: dict[str, Any]) -> tuple[NotificationDecl, ...]:
    return tuple(
        NotificationDecl(
            rpc_name=notification["name"],
            payload=type_expression(notification["payload"]),
            message=type_expression(notification["message"]),
            summary=notification.get("summary", ""),
        )
        for notification in document.get("x-rpc-notifications", ())
    )


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
