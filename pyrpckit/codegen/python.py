import json
import keyword
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pyrpckit.codegen.ir import (
    AliasDecl,
    ClientIr,
    Declaration,
    EnumDecl,
    EnumLiteralType,
    FieldDecl,
    ListType,
    LiteralType,
    MapType,
    ModelDecl,
    NamedType,
    NamespaceDecl,
    OperationDecl,
    Primitive,
    PrimitiveType,
    TypeExpr,
    UnionType,
    named_types,
)

TRANSPORT_MODULE = "pyrpckit.client"


class _Imports:
    def __init__(self) -> None:
        self._modules: dict[str, set[str]] = {}

    def add(self, module: str, *names: str) -> None:
        self._modules.setdefault(module, set()).update(names)

    def render(self) -> str:
        groups: dict[int, list[str]] = {}
        for module in sorted(self._modules):
            groups.setdefault(_import_group(module), []).append(
                _import_line(module, sorted(self._modules[module]))
            )
        return "\n\n".join("\n".join(groups[rank]) for rank in sorted(groups))


def _import_line(module: str, names: list[str]) -> str:
    inline = f"from {module} import {', '.join(names)}"
    if len(inline) <= 88:
        return inline
    imported = "".join(f"    {name},\n" for name in names)
    return f"from {module} import (\n{imported})"


def _import_group(module: str) -> int:
    root = module.split(".", 1)[0]
    if root in _STANDARD_LIBRARY:
        return 0
    if root in ("pydantic", "pyrpckit"):
        return 1
    return 2


@dataclass(frozen=True, slots=True)
class PythonClientOptions:
    package: str
    client_name: str = "RpcClient"
    base_model_name: str = "RpcModel"
    namespace_class_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"

    def namespace_class(self, namespace: str) -> str:
        override = self.namespace_class_names.get(namespace)
        if override:
            return override
        name = _pascal_case(namespace)
        if f"{name}Client" == self.client_name:
            return f"{name}NamespaceClient"
        return f"{name}Client"


def render_models(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    blocks = [_enum_block(ir.method_enum)]
    if any(model.closed for model in ir.models):
        imports.add("pydantic", "BaseModel", "ConfigDict")
        blocks.append(
            f"class {options.base_model_name}(BaseModel):\n"
            '    model_config = ConfigDict(extra="forbid")'
        )
    blocks.extend(
        _declaration_block(declaration, options, imports) for declaration in ir.declarations
    )
    rebuilds = [f"{model.name}.model_rebuild()" for model in ir.models]
    if any(not model.closed for model in ir.models):
        imports.add("pydantic", "BaseModel")
    if ir.method_enum.members:
        imports.add("enum", "StrEnum")
    body = "\n\n\n".join(blocks)
    if rebuilds:
        body = f"{body}\n\n\n" + "\n".join(rebuilds)
    return _module(
        options,
        imports,
        body,
        future_annotations=True,
    )


def render_namespace(
    ir: ClientIr,
    namespace: NamespaceDecl,
    options: PythonClientOptions,
) -> str:
    imports = _Imports()
    imports.add(TRANSPORT_MODULE, "RpcTransport")
    lines = [
        f"class {options.namespace_class(namespace.name)}:",
        "    def __init__(self, transport: RpcTransport) -> None:",
        "        self._transport = transport",
    ]
    for operation in namespace.operations:
        lines.extend(["", *_operation_lines(ir, operation, options, imports)])
    return _module(options, imports, "\n".join(lines))


def render_namespaces_init(ir: ClientIr, options: PythonClientOptions) -> str:
    classes = [options.namespace_class(space.name) for space in ir.namespaces]
    imports = _Imports()
    for namespace, class_name in zip(ir.namespaces, classes, strict=True):
        imports.add(f"{options.package}.namespaces.{namespace.name}", class_name)
    return _module(options, imports, _exports(classes))


def render_client(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add(TRANSPORT_MODULE, "RpcTransport")
    imports.add("typing", "Self")
    lines = [
        f"class {options.client_name}:",
        "    def __init__(self, transport: RpcTransport) -> None:",
        "        self._transport = transport",
    ]
    for namespace in ir.namespaces:
        class_name = options.namespace_class(namespace.name)
        imports.add(f"{options.package}.namespaces", class_name)
        attribute = _identifier(namespace.name)
        lines.append(f"        self.{attribute} = {class_name}(transport)")
    for operation in ir.root_operations:
        lines.extend(["", *_operation_lines(ir, operation, options, imports)])
    if ir.notifications:
        lines.extend(["", *_notifications_lines(ir, options, imports)])
    lines.extend(
        [
            "",
            "    async def close(self) -> None:",
            "        await self._transport.close()",
            "",
            "    async def __aenter__(self) -> Self:",
            "        return self",
            "",
            "    async def __aexit__(self, *args: object) -> None:",
            "        await self.close()",
        ]
    )
    return _module(options, imports, "\n".join(lines))


def render_package_init(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add(f"{options.package}.client", options.client_name)
    imports.add(f"{options.package}.models", ir.method_enum.name)
    exported = [options.client_name, ir.method_enum.name]
    for namespace in ir.namespaces:
        class_name = options.namespace_class(namespace.name)
        imports.add(f"{options.package}.namespaces", class_name)
        exported.append(class_name)
    return _module(options, imports, _exports(exported))


def render_files(ir: ClientIr, options: PythonClientOptions) -> dict[str, str]:
    """Render the generated package as a mapping of relative path to content."""
    files = {
        "__init__.py": render_package_init(ir, options),
        "models.py": render_models(ir, options),
        "client.py": render_client(ir, options),
    }
    if ir.namespaces:
        files["namespaces/__init__.py"] = render_namespaces_init(ir, options)
        files.update(
            {
                f"namespaces/{namespace.name}.py": render_namespace(ir, namespace, options)
                for namespace in ir.namespaces
            }
        )
    return files


def _operation_lines(
    ir: ClientIr,
    operation: OperationDecl,
    options: PythonClientOptions,
    imports: _Imports,
) -> list[str]:
    models = f"{options.package}.models"
    imports.add(models, operation.params_model, ir.method_enum.name)
    imports.add(models, *named_types(operation.result))
    for parameter in operation.params:
        imports.add(models, *named_types(parameter.type))
    result = _annotation(operation.result, imports)
    name = _identifier(operation.name)
    if operation.params:
        lines = [f"    async def {name}(", "        self,", "        *,"]
        lines.extend(
            f"        {_identifier(parameter.name)}: "
            f"{_parameter_annotation(parameter.type, parameter.required, imports)},"
            for parameter in operation.params
        )
        lines.append(f"    ) -> {result}:")
    else:
        lines = [f"    async def {name}(self) -> {result}:"]
    if operation.summary:
        lines.append(f'        """{_docstring(operation.summary)}"""')
    lines.extend(_operation_body(ir, operation, imports))
    return lines


def _operation_body(
    ir: ClientIr,
    operation: OperationDecl,
    imports: _Imports,
) -> list[str]:
    if operation.params:
        lines = [f"        params = {operation.params_model}("]
        lines.extend(
            f"            {_identifier(parameter.name)}={_identifier(parameter.name)},"
            for parameter in operation.params
        )
        lines.append("        )")
    else:
        lines = [f"        params = {operation.params_model}()"]
    call = [
        f"            {ir.method_enum.name}.{operation.method_member},",
        '            params.model_dump(mode="json", exclude_none=True),',
        "        )",
    ]
    if _is_null(operation.result):
        return [*lines, "        await self._transport.request(", *call]
    return [
        *lines,
        "        result = await self._transport.request(",
        *call,
        f"        return {_validation(ir, operation.result, imports)}",
    ]


def _notifications_lines(
    ir: ClientIr,
    options: PythonClientOptions,
    imports: _Imports,
) -> list[str]:
    imports.add("collections.abc", "AsyncIterator")
    imports.add("pydantic", "TypeAdapter")
    messages = UnionType(tuple(n.message for n in ir.notifications))
    annotation = _annotation(_collapse(messages), imports)
    for notification in ir.notifications:
        if isinstance(notification.message, NamedType):
            imports.add(f"{options.package}.models", notification.message.name)
    return [
        f"    async def notifications(self) -> AsyncIterator[{annotation}]:",
        f"        adapter = TypeAdapter({annotation})",
        "        async for message in self._transport.notifications():",
        "            yield adapter.validate_python(message)",
    ]


def _declaration_block(
    declaration: Declaration,
    options: PythonClientOptions,
    imports: _Imports,
) -> str:
    if isinstance(declaration, EnumDecl):
        imports.add("enum", "IntEnum" if declaration.integral else "StrEnum")
        return _enum_block(declaration)
    if isinstance(declaration, ModelDecl):
        return _model_block(declaration, options, imports)
    return _alias_block(declaration, imports)


def _enum_block(declaration: EnumDecl) -> str:
    base = "IntEnum" if declaration.integral else "StrEnum"
    lines = [f"class {declaration.name}({base}):"]
    lines.extend(f"    {member.name} = {_literal(member.value)}" for member in declaration.members)
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def _model_block(
    declaration: ModelDecl,
    options: PythonClientOptions,
    imports: _Imports,
) -> str:
    base = options.base_model_name if declaration.closed else "BaseModel"
    lines = [f"class {declaration.name}({base}):"]
    lines.extend(_field_line(model_field, imports) for model_field in declaration.fields)
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def _field_line(model_field: FieldDecl, imports: _Imports) -> str:
    annotation = _annotation(model_field.type, imports)
    if isinstance(model_field.type, EnumLiteralType):
        default = f"{model_field.type.enum}.{model_field.type.member}"
    elif model_field.has_default:
        default = _literal(model_field.default)
    elif model_field.required:
        return f"    {_identifier(model_field.name)}: {annotation}"
    else:
        annotation = _union([annotation, "None"])
        default = "None"
    return f"    {_identifier(model_field.name)}: {annotation} = {default}"


def _alias_block(declaration: AliasDecl, imports: _Imports) -> str:
    target = declaration.target
    if not isinstance(target, UnionType) or len(target.members) < 3:
        return f"type {declaration.name} = {_annotation(target, imports)}"
    variants = [_annotation(member, imports) for member in target.members]
    inline = " | ".join(dict.fromkeys(variants))
    if len(inline) <= 72:
        union = f"    {inline}"
    else:
        union = "\n".join(
            f"    {variant}" if index == 0 else f"    | {variant}"
            for index, variant in enumerate(variants)
        )
    if target.discriminator is None:
        return f"type {declaration.name} = (\n{union}\n)"
    imports.add("typing", "Annotated")
    imports.add("pydantic", "Field")
    return (
        f"type {declaration.name} = Annotated[\n"
        f"{union},\n"
        f"    Field(discriminator={_literal(target.discriminator)}),\n"
        "]"
    )


def _annotation(expression: TypeExpr, imports: _Imports) -> str:
    if isinstance(expression, PrimitiveType):
        return _primitive_annotation(expression.primitive, imports)
    if isinstance(expression, NamedType):
        return expression.name
    if isinstance(expression, LiteralType):
        imports.add("typing", "Literal")
        return f"Literal[{_literal(expression.value)}]"
    if isinstance(expression, EnumLiteralType):
        imports.add("typing", "Literal")
        return f"Literal[{expression.enum}.{expression.member}]"
    if isinstance(expression, ListType):
        return f"list[{_annotation(expression.item, imports)}]"
    if isinstance(expression, MapType):
        return f"dict[str, {_annotation(expression.value, imports)}]"
    return _union_annotation(expression, imports)


def _union_annotation(expression: UnionType, imports: _Imports) -> str:
    union = _union([_annotation(member, imports) for member in expression.members])
    if expression.discriminator is None:
        return union
    imports.add("typing", "Annotated")
    imports.add("pydantic", "Field")
    return f"Annotated[{union}, Field(discriminator={_literal(expression.discriminator)})]"


def _primitive_annotation(primitive: Primitive, imports: _Imports) -> str:
    if primitive is Primitive.UUID:
        imports.add("uuid", "UUID")
        return "UUID"
    if primitive is Primitive.DATETIME:
        imports.add("datetime", "datetime")
        return "datetime"
    if primitive is Primitive.ANY:
        imports.add("typing", "Any")
        return "Any"
    return _SCALAR_ANNOTATIONS[primitive]


def _parameter_annotation(
    expression: TypeExpr,
    required: bool,
    imports: _Imports,
) -> str:
    annotation = _annotation(expression, imports)
    if required:
        return annotation
    return f"{_union([annotation, 'None'])} = None"


def _validation(ir: ClientIr, expression: TypeExpr, imports: _Imports) -> str:
    annotation = _annotation(expression, imports)
    if isinstance(expression, NamedType) and any(
        model.name == expression.name for model in ir.models
    ):
        return f"{annotation}.model_validate(result)"
    imports.add("pydantic", "TypeAdapter")
    return f"TypeAdapter({annotation}).validate_python(result)"


def _module(
    options: PythonClientOptions,
    imports: _Imports,
    body: str,
    *,
    future_annotations: bool = False,
) -> str:
    header = f"# Generated by pyrpckit from {options.source}.\n# Do not edit this file manually.\n"
    preamble = "from __future__ import annotations\n\n" if future_annotations else ""
    rendered = imports.render()
    if rendered:
        rendered = f"{rendered}\n{_blank_lines_after_imports(body)}"
    return f"{header}{preamble}{rendered}{body}\n"


def _blank_lines_after_imports(body: str) -> str:
    """Two blank lines before a definition, one before anything else."""
    return "\n\n" if body.startswith(("class ", "def ", "async def ", "@")) else "\n"


def _exports(names: Iterable[str]) -> str:
    exported = "\n".join(f'    "{name}",' for name in sorted(names))
    return f"__all__ = [\n{exported}\n]"


def _collapse(expression: UnionType) -> TypeExpr:
    return expression.members[0] if len(expression.members) == 1 else expression


def _is_null(expression: TypeExpr) -> bool:
    return isinstance(expression, PrimitiveType) and expression.primitive is Primitive.NULL


def _union(annotations: list[str]) -> str:
    return " | ".join(dict.fromkeys(annotations))


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


def _docstring(summary: str) -> str:
    return summary.replace('"""', "'''").strip()


def _identifier(value: str) -> str:
    identifier = re.sub(r"\W", "_", value)
    if identifier[:1].isdigit() or keyword.iskeyword(identifier):
        identifier = f"{identifier}_"
    return identifier


def _pascal_case(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[._\- ]", value) if part)


_STANDARD_LIBRARY = frozenset({"collections", "datetime", "enum", "typing", "uuid", "__future__"})

_SCALAR_ANNOTATIONS = {
    Primitive.STRING: "str",
    Primitive.INTEGER: "int",
    Primitive.NUMBER: "float",
    Primitive.BOOLEAN: "bool",
    Primitive.NULL: "None",
}
