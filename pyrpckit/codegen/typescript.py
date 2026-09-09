import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pyrpckit.codegen._names import (
    ApiViewNode,
    api_view,
    assert_unique_names,
    camel_case,
    pascal_case,
)
from pyrpckit.codegen._templating import render_template
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
    Primitive,
    PrimitiveType,
    RouteDecl,
    ServerDecl,
    TypeExpr,
    UnionType,
    UnsupportedSchemaError,
    named_types,
)


@dataclass(frozen=True, slots=True)
class TypeScriptClientOptions:
    client_name: str | None = None
    transport_module: str = "../transport"
    api_root: str | None = None
    api_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"


def render_files(ir: ClientIr, options: TypeScriptClientOptions) -> dict[str, str]:
    """Render one generated TypeScript leaf package."""
    view = api_view(ir, api_root=options.api_root, api_names=options.api_names)
    client_name = _client_name(ir, options)
    _validate(ir, view.root_operations, view.nodes, client_name)
    renderer = _Renderer(ir, view.root_operations, view.nodes, options, client_name)
    files = {
        "core.ts": renderer.core(),
        "client.ts": renderer.client(),
        "errors.ts": renderer.errors(),
        "index.ts": renderer.index(),
        "metadata.ts": renderer.metadata(),
        "models.ts": renderer.models(),
    }
    if ir.servers:
        files["endpoints.ts"] = renderer.endpoints()
    if view.nodes:
        files["api/index.ts"] = renderer.module("")
        for node in _walk(view.nodes):
            files[_api_file(node)] = renderer.api(node)
    return files


class _Renderer:
    def __init__(
        self,
        ir: ClientIr,
        root_operations: tuple[RouteDecl, ...],
        nodes: tuple[ApiViewNode, ...],
        options: TypeScriptClientOptions,
        client_name: str,
    ) -> None:
        self.ir = ir
        self.root_operations = root_operations
        self.nodes = nodes
        self.options = options
        self.client_name = client_name

    def module(self, body: str) -> str:
        return render_template(
            "typescript/module.ts.j2",
            source=self.options.source,
            body=body,
        )

    def models(self) -> str:
        return self.module(
            "\n\n".join(self._declaration(item) for item in self.ir.declarations)
        )

    def metadata(self) -> str:
        route_lines: list[str] = []
        for route in self.ir.operations:
            route_lines.extend(
                [
                    f"  {_route_key(route)}: {{",
                    f"    method: {json.dumps(route.rpc_name)},",
                    f"    summary: {json.dumps(route.summary)},",
                    f"    tags: {_array(route.tags)},",
                    f"    deprecated: {str(route.deprecated).lower()},",
                    f"    errorCodes: {_array(error.code for error in route.errors)},",
                    f"    serverNames: {_array(route.server_names)},",
                    "  },",
                ]
            )
        protocol_version = (
            "null"
            if self.ir.protocol_version is None
            else str(self.ir.protocol_version)
        )
        body = (
            'import type { RpcContractInfo, RpcRouteInfo } from "./core";\n\n'
            "export const contract = {\n"
            f"  title: {json.dumps(self.ir.title)},\n"
            f"  version: {json.dumps(self.ir.version)},\n"
            f"  protocolVersion: {protocol_version},\n"
            "} as const satisfies RpcContractInfo;\n\n"
            "export const routes = {\n"
            f"{'\n'.join(route_lines)}\n"
            "} as const satisfies Record<string, RpcRouteInfo>;"
        )
        return self.module(body)

    def core(self) -> str:
        body = render_template(
            "typescript/core.ts.j2",
            transport_module=json.dumps(self.options.transport_module),
        ).rstrip()
        return self.module(body)

    def api(self, node: ApiViewNode) -> str:
        root = _root_prefix(node)
        imports = [f'import type {{ RpcClientCore }} from "{root}core";']
        if node.operations:
            imports.append(f'import {{ routes }} from "{root}metadata";')
            model_names = _route_model_names(node.operations)
            if model_names:
                imports.append(_type_import(model_names, f"{root}models"))
        for child in node.children:
            child_name = _identifier(child.segment)
            imports.append(
                f'import {{ {_api_class(child.path)} }} from "./{child_name}";'
            )
        lines = [f"export class {_api_class(node.path)} {{"]
        for child in node.children:
            lines.append(
                f"  readonly {_identifier(child.segment)}: {_api_class(child.path)};"
            )
        if node.children:
            lines.append("")
        if node.children:
            lines.extend(
                [
                    "  constructor(private readonly rpc: RpcClientCore) {",
                    *(
                        f"    this.{_identifier(child.segment)} = "
                        f"new {_api_class(child.path)}(rpc);"
                        for child in node.children
                    ),
                    "  }",
                ]
            )
        else:
            lines.append("  constructor(private readonly rpc: RpcClientCore) {}")
        for route in node.operations:
            lines.extend(["", *self._operation(route, root=False)])
        lines.append("}")
        return self.module("\n".join(imports) + "\n\n" + "\n".join(lines))

    def client(self) -> str:
        transport_module = json.dumps(self.options.transport_module)
        imports = [
            f"import type {{ RpcTransport }} from {transport_module};",
            'import { RpcClientCore } from "./core";',
        ]
        if self.root_operations:
            imports.append('import { routes } from "./metadata";')
            models = _route_model_names(self.root_operations)
            if models:
                imports.append(_type_import(models, "./models"))
        notification_type = _notification_type(self.ir)
        if notification_type:
            imports.append(_type_import(_model_names(notification_type), "./models"))
        for node in self.nodes:
            imports.append(
                f'import {{ {_api_class(node.path)} }} from "./{_api_module(node)}";'
            )
        lines = [f"export class {self.client_name} {{"]
        for node in self.nodes:
            lines.append(
                f"  readonly {_identifier(node.segment)}: {_api_class(node.path)};"
            )
        if self.nodes:
            lines.append("")
        lines.extend(
            [
                "  readonly #rpc: RpcClientCore;",
                "",
                "  constructor(transport: RpcTransport, "
                "options?: { closeTransport?: boolean }) {",
                "    this.#rpc = new RpcClientCore(transport, options);",
            ]
        )
        for node in self.nodes:
            lines.append(
                f"    this.{_identifier(node.segment)} = "
                f"new {_api_class(node.path)}(this.#rpc);"
            )
        lines.append("  }")
        for route in self.root_operations:
            lines.extend(["", *self._operation(route, root=True)])
        if notification_type is not None:
            lines.extend(
                [
                    "",
                    "  notifications(): "
                    f"AsyncIterable<{self._type(notification_type)}> {{",
                    "    return this.#rpc.notifications"
                    f"<{self._type(notification_type)}>();",
                    "  }",
                ]
            )
        lines.extend(
            [
                "",
                "  close(): Promise<void> {",
                "    return this.#rpc.close();",
                "  }",
                "}",
            ]
        )
        return self.module("\n".join(imports) + "\n\n" + "\n".join(lines))

    def endpoints(self) -> str:
        blocks = [
            'import { defineRpcServer, resolveRpcServer } from "./core";',
            "",
            "export const servers = {",
        ]
        for server in self.ir.servers:
            blocks.extend(
                [
                    f"  {_identifier(server.name)}: defineRpcServer({{",
                    f"    name: {json.dumps(server.name)},",
                    f"    url: {json.dumps(server.url)},",
                    f"    summary: {json.dumps(server.summary)},",
                    f"    description: {json.dumps(server.description)},",
                    f"    transport: {_ts_literal(server.transport)},",
                    "    variables: {",
                ]
            )
            for variable in server.variables:
                blocks.extend(
                    [
                        f"      {_property(variable.name)}: {{",
                        f"        default: {json.dumps(variable.default)},",
                        f"        description: {json.dumps(variable.description)},",
                        f"        enum: {_array(variable.enum)},",
                        "      },",
                    ]
                )
            blocks.extend(["    },", "  }),"])
        blocks.extend(["} as const;", ""])
        for server in self.ir.servers:
            blocks.extend(self._endpoint_helper(server))
            blocks.append("")
        return self.module("\n".join(blocks).rstrip())

    def _endpoint_helper(self, server: ServerDecl) -> list[str]:
        variables = server.variables
        name = f"{_identifier(server.name)}Url"
        if not variables:
            return [
                f"export function {name}(): string {{",
                f"  return resolveRpcServer(servers.{_identifier(server.name)});",
                "}",
            ]
        lines = [f"export function {name}(params: {{"]
        lines.extend(
            f"  {_property(variable.name)}?: string;" for variable in variables
        )
        lines.extend(
            [
                "} = {}): string {",
                "  return resolveRpcServer(",
                f"    servers.{_identifier(server.name)},",
                "    params,",
                "  );",
                "}",
            ]
        )
        return lines

    def errors(self) -> str:
        named = {
            error.name: error
            for route in self.ir.operations
            for error in route.errors
            if error.name is not None
        }
        if not named:
            return self.module("export {};")
        model_names = {
            name
            for error in named.values()
            if error.data is not None
            for name in _model_names(error.data)
        }
        imports = ['import { RpcRemoteError } from "./core";']
        if model_names:
            imports.append(_type_import(model_names, "./models"))
        blocks: list[str] = []
        for name, error in named.items():
            lines = [
                f"export class {_schema_name(name)}Error extends RpcRemoteError {{",
                f"  static readonly code = {error.code};",
            ]
            if error.data is not None:
                lines.extend(
                    [
                        "",
                        f"  declare readonly data: {self._type(error.data)};",
                    ]
                )
            lines.append("}")
            blocks.append("\n".join(lines))
        return self.module("\n".join(imports) + "\n\n" + "\n\n".join(blocks))

    def index(self) -> str:
        lines = [f'export {{ {self.client_name} }} from "./client";']
        if self.ir.servers:
            helpers = ", ".join(
                [
                    *(f"{_identifier(server.name)}Url" for server in self.ir.servers),
                    "servers",
                ]
            )
            lines.append(f'export {{ {helpers} }} from "./endpoints";')
        notification_type = _notification_type(self.ir)
        if notification_type is not None:
            lines.append(_type_export(_model_names(notification_type), "./models"))
        return self.module("\n".join(lines))

    def _operation(self, route: RouteDecl, *, root: bool) -> list[str]:
        params = ""
        if route.params:
            optional = (
                " = {}" if not any(param.required for param in route.params) else ""
            )
            params = f"params: {route.params_model}{optional}"
        wire_type = self._type(route.result)
        return_type = "void" if _is_null(route.result) else wire_type
        lines: list[str] = []
        if route.summary:
            lines.append(f"  /** {_comment(route.summary)} */")
        prefix = "async " if _is_null(route.result) else ""
        method_name = _identifier(route.operation_name)
        lines.append(f"  {prefix}{method_name}({params}): Promise<{return_type}> {{")
        call = (
            f"this.rpc.request<{wire_type}>"
            if not root
            else f"this.#rpc.request<{wire_type}>"
        )
        arguments = f"routes.{_route_key(route)}"
        if params:
            arguments += ", params"
        statement = f"{call}({arguments})"
        if _is_null(route.result):
            lines.append(f"    await {statement};")
        else:
            lines.append(f"    return {statement};")
        lines.append("  }")
        return lines

    def _declaration(self, declaration: Declaration) -> str:
        if isinstance(declaration, EnumDecl):
            return self._enum(declaration)
        if isinstance(declaration, ModelDecl):
            return self._model(declaration)
        if isinstance(declaration, AliasDecl):
            return (
                f"export type {_schema_name(declaration.name)} = "
                f"{self._type(declaration.target)};"
            )
        raise TypeError(f"Unsupported declaration: {type(declaration).__name__}")

    def _enum(self, declaration: EnumDecl) -> str:
        members = "\n".join(
            f"  {member.name}: {json.dumps(member.value)},"
            for member in declaration.members
        )
        return (
            f"export const {_schema_name(declaration.name)} = "
            f"{{\n{members}\n}} as const;\n\n"
            f"export type {_schema_name(declaration.name)} = "
            f"(typeof {_schema_name(declaration.name)})"
            f"[keyof typeof {_schema_name(declaration.name)}];"
        )

    def _model(self, declaration: ModelDecl) -> str:
        if not declaration.fields:
            return (
                f"export type {_schema_name(declaration.name)} = Record<string, never>;"
            )
        fields = "\n".join(self._field(field) for field in declaration.fields)
        return f"export type {_schema_name(declaration.name)} = {{\n{fields}\n}};"

    def _field(self, field: FieldDecl) -> str:
        optional = not field.required and not _is_discriminator(field.name, field.type)
        suffix = "?" if optional else ""
        return f"  {_property(field.name)}{suffix}: {self._type(field.type)};"

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
    nodes: tuple[ApiViewNode, ...],
    client_name: str,
) -> None:
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
        *((node.source_path[-1], _identifier(node.segment)) for node in nodes),
        *(
            (route.rpc_name, _identifier(route.operation_name))
            for route in root_operations
        ),
    ]
    if ir.notifications:
        client_members.append(("<client.notifications>", "notifications"))
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
    return f"{''.join(pascal_case(segment) for segment in path)}Api"


def _api_module(node: ApiViewNode) -> str:
    return "api/" + "/".join(_identifier(segment) for segment in node.path)


def _api_file(node: ApiViewNode) -> str:
    path = "/".join(_identifier(segment) for segment in node.path)
    return f"api/{path}/index.ts" if node.children else f"api/{path}.ts"


def _root_prefix(node: ApiViewNode) -> str:
    levels = len(node.path) + (1 if node.children else 0)
    return "../" * levels


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


def _walk(nodes: tuple[ApiViewNode, ...]) -> Iterable[ApiViewNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


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
