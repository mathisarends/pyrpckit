import json
import keyword
from collections.abc import Iterable
from typing import Any

from pyrpckit.codegen.ir import (
    ClientIr,
    EnumDecl,
    EnumLiteralType,
    FieldDecl,
    ListType,
    LiteralType,
    MapType,
    ModelDecl,
    NamedType,
    NotificationDecl,
    Primitive,
    PrimitiveType,
    RouteDecl,
    ServerDecl,
    TypeExpr,
    UnionType,
    UnsupportedSchemaError,
    named_types,
)
from pyrpckit.codegen.names import (
    NamespaceViewNode,
    assert_unique_names,
    client_view,
    pascal_case,
    snake_case,
)
from pyrpckit.codegen.options import PythonClientOptions
from pyrpckit.codegen.templating import render_template


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
                _import_line(
                    module,
                    sorted(
                        self._modules[module],
                        key=_import_name_key,
                    ),
                )
            )
        return "\n\n".join("\n".join(groups[rank]) for rank in sorted(groups))


def render_files(ir: ClientIr, options: PythonClientOptions) -> dict[str, str]:
    """Render one generated Python leaf package."""
    view = client_view(ir, api_root=options.api_root, api_names=options.api_names)
    client_name = _client_name(ir, options)
    _validate(ir, view.root_operations, view.nodes, options, client_name)
    files = {
        "__init__.py": _render_package_init(ir, options, client_name),
        **_render_runtime(options),
        "client.py": _render_client(
            ir,
            view.root_operations,
            view.nodes,
            view.root_notifications,
            options,
            client_name,
        ),
    }
    if ir.declarations:
        files["models.py"] = _render_models(ir, options)
    if ir.operations or ir.notifications:
        files["routes.py"] = _render_routes(ir, options)
    if _named_errors(ir):
        files["errors.py"] = _render_errors(ir, options)
    if ir.servers:
        files["endpoints.py"] = _render_endpoints(ir, options)
    if options.with_transport == "websocket":
        files["transport.py"] = _render_websocket_transport(options)
    if view.nodes:
        for node in view.nodes:
            files[_api_file(node)] = _render_api(node, ir, options)
    return files


def _render_runtime(options: PythonClientOptions) -> dict[str, str]:
    files = {
        "internal/__init__.py": _render_runtime_init(options),
        "internal/core.py": _render_runtime_module(options, "core"),
        "internal/errors.py": _render_runtime_module(options, "errors"),
        "internal/metadata.py": _render_runtime_module(options, "metadata"),
        "internal/transport.py": _render_runtime_module(options, "transport"),
        "internal/unset.py": _render_runtime_module(options, "unset"),
    }
    if options.with_transport == "websocket":
        files["internal/connection.py"] = _render_runtime_module(
            options,
            "connection",
        )
    return files


def _render_runtime_init(options: PythonClientOptions) -> str:
    imports = _Imports()
    exported = ["UNSET", "RpcClientCore", "RpcClientHook", "UnsetType"]
    if options.with_transport == "websocket":
        imports.add(".connection", "ClientConnection")
        exported.append("ClientConnection")
    imports.add(".core", "RpcClientCore", "RpcClientHook")
    imports.add(
        ".errors",
        "RpcClientError",
        "RpcNotificationValidationError",
        "RpcRemoteError",
        "RpcResponseValidationError",
        "RpcTransportError",
    )
    imports.add(
        ".metadata",
        "JsonValue",
        "RpcContractInfo",
        "RpcNotificationInfo",
        "RpcRouteInfo",
        "RpcServerInfo",
        "RpcServerVariable",
        "RpcTransportDescriptor",
    )
    imports.add(".transport", "RpcTransport")
    imports.add(".unset", "UNSET", "UnsetType")
    exported.extend(
        [
            "JsonValue",
            "RpcClientError",
            "RpcContractInfo",
            "RpcNotificationInfo",
            "RpcNotificationValidationError",
            "RpcRemoteError",
            "RpcResponseValidationError",
            "RpcRouteInfo",
            "RpcServerInfo",
            "RpcServerVariable",
            "RpcTransport",
            "RpcTransportDescriptor",
            "RpcTransportError",
        ]
    )
    body = render_template(
        "python/package_init.py.j2",
        filters={"literal": _literal},
        exports=sorted(exported),
    ).rstrip()
    return _module(options, imports, body)


def _render_runtime_module(options: PythonClientOptions, name: str) -> str:
    body = render_template(f"python/runtime/{name}.py.j2").rstrip()
    return _module(options, _Imports(), body)


def _runtime_module(options: PythonClientOptions) -> str:
    return f"{options.package}.internal"


def _render_models(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    if ir.models:
        imports.add("pydantic", "BaseModel", "ConfigDict")
    for declaration in ir.declarations:
        if isinstance(declaration, EnumDecl):
            imports.add("enum", "IntEnum" if declaration.integral else "StrEnum")
        elif isinstance(declaration, ModelDecl) and declaration.closed:
            imports.add("pydantic", "ConfigDict")
    body = render_template(
        "python/models.py.j2",
        filters=_template_filters(imports, options),
        declarations=ir.declarations,
        models=ir.models,
        base_model_name=options.base_model_name,
    )
    body = _collapse_blank_lines(body)
    return _module(options, imports, body, future_annotations=True)


def _render_routes(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add("pydantic", "TypeAdapter")
    imports.add(_runtime_module(options), "RpcNotificationInfo", "RpcRouteInfo")
    for route in ir.operations:
        imports.add(
            f"{options.package}.models",
            *_model_names(route.result),
        )
    for event in ir.notifications:
        imports.add(
            f"{options.package}.models",
            *_model_names(event.message),
            *_model_names(event.payload),
        )
    body = render_template(
        "python/routes.py.j2",
        filters=_template_filters(imports, options),
        routes=ir.operations,
        notifications=ir.notifications,
    )
    return _module(options, imports, body)


def _render_endpoints(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    imports.add("collections.abc", "Iterable")
    imports.add("dataclasses", "dataclass")
    imports.add("enum", "StrEnum")
    imports.add(
        _runtime_module(options),
        "RpcServerInfo",
        "RpcTransportDescriptor",
    )
    if any(server.variables for server in ir.servers):
        imports.add(_runtime_module(options), "RpcServerVariable")
    if any(variable.enum for server in ir.servers for variable in server.variables):
        imports.add("typing", "Literal")

    body = render_template(
        "python/endpoints.py.j2",
        filters=_template_filters(imports, options),
        servers=ir.servers,
    )
    body = _collapse_blank_lines(body)
    return _module(options, imports, body)


def _server_subprotocols(server: ServerDecl) -> str:
    if server.transport is None:
        return "()"
    return _tuple_literal(server.transport.subprotocols)


def _render_errors(ir: ClientIr, options: PythonClientOptions) -> str:
    imports = _Imports()
    errors = []
    seen: set[str] = set()
    for route in ir.operations:
        for error in route.errors:
            if error.name is None or error.name in seen:
                continue
            seen.add(error.name)
            imports.add("typing", "ClassVar")
            imports.add(_runtime_module(options), "RpcRemoteError")
            if error.data is not None:
                imports.add(f"{options.package}.models", *_model_names(error.data))
            errors.append(error)
    body = render_template(
        "python/errors.py.j2",
        filters=_template_filters(imports, options),
        errors=errors,
    )
    return _module(options, imports, body)


def _add_notification_imports(
    event: NotificationDecl, imports: _Imports, options: PythonClientOptions
) -> None:
    imports.add("collections.abc", "AsyncIterator")
    imports.add(f"{options.package}.models", *_model_names(event.payload))
    imports.add(f"{options.package}.routes", _constant(event.rpc_name))
    _annotation(event.payload, imports)


def _render_api(
    root: NamespaceViewNode,
    ir: ClientIr,
    options: PythonClientOptions,
) -> str:
    imports = _Imports()
    imports.add(_runtime_module(options), "RpcClientCore")
    nodes = tuple(_walk_postorder((root,)))
    for node in nodes:
        for route in node.operations:
            _add_operation_imports(route, imports, options)
        for event in node.notifications:
            _add_notification_imports(event, imports, options)
    body = render_template(
        "python/api.py.j2",
        filters=_template_filters(imports, options),
        nodes=nodes,
    )
    body = _collapse_blank_lines(body)
    return _module(options, imports, body)


def _render_client(
    ir: ClientIr,
    root_operations: tuple[RouteDecl, ...],
    nodes: tuple[NamespaceViewNode, ...],
    root_events: tuple[NotificationDecl, ...],
    options: PythonClientOptions,
    client_name: str,
) -> str:
    imports = _Imports()
    imports.add("typing", "Self")
    imports.add(_runtime_module(options), "RpcClientCore", "RpcTransport")
    if ir.servers:
        imports.add("collections.abc", "Mapping")
        imports.add(f"{options.package}.endpoints", "ServerName")
    if options.with_transport == "websocket":
        imports.add(_runtime_module(options), "ClientConnection")
        imports.add(
            f"{options.package}.endpoints",
            "Endpoint",
            "resolve_endpoints",
        )
        imports.add(f"{options.package}.transport", "WebSocketTransport")
    for node in nodes:
        api_class = _api_class(node.path)
        imports.add(f"{options.package}.{_api_module(node)}", api_class)
    for route in root_operations:
        _add_operation_imports(route, imports, options)
    for event in root_events:
        _add_notification_imports(event, imports, options)
    body = render_template(
        "python/client.py.j2",
        filters=_template_filters(imports, options),
        client_name=client_name,
        servers=ir.servers,
        nodes=nodes,
        operations=root_operations,
        notifications=root_events,
        with_websocket=options.with_transport == "websocket",
    )
    return _module(options, imports, body)


def _render_package_init(
    ir: ClientIr,
    options: PythonClientOptions,
    client_name: str,
) -> str:
    imports = _Imports()
    imports.add(
        _runtime_module(options),
        "RpcClientError",
        "RpcNotificationValidationError",
        "RpcRemoteError",
        "RpcResponseValidationError",
        "RpcTransportError",
    )
    imports.add(f"{options.package}.client", client_name)
    exported = [
        client_name,
        "RpcClientError",
        "RpcNotificationValidationError",
        "RpcRemoteError",
        "RpcResponseValidationError",
        "RpcTransportError",
    ]
    if ir.servers:
        imports.add(options.package, "endpoints")
        imports.add(f"{options.package}.endpoints", "Endpoint", "ServerName")
        exported.extend(["Endpoint", "ServerName", "endpoints"])
    if options.with_transport == "websocket":
        imports.add(f"{options.package}.transport", "WebSocketTransport")
        exported.append("WebSocketTransport")
    body = render_template(
        "python/package_init.py.j2",
        filters={"literal": _literal},
        exports=sorted(exported),
    ).rstrip()
    return _module(options, imports, body)


def _add_operation_imports(
    route: RouteDecl, imports: _Imports, options: PythonClientOptions
) -> None:
    models = f"{options.package}.models"
    imports.add(f"{options.package}.routes", route.method_member)
    if route.params_model is not None:
        imports.add(models, _schema_name(route.params_model))
    for parameter in route.params:
        imports.add(models, *_model_names(parameter.type))
    imports.add(models, *_model_names(route.result))
    _annotation(route.result, imports)
    for parameter in route.params:
        _parameter_annotation(parameter, imports, options)


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


def _parameter_annotation(
    parameter: Any,
    imports: _Imports,
    options: PythonClientOptions,
) -> str:
    annotation = _annotation(parameter.type, imports)
    if parameter.required:
        return annotation
    if parameter.has_default:
        return f"{annotation} = {_literal(parameter.default)}"
    imports.add(_runtime_module(options), "UNSET", "UnsetType")
    return f"{_union((annotation, 'UnsetType'))} = UNSET"


def _template_filters(
    imports: _Imports,
    options: PythonClientOptions,
) -> dict[str, Any]:
    return {
        "annotation": lambda expression: _annotation(expression, imports),
        "api_class": _api_class,
        "constant": _constant,
        "direct_params": lambda parameters: all(
            parameter.required or parameter.has_default for parameter in parameters
        ),
        "docstring": _docstring,
        "enum_decl": lambda declaration: isinstance(declaration, EnumDecl),
        "field_line": lambda field: _field_line(field, imports),
        "identifier": _identifier,
        "literal": _literal,
        "model_decl": lambda declaration: isinstance(declaration, ModelDecl),
        "null_type": _is_null,
        "options_literal": lambda values: _literal(dict(values)),
        "parameter_annotation": lambda parameter: _parameter_annotation(
            parameter, imports, options
        ),
        "schema_name": _schema_name,
        "server_subprotocols": _server_subprotocols,
        "tuple_literal": _tuple_literal,
        "variable_annotation": _variable_annotation,
    }


def _variable_annotation(variable: Any) -> str:
    if not variable.enum:
        return "str"
    return "Literal[" + ", ".join(_literal(value) for value in variable.enum) + "]"


def _validate(
    ir: ClientIr,
    root_operations: tuple[RouteDecl, ...],
    nodes: tuple[NamespaceViewNode, ...],
    options: PythonClientOptions,
    client_name: str,
) -> None:
    if options.with_transport not in {None, "websocket"}:
        raise UnsupportedSchemaError("with_transport must be None or 'websocket'")
    if options.with_transport == "websocket":
        if not ir.servers:
            raise UnsupportedSchemaError(
                "with_transport='websocket' requires at least one OpenRPC server"
            )
        invalid = [
            server.name
            for server in ir.servers
            if server.transport is None or server.transport.type != "websocket"
        ]
        if invalid:
            raise UnsupportedSchemaError(
                "WebSocket generation requires websocket transport descriptors for: "
                + ", ".join(invalid)
            )
        if len(ir.servers) > 1:
            unassigned = [
                item.rpc_name
                for item in (*ir.operations, *ir.notifications)
                if item.server is None
            ]
            if unassigned:
                raise UnsupportedSchemaError(
                    "Routes need a server when generating multiple WebSocket "
                    "connections: " + ", ".join(unassigned)
                )
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
        ("<client.from_transport>", "from_transport"),
        *((node.source_path[-1], _identifier(node.segment)) for node in nodes),
        *(
            (route.rpc_name, _identifier(route.operation_name))
            for route in root_operations
        ),
        *(
            (event.rpc_name, _identifier(event.operation_name))
            for event in client_view(
                ir,
                api_root=options.api_root,
                api_names=options.api_names,
            ).root_notifications
        ),
    ]
    if ir.servers:
        client_members.extend(
            [
                ("<client.from_transports>", "from_transports"),
                ("<client.from_transport_map>", "from_transport_map"),
            ]
        )
    if options.with_transport == "websocket":
        client_members.append(("<client.connect>", "connect"))
    assert_unique_names("root client", client_members)
    _validate_nodes(nodes)
    for node in nodes:
        assert_unique_names(
            f"Python namespace module {_identifier(node.segment)!r}",
            (
                (".".join(item.source_path), _api_class(item.path))
                for item in _walk((node,))
            ),
        )
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


def _validate_nodes(nodes: tuple[NamespaceViewNode, ...]) -> None:
    for node in nodes:
        values = [
            (child.segment, _identifier(child.segment)) for child in node.children
        ]
        values.extend(
            (route.rpc_name, _identifier(route.operation_name))
            for route in node.operations
        )
        values.extend(
            (event.rpc_name, _identifier(event.operation_name))
            for event in node.notifications
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
    return "".join(pascal_case(segment) for segment in path)


def _api_module(node: NamespaceViewNode) -> str:
    return f"namespaces.{_identifier(node.path[0])}"


def _api_file(node: NamespaceViewNode) -> str:
    return f"namespaces/{_identifier(node.path[0])}.py"


def _walk(nodes: tuple[NamespaceViewNode, ...]) -> Iterable[NamespaceViewNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _walk_postorder(
    nodes: tuple[NamespaceViewNode, ...],
) -> Iterable[NamespaceViewNode]:
    for node in nodes:
        yield from _walk_postorder(node.children)
        yield node


def _named_errors(ir: ClientIr) -> bool:
    return any(
        error.name is not None for route in ir.operations for error in route.errors
    )


def _render_websocket_transport(options: PythonClientOptions) -> str:
    body = render_template(
        "python/transport.py.j2",
        runtime_module=_runtime_module(options),
    ).rstrip()
    return _module(options, _Imports(), body)


def _collapse(expression: UnionType) -> TypeExpr:
    return expression.members[0] if len(expression.members) == 1 else expression


def _allows_none(expression: TypeExpr) -> bool:
    if isinstance(expression, PrimitiveType):
        return expression.primitive is Primitive.NULL
    if isinstance(expression, UnionType):
        return any(_allows_none(member) for member in expression.members)
    return False


def _is_null(expression: TypeExpr) -> bool:
    return (
        isinstance(expression, PrimitiveType) and expression.primitive is Primitive.NULL
    )


def _union(items: Iterable[str]) -> str:
    return " | ".join(dict.fromkeys(items))


def _docstring(value: str) -> str:
    return " ".join(value.split()).replace('"""', '"""')


def _collapse_blank_lines(body: str) -> str:
    while "\n\n\n\n" in body:
        body = body.replace("\n\n\n\n", "\n\n\n")
    return body


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
    rendered = render_template(
        "python/module.py.j2",
        source=options.source,
        future_annotations=future_annotations,
        imports=rendered_imports,
        import_spacing=import_spacing,
        body=body.rstrip(),
    )
    return f"{rendered.rstrip()}\n"


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


def _import_name_key(name: str) -> tuple[int, str]:
    if name.isupper():
        group = 0
    elif name[0].isupper():
        group = 1
    else:
        group = 2
    return group, name.casefold()


def _import_group(module: str) -> int:
    root = module.split(".", 1)[0]
    if root in {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "uuid",
    }:
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
