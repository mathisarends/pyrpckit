from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pyrpckit.codegen.ir import ApiNode, ClientIr, RouteDecl, UnsupportedSchemaError


@dataclass(frozen=True, slots=True)
class ApiView:
    root_operations: tuple[RouteDecl, ...]
    nodes: tuple[ApiViewNode, ...]


@dataclass(frozen=True, slots=True)
class ApiViewNode:
    segment: str
    path: tuple[str, ...]
    source_path: tuple[str, ...]
    operations: tuple[RouteDecl, ...]
    children: tuple[ApiViewNode, ...]


def api_view(
    ir: ClientIr,
    *,
    api_root: str | None,
    api_names: Mapping[str, str],
) -> ApiView:
    root = tuple(api_root.split(".")) if api_root else ()
    if api_root and (any(not part for part in root) or not _root_exists(ir.api, root)):
        raise UnsupportedSchemaError(
            f"api_root {api_root!r} does not identify an API path in the contract"
        )

    matched_names: set[str] = set()
    root_operations = list(ir.root_operations)
    projected: list[tuple[tuple[str, ...], tuple[str, ...], RouteDecl]] = []
    for route in ir.operations:
        if not route.path:
            continue
        visible_source = (
            route.path[len(root) :]
            if root and route.path[: len(root)] == root
            else route.path
        )
        visible = tuple(
            _api_name(
                route.path,
                visible_source,
                index,
                api_names,
                matched_names,
            )
            for index in range(len(visible_source))
        )
        if not visible:
            root_operations.append(route)
        else:
            projected.append((visible, route.path, route))

    unknown = sorted(set(api_names) - matched_names)
    if unknown:
        rendered = ", ".join(repr(name) for name in unknown)
        raise UnsupportedSchemaError(
            f"api_names contains paths that do not exist in the contract: {rendered}"
        )
    return ApiView(tuple(root_operations), _view_tree(projected))


def assert_unique_names(
    owner: str,
    values: Iterable[tuple[str, str]],
) -> None:
    identifiers: dict[str, str] = {}
    for source, identifier in values:
        previous = identifiers.get(identifier)
        if previous is not None and previous != source:
            raise UnsupportedSchemaError(
                f"{owner} maps both {previous!r} and {source!r} to "
                f"identifier {identifier!r}"
            )
        identifiers[identifier] = source


def snake_case(value: str) -> str:
    parts = _words(value)
    return "_".join(part.lower() for part in parts)


def camel_case(value: str) -> str:
    parts = _words(value)
    if not parts:
        return ""
    first, *rest = parts
    return first.lower() + "".join(part[:1].upper() + part[1:].lower() for part in rest)


def pascal_case(value: str) -> str:
    return "".join(part[:1].upper() + part[1:].lower() for part in _words(value))


def _words(value: str) -> tuple[str, ...]:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", separated) if part)


def _root_exists(nodes: tuple[ApiNode, ...], root: tuple[str, ...]) -> bool:
    candidates = nodes
    for segment in root:
        node = next((item for item in candidates if item.segment == segment), None)
        if node is None:
            return False
        candidates = node.children
    return True


def _api_name(
    source_path: tuple[str, ...],
    visible_source: tuple[str, ...],
    index: int,
    aliases: Mapping[str, str],
    matched: set[str],
) -> str:
    root_length = len(source_path) - len(visible_source)
    original_prefix = ".".join(source_path[: root_length + index + 1])
    visible_prefix = ".".join(visible_source[: index + 1])
    segment = visible_source[index]
    for key in (original_prefix, visible_prefix, segment):
        if key in aliases:
            matched.add(key)
            return aliases[key]
    return segment


def _view_tree(
    routes: list[tuple[tuple[str, ...], tuple[str, ...], RouteDecl]],
) -> tuple[ApiViewNode, ...]:
    tree: dict[str, dict[str, object]] = {}
    for visible_path, source_path, route in routes:
        cursor = tree
        for index, segment in enumerate(visible_path):
            node = cursor.setdefault(
                segment,
                {
                    "source_path": source_path[
                        : len(source_path) - len(visible_path) + index + 1
                    ],
                    "operations": [],
                    "children": {},
                },
            )
            if index == len(visible_path) - 1:
                operations = node["operations"]
                assert isinstance(operations, list)
                operations.append(route)
            children = node["children"]
            assert isinstance(children, dict)
            cursor = children
    return tuple(_view_node(segment, node, ()) for segment, node in tree.items())


def _view_node(
    segment: str,
    value: dict[str, object],
    parent: tuple[str, ...],
) -> ApiViewNode:
    children = value["children"]
    operations = value["operations"]
    source_path = value["source_path"]
    assert isinstance(children, dict)
    assert isinstance(operations, list)
    assert isinstance(source_path, tuple)
    path = (*parent, segment)
    return ApiViewNode(
        segment=segment,
        path=path,
        source_path=source_path,
        operations=tuple(operations),
        children=tuple(
            _view_node(child_segment, child, path)
            for child_segment, child in children.items()
        ),
    )
