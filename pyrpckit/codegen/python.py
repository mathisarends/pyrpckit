import json
import keyword
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pyrpckit.codegen._names import (
    ApiViewNode,
    api_view,
    assert_unique_names,
    pascal_case,
    snake_case,
)
from pyrpckit.codegen._templating import render_template
from pyrpckit.codegen.ir import (
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
    Primitive,
    PrimitiveType,
    RouteDecl,
    TypeExpr,
    UnionType,
    UnsupportedSchemaError,
    named_types,
)

RUNTIME_MODULE = "pyrpckit.client"


class _Imports:
    def __init__(self) -> None:
        self._modules: dict[str, set[str]] = {}

    def add(self, module: str, *names: str) -> None:
        if names:
            self._modules.setdefault(module, set()).update(names)

    def render(self) -> str:
        groups: dict[int, list[str]] = {}
        for module in sorted(self._modules):
            groups.setdefault(_import_group(module), []).append(
                _import_line(module, sorted(self._modules[module]))
            )
        return "\n\n".join("\n".join(groups[rank]) for rank in sorted(groups))


@dataclass(frozen=True, slots=True)
class PythonClientOptions:
    package: str
    client_name: str | None = None
    base_model_name: str = "RpcModel"
    api_root: str | None = None
    api_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"


def render_files(ir: ClientIr, options: PythonClientOptions) -> dict[str, str]:
    """Render one generated Python leaf package."""
    view = api_view(ir, api_root=options.api_root, api_names=options.api_names)
    client_name = _client_name(ir, options)
    _validate(ir, view.root_operations, view.nodes, options, client_name)
    files = {
        "__init__.py": _render_package_init(ir, options, client_name),
        "client.py": _render_client(
            ir, view.root_operations, view.nodes, options, client_name
        ),
        "errors.py": _render_errors(ir, options),
        "metadata.py": _render_metadata(ir, options),
        "models.py": _render_models(ir, options),
    }
    if ir.servers:
        files["endpoints.py"] = _render_endpoints(ir, options)
    if view.nodes:
        files["api/__init__.py"] = _module(options, _Imports(), "")
        for node in _walk(view.nodes):
            files[_api_file(node)] = _render_api(node, ir, options)
    return files


def _render_models(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    blocks: list[str] = []
    if ir.models:
        imports.add("pydantic", "BaseModel")
        blocks.append(f"class {options.base_model_name}(BaseModel):\n    pass")
    blocks.extend(
        _declaration_block(declaration, options, imports)
        for declaration in ir.declarations
    )
    rebuilds = [f"{_schema_name(model.name)}.model_rebuild()" for model in ir.models]
    body = "\n\n\n".join(blocks)
    if rebuilds:
        body = f"{body}\n\n\n" + "\n".join(rebuilds)
    return _module(options, imports, body, future_annotations=True)


def _render_metadata(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add(RUNTIME_MODULE, "RpcContractInfo", "RpcRouteInfo")
    protocol_version = repr(ir.protocol_version)
    blocks = [
        "CONTRACT = RpcContractInfo(\n"
        f"    title={_literal(ir.title)},\n"
        f"    version={_literal(ir.version)},\n"
        f"    protocol_version={protocol_version},\n"
        ")"
    ]
    for route in ir.operations:
        blocks.append(
            f"{route.method_member} = RpcRouteInfo(\n"
            f"    method={_literal(route.rpc_name)},\n"
            f"    summary={_literal(route.summary)},\n"
            f"    tags={_tuple_literal(route.tags)},\n"
            f"    deprecated={route.deprecated!r},\n"
            f"    error_codes={tuple(error.code for error in route.errors)!r},\n"
            f"    server_names={_tuple_literal(route.server_names)},\n"
            ")"
        )
    return _module(options, imports, "\n\n\n".join(blocks))


def _render_endpoints(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add(RUNTIME_MODULE, "RpcServerInfo", "RpcServerVariable")
    server_blocks: list[str] = []
    helper_blocks: list[str] = []
    for server in ir.servers:
        variables = "\n".join(
            f"            {_literal(variable.name)}: RpcServerVariable(\n"
            f"                default={_literal(variable.default)},\n"
            f"                description={_literal(variable.description)},\n"
            f"                enum={_tuple_literal(variable.enum)},\n"
            "            ),"
            for variable in server.variables
        )
        mapping = "{}" if not variables else "{\n" + variables + "\n        }"
        server_blocks.append(
            f"    {_constant(server.name)} = RpcServerInfo(\n"
            f"        name={_literal(server.name)},\n"
            f"        url={_literal(server.url)},\n"
            f"        summary={_literal(server.summary)},\n"
            f"        description={_literal(server.description)},\n"
            f"        variables={mapping},\n"
            f"        transport={_literal(server.transport)},\n"
            "    )"
        )
        helper_blocks.append(_endpoint_helper(server))
    body = "class Servers:\n" + "\n\n".join(server_blocks)
    if helper_blocks:
        body += "\n\n\n" + "\n\n\n".join(helper_blocks)
    return _module(options, imports, body)


def _endpoint_helper(server: Any) -> str:
    name = f"{_identifier(server.name)}_url"
    if not server.variables:
        constant = _constant(server.name)
        return f"def {name}() -> str:\n    return Servers.{constant}.resolve()"
    lines = [f"def {name}(", "    *,"]
    for variable in server.variables:
        lines.append(
            f"    {_identifier(variable.name)}: str = {_literal(variable.default)},"
        )
    lines.extend(
        [
            ") -> str:",
            f"    return Servers.{_constant(server.name)}.resolve(",
            "        {",
        ]
    )
    lines.extend(
        f"            {_literal(variable.name)}: {_identifier(variable.name)},"
        for variable in server.variables
    )
    lines.extend(["        }", "    )"])
    return "\n".join(lines)


def _render_errors(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    blocks: list[str] = []
    seen: set[str] = set()
    for route in ir.operations:
        for error in route.errors:
            if error.name is None or error.name in seen:
                continue
            seen.add(error.name)
            imports.add("typing", "ClassVar")
            imports.add(RUNTIME_MODULE, "RpcRemoteError")
            if error.data is not None:
                imports.add(f"{options.package}.models", *_model_names(error.data))
            lines = [
                f"class {_schema_name(error.name)}Error(RpcRemoteError):",
                f"    code: ClassVar[int] = {error.code}",
            ]
            if error.data is not None:
                lines.append(f"    data: {_annotation(error.data, imports)}")
            blocks.append("\n".join(lines))
    body = "__all__: list[str] = []" if not blocks else "\n\n\n".join(blocks)
    return _module(options, imports, body)


def _render_api(node: ApiViewNode, ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add(RUNTIME_MODULE, "RpcClientCore")
    class_name = _api_class(node.path)
    constants: list[str] = []
    lines = [
        f"class {class_name}:",
        "    def __init__(self, rpc: RpcClientCore) -> None:",
        "        self._rpc = rpc",
    ]
    for child in node.children:
        child_class = _api_class(child.path)
        imports.add(f"{options.package}.{_api_module(child)}", child_class)
        lines.append(f"        self.{_identifier(child.segment)} = {child_class}(rpc)")
    for route in node.operations:
        constants.append(_result_adapter(route, imports, options))
        lines.extend(["", *_operation_lines(route, imports, options)])
    body = _constants_and_definition(constants, "\n".join(lines))
    return _module(options, imports, body)


def _render_client(
    ir: ClientIr,
    root_operations: tuple[RouteDecl, ...],
    nodes: tuple[ApiViewNode, ...],
    options: PythonClientOptions,
    client_name: str,
) -> str:
    imports = _Imports()
    imports.add("typing", "Self")
    imports.add(RUNTIME_MODULE, "RpcClientCore", "RpcTransport")
    constants: list[str] = []
    for route in root_operations:
        constants.append(_result_adapter(route, imports, options))
    event_type: TypeExpr | None = None
    message_type: TypeExpr | None = None
    if ir.events:
        imports.add("collections.abc", "AsyncIterator")
        imports.add("pydantic", "TypeAdapter")
        event_type = _collapse(UnionType(tuple(event.payload for event in ir.events)))
        message_type = _collapse(UnionType(tuple(event.message for event in ir.events)))
        imports.add(
            f"{options.package}.models",
            *_model_names(event_type),
            *_model_names(message_type),
        )
        constants.append(
            "_EVENT_MESSAGE_ADAPTER = TypeAdapter("
            f"{_annotation(message_type, imports)})"
        )
    lines = [
        f"class {client_name}:",
        "    def __init__(",
        "        self,",
        "        transport: RpcTransport,",
        "        *,",
        "        close_transport: bool = True,",
        "    ) -> None:",
        "        self._rpc = RpcClientCore(transport, close_transport=close_transport)",
    ]
    for node in nodes:
        api_class = _api_class(node.path)
        imports.add(f"{options.package}.{_api_module(node)}", api_class)
        lines.append(
            f"        self.{_identifier(node.segment)} = {api_class}(self._rpc)"
        )
    for route in root_operations:
        lines.extend(["", *_operation_lines(route, imports, options)])
    if event_type is not None:
        annotation = _annotation(event_type, imports)
        lines.extend(
            [
                "",
                f"    async def events(self) -> AsyncIterator[{annotation}]:",
                "        async for message in self._rpc.notifications():",
                "            notification = "
                "_EVENT_MESSAGE_ADAPTER.validate_python(message)",
                "            yield notification.params",
            ]
        )
    lines.extend(
        [
            "",
            "    async def close(self) -> None:",
            "        await self._rpc.close()",
            "",
            "    async def __aenter__(self) -> Self:",
            "        return self",
            "",
            "    async def __aexit__(self, *args: object) -> None:",
            "        await self.close()",
        ]
    )
    body = _constants_and_definition(constants, "\n".join(lines))
    return _module(options, imports, body)


def _render_package_init(
    ir: ClientIr,
    options: PythonClientOptions,
    client_name: str,
) -> str:
    imports = _Imports()
    imports.add(f"{options.package}.client", client_name)
    exported = [client_name]
    if ir.servers:
        imports.add(f"{options.package}.endpoints", "Servers")
        exported.append("Servers")
        for server in ir.servers:
            helper = f"{_identifier(server.name)}_url"
            imports.add(f"{options.package}.endpoints", helper)
            exported.append(helper)
    return _module(options, imports, _exports(exported))


def _operation_lines(
    route: RouteDecl,
    imports: _Imports,
    options: PythonClientOptions,
) -> list[str]:
    models = f"{options.package}.models"
    imports.add(f"{options.package}.metadata", route.method_member)
    if route.params_model is not None:
        imports.add(models, _schema_name(route.params_model))
    for parameter in route.params:
        imports.add(models, *_model_names(parameter.type))
    result = _annotation(route.result, imports)
    imports.add(models, *_model_names(route.result))
    name = _identifier(route.operation_name)
    if route.params:
        lines = [f"    async def {name}(", "        self,", "        *,"]
        lines.extend(
            f"        {_identifier(parameter.name)}: "
            f"{_parameter_annotation(parameter, imports)},"
            for parameter in route.params
        )
        lines.append(f"    ) -> {result}:")
    else:
        lines = [f"    async def {name}(self) -> {result}:"]
    description = route.summary or route.description
    if description:
        lines.append(f'        """{_docstring(description)}"""')
    lines.extend(_operation_body(route))
    return lines


def _operation_body(route: RouteDecl) -> list[str]:
    lines: list[str] = []
    if route.params_model is not None:
        lines.append("        values: dict[str, object] = {}")
        for parameter in route.params:
            name = _identifier(parameter.name)
            if parameter.required or parameter.has_default:
                lines.append(f"        values[{_literal(parameter.name)}] = {name}")
            else:
                lines.extend(
                    [
                        f"        if {name} is not UNSET:",
                        f"            values[{_literal(parameter.name)}] = {name}",
                    ]
                )
        model_name = _schema_name(route.params_model)
        lines.append(f"        params = {model_name}.model_validate(values)")
    lines.append("        return await self._rpc.request(")
    lines.append(f"            {route.method_member},")
    if route.params_model is not None:
        lines.extend(
            [
                "            params=params.model_dump("
                'mode="json", by_alias=True, exclude_unset=True),',
            ]
        )
    lines.append(f"            result_adapter=_{route.method_member}_RESULT_ADAPTER,")
    lines.append("        )")
    return lines


def _result_adapter(
    route: RouteDecl,
    imports: _Imports,
    options: PythonClientOptions,
) -> str:
    imports.add("pydantic", "TypeAdapter")
    imports.add(f"{options.package}.models", *_model_names(route.result))
    annotation = _annotation(route.result, imports)
    return f"_{route.method_member}_RESULT_ADAPTER = TypeAdapter({annotation})"


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
    return (
        f"type {_schema_name(declaration.name)} = "
        f"{_annotation(declaration.target, imports)}"
    )


def _enum_block(declaration: EnumDecl) -> str:
    base = "IntEnum" if declaration.integral else "StrEnum"
    lines = [f"class {_schema_name(declaration.name)}({base}):"]
    lines.extend(
        f"    {member.name} = {_literal(member.value)}"
        for member in declaration.members
    )
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def _model_block(
    declaration: ModelDecl,
    options: PythonClientOptions,
    imports: _Imports,
) -> str:
    lines = [f"class {_schema_name(declaration.name)}({options.base_model_name}):"]
    if declaration.closed:
        imports.add("pydantic", "ConfigDict")
        lines.append('    model_config = ConfigDict(extra="forbid")')
    lines.extend(
        _field_line(model_field, imports) for model_field in declaration.fields
    )
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def _field_line(field: FieldDecl, imports: _Imports) -> str:
    annotation = _annotation(field.type, imports)
    name = _identifier(field.name)
    if isinstance(field.type, EnumLiteralType):
        default = f"{_schema_name(field.type.enum)}.{field.type.member}"
    elif field.has_default:
        default = _literal(field.default)
    elif field.required:
        if name == field.name:
            return f"    {name}: {annotation}"
        imports.add("pydantic", "Field")
        return f"    {name}: {annotation} = Field(alias={_literal(field.name)})"
    else:
        if not _allows_none(field.type):
            annotation = _union((annotation, "None"))
        default = "None"
    if name != field.name:
        imports.add("pydantic", "Field")
        default = f"Field({default}, alias={_literal(field.name)})"
    return f"    {name}: {annotation} = {default}"


def _annotation(expression: TypeExpr, imports: _Imports) -> str:
    if isinstance(expression, PrimitiveType):
        return _primitive_annotation(expression.primitive, imports)
    if isinstance(expression, NamedType):
        return _schema_name(expression.name)
    if isinstance(expression, LiteralType):
        imports.add("typing", "Literal")
        return f"Literal[{_literal(expression.value)}]"
    if isinstance(expression, EnumLiteralType):
        imports.add("typing", "Literal")
        return f"Literal[{_schema_name(expression.enum)}.{expression.member}]"
    if isinstance(expression, ListType):
        return f"list[{_annotation(expression.item, imports)}]"
    if isinstance(expression, MapType):
        return f"dict[str, {_annotation(expression.value, imports)}]"
    union = _union(tuple(_annotation(member, imports) for member in expression.members))
    if expression.discriminator is None:
        return union
    imports.add("typing", "Annotated")
    imports.add("pydantic", "Field")
    return (
        f"Annotated[{union}, Field(discriminator={_literal(expression.discriminator)})]"
    )


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
    return {
        Primitive.STRING: "str",
        Primitive.INTEGER: "int",
        Primitive.NUMBER: "float",
        Primitive.BOOLEAN: "bool",
        Primitive.NULL: "None",
    }[primitive]


def _parameter_annotation(parameter: Any, imports: _Imports) -> str:
    annotation = _annotation(parameter.type, imports)
    if parameter.required:
        return annotation
    if parameter.has_default:
        return f"{annotation} = {_literal(parameter.default)}"
    imports.add(RUNTIME_MODULE, "UNSET", "UnsetType")
    return f"{_union((annotation, 'UnsetType'))} = UNSET"


def _validate(
    ir: ClientIr,
    root_operations: tuple[RouteDecl, ...],
    nodes: tuple[ApiViewNode, ...],
    options: PythonClientOptions,
    client_name: str,
) -> None:
    _assert_class_name(client_name, "client_name")
    _assert_class_name(options.base_model_name, "base_model_name")
    assert_unique_names(
        "schemas",
        (
            (declaration.name, pascal_case(declaration.name))
            for declaration in ir.declarations
        ),
    )
    for declaration in ir.declarations:
        generated_name = _schema_name(declaration.name)
        if generated_name in _RUNTIME_NAMES:
            raise UnsupportedSchemaError(
                f"Schema {declaration.name!r} collides with runtime import "
                f"{generated_name!r}"
            )
    for model in ir.models:
        assert_unique_names(
            f"model {model.name}",
            ((field.name, _identifier(field.name)) for field in model.fields),
        )
    for route in ir.operations:
        assert_unique_names(
            f"method {route.rpc_name}",
            (
                (parameter.name, _identifier(parameter.name))
                for parameter in route.params
            ),
        )
    client_members = [
        ("<client.close>", "close"),
        *((node.source_path[-1], _identifier(node.segment)) for node in nodes),
        *(
            (route.rpc_name, _identifier(route.operation_name))
            for route in root_operations
        ),
    ]
    if ir.events:
        client_members.append(("<client.events>", "events"))
    assert_unique_names("root client", client_members)
    _validate_nodes(nodes)
    assert_unique_names(
        "servers",
        ((server.name, _identifier(server.name)) for server in ir.servers),
    )
    for server in ir.servers:
        assert_unique_names(
            f"server {server.name}",
            (
                (variable.name, _identifier(variable.name))
                for variable in server.variables
            ),
        )


def _validate_nodes(nodes: tuple[ApiViewNode, ...]) -> None:
    for node in nodes:
        values = [
            (child.segment, _identifier(child.segment)) for child in node.children
        ]
        values.extend(
            (route.rpc_name, _identifier(route.operation_name))
            for route in node.operations
        )
        assert_unique_names(f"API path {'.'.join(node.path)}", values)
        _validate_nodes(node.children)


def _client_name(ir: ClientIr, options: PythonClientOptions) -> str:
    return options.client_name or f"{pascal_case(ir.title)}Client"


def _assert_class_name(value: str, option: str) -> None:
    if not value.isidentifier() or keyword.iskeyword(value):
        raise UnsupportedSchemaError(
            f"{option} must be a valid Python identifier: {value!r}"
        )


def _identifier(value: str) -> str:
    identifier = snake_case(value)
    if not identifier:
        raise UnsupportedSchemaError(
            f"Cannot derive a Python identifier from {value!r}"
        )
    if identifier[0].isdigit():
        identifier = f"_{identifier}"
    if keyword.iskeyword(identifier):
        identifier = f"{identifier}_"
    return identifier


def _schema_name(value: str) -> str:
    name = pascal_case(value)
    if not name:
        raise UnsupportedSchemaError(
            f"Cannot derive a Python class name from {value!r}"
        )
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _model_names(expression: TypeExpr) -> set[str]:
    return {_schema_name(name) for name in named_types(expression)}


def _constant(value: str) -> str:
    return _identifier(value).upper()


def _api_class(path: tuple[str, ...]) -> str:
    return f"{''.join(pascal_case(segment) for segment in path)}Api"


def _api_module(node: ApiViewNode) -> str:
    return "api." + ".".join(_identifier(segment) for segment in node.path)


def _api_file(node: ApiViewNode) -> str:
    path = "/".join(_identifier(segment) for segment in node.path)
    return f"api/{path}/__init__.py" if node.children else f"api/{path}.py"


def _walk(nodes: tuple[ApiViewNode, ...]) -> Iterable[ApiViewNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _collapse(expression: UnionType) -> TypeExpr:
    return expression.members[0] if len(expression.members) == 1 else expression


def _allows_none(expression: TypeExpr) -> bool:
    if isinstance(expression, PrimitiveType):
        return expression.primitive is Primitive.NULL
    if isinstance(expression, UnionType):
        return any(_allows_none(member) for member in expression.members)
    return False


def _constants_and_definition(constants: list[str], definition: str) -> str:
    if not constants:
        return definition
    return f"{'\n\n'.join(constants)}\n\n\n{definition}"


def _union(items: Iterable[str]) -> str:
    return " | ".join(dict.fromkeys(items))


def _docstring(value: str) -> str:
    return " ".join(value.split()).replace('"""', '"""')


def _module(
    options: PythonClientOptions,
    imports: _Imports,
    body: str,
    *,
    future_annotations: bool = False,
) -> str:
    rendered_imports = imports.render()
    import_spacing = ""
    if rendered_imports:
        if not body:
            import_spacing = "\n"
        elif body.startswith(("class ", "def ", "async def ", "@")):
            import_spacing = "\n\n\n"
        else:
            import_spacing = "\n\n"
    return render_template(
        "python/module.py.j2",
        source=options.source,
        future_annotations=future_annotations,
        imports=rendered_imports,
        import_spacing=import_spacing,
        body=body.rstrip(),
    )


def _exports(names: Iterable[str]) -> str:
    values = "\n".join(f'    "{name}",' for name in sorted(names))
    return f"__all__ = [\n{values}\n]"


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    return repr(value)


def _tuple_literal(values: tuple[Any, ...]) -> str:
    if not values:
        return "()"
    items = ", ".join(_literal(value) for value in values)
    suffix = "," if len(values) == 1 else ""
    return f"({items}{suffix})"


def _import_line(module: str, names: list[str]) -> str:
    inline = f"from {module} import {', '.join(names)}"
    if len(inline) <= 88:
        return inline
    values = "".join(f"    {name},\n" for name in names)
    return f"from {module} import (\n{values})"


def _import_group(module: str) -> int:
    root = module.split(".", 1)[0]
    if root in {"collections", "datetime", "enum", "typing", "uuid", "__future__"}:
        return 0
    if root in {"pydantic", "pyrpckit"}:
        return 1
    return 2


_RUNTIME_NAMES = frozenset(
    {
        "AsyncIterator",
        "RpcClientCore",
        "RpcRemoteError",
        "RpcTransport",
        "Self",
        "TypeAdapter",
        "UNSET",
        "UnsetType",
    }
)
