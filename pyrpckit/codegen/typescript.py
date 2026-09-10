import json
import re
from collections.abc import Iterable

from pyrpckit.codegen.ir import (
    ClientIr,
    EnumDecl,
    EnumLiteralType,
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
    ServerVariableDecl,
    TypeExpr,
    UnionType,
    UnsupportedSchemaError,
    named_types,
)
from pyrpckit.codegen.names import (
    NamespaceViewNode,
    assert_unique_names,
    camel_case,
    client_view,
    pascal_case,
)
from pyrpckit.codegen.options import TypeScriptClientOptions
from pyrpckit.codegen.templating import render_template


def render_files(ir: ClientIr, options: TypeScriptClientOptions) -> dict[str, str]:
    """Render one generated TypeScript leaf package."""
    view = client_view(ir, api_root=options.api_root, api_names=options.api_names)
    client_name = _client_name(ir, options)
    _validate(ir, view.root_operations, view.nodes, options, client_name)
    renderer = _Renderer(
        ir,
        view.root_operations,
        view.nodes,
        view.root_notifications,
        options,
        client_name,
    )
    files = {
        "core.ts": renderer.core(),
        "client.ts": renderer.client(),
        "index.ts": renderer.index(),
    }
    if ir.declarations:
        files["models.ts"] = renderer.models()
    if ir.operations:
        files["routes.ts"] = renderer.routes()
    if _named_errors(ir):
        files["errors.ts"] = renderer.errors()
    if ir.servers:
        files["endpoints.ts"] = renderer.endpoints()
    if options.with_transport == "websocket":
        files["transport.ts"] = renderer.transport()
    if view.nodes:
        files["namespaces/index.ts"] = renderer.namespaces_index()
        for node in view.nodes:
            files[_api_file(node)] = renderer.api(node)
    return files


class _Renderer:
    def __init__(
        self,
        ir: ClientIr,
        root_operations: tuple[RouteDecl, ...],
        nodes: tuple[NamespaceViewNode, ...],
        root_events: tuple[NotificationDecl, ...],
        options: TypeScriptClientOptions,
        client_name: str,
    ) -> None:
        self.ir = ir
        self.root_operations = root_operations
        self.nodes = nodes
        self.root_events = root_events
        self.options = options
        self.client_name = client_name

    def module(self, body: str) -> str:
        return render_template(
            "typescript/module.ts.j2",
            source=self.options.source,
            body=body,
        )

    def template(self, name: str, **context: object) -> str:
        return render_template(
            f"typescript/{name}.ts.j2",
            filters=self._filters(),
            **context,
        ).rstrip()

    def _filters(self) -> dict[str, object]:
        return {
            "all_optional": lambda parameters: (
                not any(parameter.required for parameter in parameters)
            ),
            "api_class": _api_class,
            "array": _array,
            "comment": _comment,
            "compact_notification": self._compact_notification,
            "enum_decl": lambda declaration: isinstance(declaration, EnumDecl),
            "identifier": _identifier,
            "literal": _ts_literal,
            "model_decl": lambda declaration: isinstance(declaration, ModelDecl),
            "null_type": _is_null,
            "optional_field": lambda field: (
                not field.required and not _is_discriminator(field.name, field.type)
            ),
            "property": _property,
            "route_access": lambda route: f"routes.{_route_key(route)}",
            "route_key": _route_key,
            "schema_name": _schema_name,
            "server_variable_type": _server_variable_type,
            "subprotocols": _server_subprotocols,
            "type": self._type,
        }

    def _compact_notification(
        self,
        event: NotificationDecl,
        root: bool,
    ) -> bool:
        receiver = "this.#rpc" if root else "this.rpc"
        arguments = _ts_literal(event.rpc_name)
        if event.server is not None:
            arguments += f", {_ts_literal(event.server)}"
        call = (
            f"    return {receiver}.notifications<{self._type(event.payload)}>"
            f"({arguments});"
        )
        return len(call) <= 80

    def models(self) -> str:
        return self.module(self.template("models", declarations=self.ir.declarations))

    def namespaces_index(self) -> str:
        return self.module(self.template("namespace_index", nodes=self.nodes))

    def routes(self) -> str:
        return self.module(self.template("routes", routes=self.ir.operations))

    def core(self) -> str:
        body = render_template(
            "typescript/core.ts.j2",
            transport_module=json.dumps(self._transport_module()),
        ).rstrip()
        return self.module(body)

    def transport(self) -> str:
        return self.module(render_template("typescript/transport.ts.j2").rstrip())

    def api(self, root_node: NamespaceViewNode) -> str:
        root = "../"
        nodes = tuple(_walk((root_node,)))
        imports = [f'import type {{ RpcClientCore }} from "{root}core";']
        if any(node.operations for node in nodes):
            imports.append(f'import {{ routes }} from "{root}routes";')
        model_names: set[str] = set()
        for node in nodes:
            model_names.update(_route_model_names(node.operations))
            model_names.update(
                name
                for event in node.notifications
                for name in _model_names(event.payload)
            )
        if model_names:
            imports.append(_type_import(model_names, f"{root}models"))
        body = self.template(
            "api",
            imports=imports,
            nodes=tuple(_walk_postorder((root_node,))),
        )
        return self.module(body)

    def client(self) -> str:
        imports = [
            'import { RpcClientCore, type RpcTransport } from "./core";',
        ]
        if self.root_operations:
            imports.append('import { routes } from "./routes";')
        models = _route_model_names(self.root_operations)
        models.update(
            name for event in self.root_events for name in _model_names(event.payload)
        )
        if models:
            imports.append(_type_import(models, "./models"))
        transports_name = _transports_name(self.client_name)
        if self.ir.servers:
            endpoint_imports = "type Endpoint, type ServerName"
            if self.options.with_transport == "websocket":
                endpoint_imports = f"resolveEndpoints, {endpoint_imports}"
            imports.append(f'import {{ {endpoint_imports} }} from "./endpoints";')
        if self.options.with_transport == "websocket":
            imports.extend(
                [
                    "import { WebSocketTransport, type WebSocketFactory } "
                    'from "./transport";',
                ]
            )
        if self.nodes:
            namespace_classes = ", ".join(_api_class(node.path) for node in self.nodes)
            imports.append(f'import {{ {namespace_classes} }} from "./namespaces";')
        transport_type = (
            f"RpcTransport | {transports_name}" if self.ir.servers else "RpcTransport"
        )
        body = self.template(
            "client",
            imports=imports,
            client_name=self.client_name,
            transports_name=transports_name,
            transport_type=transport_type,
            servers=self.ir.servers,
            nodes=self.nodes,
            operations=self.root_operations,
            notifications=self.root_events,
            with_websocket=self.options.with_transport == "websocket",
        )
        return self.module(body)

    def _transport_module(self) -> str:
        if self.options.with_transport == "websocket":
            return "./transport"
        return self.options.transport_module

    def endpoints(self) -> str:
        return self.module(self.template("endpoints", servers=self.ir.servers))

    def errors(self) -> str:
        named = {
            error.name: error
            for route in self.ir.operations
            for error in route.errors
            if error.name is not None
        }
        model_names = {
            name
            for error in named.values()
            if error.data is not None
            for name in _model_names(error.data)
        }
        model_import = _type_import(model_names, "./models") if model_names else ""
        return self.module(
            self.template(
                "errors",
                errors=tuple(named.values()),
                model_import=model_import,
            )
        )

    def index(self) -> str:
        body = self.template(
            "index",
            client_name=self.client_name,
            transports_name=_transports_name(self.client_name),
            servers=self.ir.servers,
            with_websocket=self.options.with_transport == "websocket",
            named_errors=_named_errors(self.ir),
        )
        return self.module(body)

    def _type(self, expression: TypeExpr) -> str:
        if isinstance(expression, PrimitiveType):
            return _PRIMITIVES[expression.primitive]
        if isinstance(expression, NamedType):
            return _schema_name(expression.name)
        if isinstance(expression, LiteralType):
            return json.dumps(expression.value)
        if isinstance(expression, EnumLiteralType):
            return f"typeof {_schema_name(expression.enum)}.{expression.member}"
        if isinstance(expression, ListType):
            item = self._type(expression.item)
            if isinstance(expression.item, UnionType):
                item = f"({item})"
            return f"{item}[]"
        if isinstance(expression, MapType):
            return f"Record<string, {self._type(expression.value)}>"
        if isinstance(expression, UnionType):
            return " | ".join(
                dict.fromkeys(self._type(member) for member in expression.members)
            )
        raise TypeError(f"Unsupported type: {type(expression).__name__}")


def _validate(
    ir: ClientIr,
    root_operations: tuple[RouteDecl, ...],
    nodes: tuple[NamespaceViewNode, ...],
    options: TypeScriptClientOptions,
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
    if not _VALID_IDENTIFIER.fullmatch(client_name):
        raise UnsupportedSchemaError(
            f"client_name must be a valid TypeScript identifier: {client_name!r}"
        )
    assert_unique_names(
        "schemas",
        (
            (declaration.name, _schema_name(declaration.name))
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
    client_members = [
        ("<client.close>", "close"),
        ("<client.fromTransport>", "fromTransport"),
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
        client_members.append(("<client.fromTransports>", "fromTransports"))
    if options.with_transport == "websocket":
        client_members.append(("<client.connect>", "connect"))
    assert_unique_names("root client", client_members)
    _validate_nodes(nodes)
    for node in nodes:
        assert_unique_names(
            f"TypeScript namespace module {_identifier(node.segment)!r}",
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


def _client_name(ir: ClientIr, options: TypeScriptClientOptions) -> str:
    return options.client_name or f"{pascal_case(ir.title)}Client"


def _identifier(value: str) -> str:
    identifier = camel_case(value)
    if not identifier:
        raise UnsupportedSchemaError(
            f"Cannot derive a TypeScript identifier from {value!r}"
        )
    if identifier[0].isdigit():
        identifier = f"_{identifier}"
    if identifier in _RESERVED_WORDS:
        identifier = f"{identifier}_"
    return identifier


def _schema_name(value: str) -> str:
    name = pascal_case(value)
    if not name:
        raise UnsupportedSchemaError(
            f"Cannot derive a TypeScript class name from {value!r}"
        )
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _model_names(expression: TypeExpr) -> set[str]:
    return {_schema_name(name) for name in named_types(expression)}


def _property(value: str) -> str:
    return value if _VALID_IDENTIFIER.fullmatch(value) else json.dumps(value)


def _route_key(route: RouteDecl) -> str:
    return _identifier(route.rpc_name)


def _api_class(path: tuple[str, ...]) -> str:
    return "".join(pascal_case(segment) for segment in path)


def _api_file(node: NamespaceViewNode) -> str:
    return f"namespaces/{_identifier(node.path[0])}.ts"


def _route_model_names(routes: Iterable[RouteDecl]) -> set[str]:
    names: set[str] = set()
    for route in routes:
        if route.params and route.params_model is not None:
            names.add(_schema_name(route.params_model))
        names.update(_model_names(route.result))
    return names


def _notification_type(ir: ClientIr) -> TypeExpr | None:
    if not ir.notifications:
        return None
    members = tuple(item.payload for item in ir.notifications)
    return members[0] if len(members) == 1 else UnionType(members)


def _type_import(names: Iterable[str], module: str) -> str:
    values = sorted(set(names))
    inline = f'import type {{ {", ".join(values)} }} from "{module}";'
    if len(inline) <= 80:
        return inline
    body = "\n".join(f"  {name}," for name in values)
    return f'import type {{\n{body}\n}} from "{module}";'


def _type_export(names: Iterable[str], module: str) -> str:
    return f'export type {{ {", ".join(sorted(set(names)))} }} from "{module}";'


def _array(values: Iterable[object]) -> str:
    return "[" + ", ".join(_ts_literal(value) for value in values) + "]"


def _server_variable_type(variable: ServerVariableDecl) -> str:
    if variable.enum:
        return " | ".join(_ts_literal(value) for value in variable.enum)
    return "string"


def _server_subprotocols(server: ServerDecl) -> str:
    if server.transport is None:
        return "[]"
    return _array(server.transport.subprotocols)


def _ts_literal(value: object) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return str(value).lower()
    return json.dumps(value)


def _comment(value: str) -> str:
    return " ".join(value.split()).replace("*/", "* /")


def _is_null(expression: TypeExpr) -> bool:
    return (
        isinstance(expression, PrimitiveType) and expression.primitive is Primitive.NULL
    )


def _is_discriminator(name: str, expression: TypeExpr) -> bool:
    return name == "type" and isinstance(expression, LiteralType | EnumLiteralType)


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


def _transports_name(client_name: str) -> str:
    stem = client_name.removesuffix("Client") or client_name
    return f"{stem}Transports"


def _named_errors(ir: ClientIr) -> bool:
    return any(
        error.name is not None for route in ir.operations for error in route.errors
    )


_PRIMITIVES = {
    Primitive.STRING: "string",
    Primitive.INTEGER: "number",
    Primitive.NUMBER: "number",
    Primitive.BOOLEAN: "boolean",
    Primitive.NULL: "null",
    Primitive.UUID: "string",
    Primitive.DATETIME: "string",
    Primitive.ANY: "unknown",
}

_VALID_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

_RESERVED_WORDS = frozenset(
    {
        "break",
        "case",
        "class",
        "const",
        "constructor",
        "continue",
        "debugger",
        "default",
        "delete",
        "do",
        "else",
        "enum",
        "export",
        "extends",
        "false",
        "finally",
        "for",
        "function",
        "if",
        "import",
        "in",
        "instanceof",
        "new",
        "null",
        "return",
        "super",
        "switch",
        "this",
        "throw",
        "true",
        "try",
        "typeof",
        "var",
        "void",
        "while",
        "with",
        "yield",
    }
)

_RUNTIME_NAMES = frozenset(
    {"RpcClientCore", "RpcRemoteError", "RpcRouteInfo", "RpcTransport"}
)
