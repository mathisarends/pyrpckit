import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pyrpckit.codegen._names import (
    NamespaceViewNode,
    assert_unique_names,
    camel_case,
    client_view,
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


@dataclass(frozen=True, slots=True)
class TypeScriptClientOptions:
    client_name: str | None = None
    transport_module: str = "../transport"
    api_root: str | None = None
    api_names: Mapping[str, str] = field(default_factory=dict)
    source: str = "the OpenRPC document"
    with_transport: str | None = None


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
        for node in _walk(view.nodes):
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

    def models(self) -> str:
        return self.module(
            "\n\n".join(self._declaration(item) for item in self.ir.declarations)
        )

    def namespaces_index(self) -> str:
        return self.module(
            "\n".join(
                f'export {{ {_api_class(node.path)} }} from "./'
                f'{_identifier(node.segment)}";'
                for node in self.nodes
            )
        )

    def routes(self) -> str:
        route_lines: list[str] = []
        for route in self.ir.operations:
            route_lines.extend(
                [
                    f"  {_route_key(route)}: {{",
                    f"    method: {json.dumps(route.rpc_name)},",
                    *(
                        [f"    server: {json.dumps(route.server)},"]
                        if route.server is not None
                        else []
                    ),
                    "  },",
                ]
            )
        body = (
            'import type { RpcRouteInfo } from "./core";\n\n'
            "export const routes = {\n"
            f"{'\n'.join(route_lines)}\n"
            "} as const satisfies Record<string, RpcRouteInfo>;"
        )
        return self.module(body)

    def core(self) -> str:
        body = render_template(
            "typescript/core.ts.j2",
            transport_module=json.dumps(self._transport_module()),
        ).rstrip()
        return self.module(body)

    def transport(self) -> str:
        return self.module(render_template("typescript/transport.ts.j2").rstrip())

    def api(self, node: NamespaceViewNode) -> str:
        root = _root_prefix(node)
        imports = [f'import type {{ RpcClientCore }} from "{root}core";']
        if node.operations:
            imports.append(f'import {{ routes }} from "{root}routes";')
        model_names = _route_model_names(node.operations)
        model_names.update(
            name for event in node.notifications for name in _model_names(event.payload)
        )
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
        for event in node.notifications:
            lines.extend(["", *self._notification(event, root=False)])
        lines.append("}")
        return self.module("\n".join(imports) + "\n\n" + "\n".join(lines))

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
        prelude: list[str] = []
        if self.ir.servers:
            prelude.extend(
                [
                    f"export type {transports_name} = {{",
                    *(
                        f"  readonly {_property(server.name)}: RpcTransport;"
                        for server in self.ir.servers
                    ),
                    "};",
                ]
            )
        if self.options.with_transport == "websocket":
            prelude.extend(
                [
                    "",
                    "export type ConnectOptions = {",
                    "  readonly endpoints?: readonly Endpoint[];",
                    "  readonly requestTimeoutMs?: number;",
                    "  readonly notificationQueueSize?: number;",
                    "  readonly socketFactory?: WebSocketFactory;",
                    "};",
                ]
            )
        lines = [*prelude, "" if prelude else "", f"export class {self.client_name} {{"]
        for node in self.nodes:
            lines.append(
                f"  readonly {_identifier(node.segment)}: {_api_class(node.path)};"
            )
        if self.nodes:
            lines.append("")
        transport_type = (
            f"RpcTransport | {transports_name}" if self.ir.servers else "RpcTransport"
        )
        lines.extend(
            [
                "  readonly #rpc: RpcClientCore;",
                "",
                "  constructor(",
                f"    transport: {transport_type},",
                "    options?: { readonly closeTransport?: boolean },",
                "  ) {",
                "    this.#rpc = new RpcClientCore(transport, options);",
            ]
        )
        for node in self.nodes:
            lines.append(
                f"    this.{_identifier(node.segment)} = "
                f"new {_api_class(node.path)}(this.#rpc);"
            )
        lines.append("  }")
        lines.extend(
            [
                "",
                f"  static fromTransport(transport: RpcTransport): "
                f"{self.client_name} {{",
                f"    return new {self.client_name}(transport);",
                "  }",
            ]
        )
        if self.ir.servers:
            lines.extend(
                [
                    "",
                    "  static fromTransports(",
                    f"    transports: {transports_name},",
                    f"  ): {self.client_name} {{",
                    f"    return new {self.client_name}(transports);",
                    "  }",
                ]
            )
        if self.options.with_transport == "websocket":
            lines.extend(["", *self._connect_method(transports_name)])
        for route in self.root_operations:
            lines.extend(["", *self._operation(route, root=True)])
        for event in self.root_events:
            lines.extend(["", *self._notification(event, root=True)])
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

    def _connect_method(self, transports_name: str) -> list[str]:
        return [
            "  static async connect(options: ConnectOptions = {}): "
            f"Promise<{self.client_name}> {{",
            "    const transports: Partial<Record<ServerName, RpcTransport>> = {};",
            "    try {",
            "      for (const endpoint of resolveEndpoints(",
            "        options.endpoints ?? [],",
            "      )) {",
            "        transports[endpoint.server] = await WebSocketTransport.open(",
            "          endpoint.url,",
            "          {",
            "            subprotocols: endpoint.subprotocols,",
            "            requestTimeoutMs: options.requestTimeoutMs,",
            "            notificationQueueSize: options.notificationQueueSize,",
            "            socketFactory: options.socketFactory,",
            "          },",
            "        );",
            "      }",
            "    } catch (error) {",
            "      await Promise.all(",
            "        Object.values(transports).map((transport) => transport.close()),",
            "      );",
            "      throw error;",
            "    }",
            f"    return {self.client_name}.fromTransports(transports as "
            f"{transports_name});",
            "  }",
        ]

    def _transport_module(self) -> str:
        if self.options.with_transport == "websocket":
            return "./transport"
        return self.options.transport_module

    def endpoints(self) -> str:
        blocks = [
            "export type RpcTransportDescriptor =",
            "  | {",
            '      readonly type: "websocket";',
            '      readonly messageEncoding: "json";',
            "      readonly subprotocols?: readonly string[];",
            "    }",
            "  | {",
            "      readonly type: string;",
            "      readonly [option: string]: unknown;",
            "    };",
            "",
            "type RpcServerVariable = {",
            "  readonly default: string;",
            "  readonly description?: string;",
            "  readonly enum?: readonly string[];",
            "};",
            "",
            "type RpcServerInfo = {",
            "  readonly name: string;",
            "  readonly url: string;",
            "  readonly summary?: string;",
            "  readonly description?: string;",
            "  readonly variables?: Readonly<Record<string, RpcServerVariable>>;",
            "  readonly transport?: RpcTransportDescriptor;",
            "};",
            "",
            "export type ServerName = "
            + " | ".join(json.dumps(server.name) for server in self.ir.servers)
            + ";",
            "",
            "export type Endpoint = {",
            "  readonly server: ServerName;",
            "  readonly url: string;",
            "  readonly subprotocols: readonly string[];",
            "};",
            "",
            "export const servers = {",
        ]
        for server in self.ir.servers:
            blocks.extend(
                [
                    f"  {_identifier(server.name)}: {{",
                    f"    name: {json.dumps(server.name)},",
                    f"    url: {json.dumps(server.url)},",
                ]
            )
            if server.summary:
                blocks.append(f"    summary: {json.dumps(server.summary)},")
            if server.description:
                blocks.append(f"    description: {json.dumps(server.description)},")
            if server.transport is not None:
                blocks.extend(
                    [
                        "    transport: {",
                        f"      type: {json.dumps(server.transport.type)},",
                    ]
                )
                if server.transport.message_encoding is not None:
                    blocks.append(
                        "      messageEncoding: "
                        f"{json.dumps(server.transport.message_encoding)},"
                    )
                if server.transport.subprotocols:
                    blocks.append(
                        f"      subprotocols: {_array(server.transport.subprotocols)},"
                    )
                blocks.extend(
                    f"      {_property(name)}: {json.dumps(value)},"
                    for name, value in server.transport.options
                )
                blocks.append("    },")
            if server.variables:
                blocks.append("    variables: {")
            for variable in server.variables:
                blocks.append(f"      {_property(variable.name)}: {{")
                blocks.append(f"        default: {json.dumps(variable.default)},")
                if variable.description:
                    blocks.append(
                        f"        description: {json.dumps(variable.description)},"
                    )
                if variable.enum:
                    blocks.append(f"        enum: {_array(variable.enum)},")
                blocks.append("      },")
            if server.variables:
                blocks.append("    },")
            blocks.append("  },")
        blocks.extend(["} as const satisfies Record<string, RpcServerInfo>;", ""])
        for server in self.ir.servers:
            blocks.extend(self._endpoint_helper(server))
            blocks.append("")
        blocks.extend(
            [
                "export const endpoints = {",
                *(f"  {_identifier(server.name)}," for server in self.ir.servers),
                "} as const;",
                "",
                "export function resolveEndpoints(",
                "  overrides: readonly Endpoint[],",
                "): readonly Endpoint[] {",
                "  const resolved = new Map<ServerName, Endpoint>([",
                *(
                    f"    [{json.dumps(server.name)}, {_identifier(server.name)}()],"
                    for server in self.ir.servers
                ),
                "  ]);",
                "  const supplied = new Set<ServerName>();",
                "  for (const endpoint of overrides) {",
                "    if (supplied.has(endpoint.server)) {",
                "      throw new Error(`Duplicate endpoint for ${endpoint.server}`);",
                "    }",
                "    supplied.add(endpoint.server);",
                "    resolved.set(endpoint.server, endpoint);",
                "  }",
                "  return [...resolved.values()];",
                "}",
                "",
                "function resolveUrl(",
                "  server: RpcServerInfo,",
                "  values: Readonly<Record<string, string>>,",
                "): string {",
                "  let url = server.url;",
                "  for (const [name, variable] of Object.entries(",
                "    server.variables ?? {},",
                "  )) {",
                "    const value = values[name] ?? variable.default;",
                "    if (",
                "      variable.enum !== undefined &&",
                "      !variable.enum.includes(value)",
                "    ) {",
                "      throw new Error(",
                "        `Invalid value for server variable ${name}: ${value}`,",
                "      );",
                "    }",
                "    url = url.replaceAll(`{${name}}`, value);",
                "  }",
                "  return url;",
                "}",
            ]
        )
        return self.module("\n".join(blocks).rstrip())

    def _endpoint_helper(self, server: ServerDecl) -> list[str]:
        variables = server.variables
        name = _identifier(server.name)
        subprotocols = (
            _array(server.transport.subprotocols)
            if server.transport is not None
            else "[]"
        )
        if not variables:
            return [
                f"function {name}(): Endpoint {{",
                "  return {",
                f"    server: {json.dumps(server.name)},",
                f"    url: servers.{_identifier(server.name)}.url,",
                f"    subprotocols: {subprotocols},",
                "  };",
                "}",
            ]
        lines = [f"function {name}(variables: {{"]
        lines.extend(
            f"  {_property(variable.name)}?: "
            + (
                " | ".join(json.dumps(value) for value in variable.enum)
                if variable.enum
                else "string"
            )
            + ";"
            for variable in variables
        )
        lines.extend(
            [
                "} = {}): Endpoint {",
                "  return {",
                f"    server: {json.dumps(server.name)},",
                f"    url: resolveUrl(servers.{_identifier(server.name)}, variables),",
                f"    subprotocols: {subprotocols},",
                "  };",
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
            lines.append(
                f"export type {{ {_transports_name(self.client_name)} }} "
                'from "./client";'
            )
        if self.options.with_transport == "websocket":
            lines.append('export type { ConnectOptions } from "./client";')
        if self.ir.servers:
            lines.append('export { endpoints, servers } from "./endpoints";')
            lines.append('export type { Endpoint, ServerName } from "./endpoints";')
        if self.options.with_transport == "websocket":
            lines.append('export { WebSocketTransport } from "./transport";')
            lines.append(
                'export type { WebSocketFactory, WebSocketOptions } from "./transport";'
            )
        if _named_errors(self.ir):
            lines.append('export * from "./errors";')
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

    def _notification(
        self,
        event: NotificationDecl,
        *,
        root: bool,
    ) -> list[str]:
        result = self._type(event.payload)
        lines: list[str] = []
        if event.summary:
            lines.append(f"  /** {_comment(event.summary)} */")
        lines.extend(
            [
                f"  {_identifier(event.operation_name)}(): AsyncIterable<{result}> {{",
                f"    return {'this.#rpc' if root else 'this.rpc'}"
                f".notifications<{result}>(",
                f"      {json.dumps(event.rpc_name)},",
                *(
                    [f"      {json.dumps(event.server)},"]
                    if event.server is not None
                    else []
                ),
                "    );",
                "  }",
            ]
        )
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


def _api_module(node: NamespaceViewNode) -> str:
    return "namespaces/" + "/".join(_identifier(segment) for segment in node.path)


def _api_file(node: NamespaceViewNode) -> str:
    path = "/".join(_identifier(segment) for segment in node.path)
    return f"namespaces/{path}/index.ts" if node.children else f"namespaces/{path}.ts"


def _root_prefix(node: NamespaceViewNode) -> str:
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


def _walk(nodes: tuple[NamespaceViewNode, ...]) -> Iterable[NamespaceViewNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


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
