from copy import deepcopy
from typing import Any

import pytest

from pyrpckit.codegen import render_python_client
from pyrpckit.codegen.ir import UnsupportedSchemaError
from pyrpckit.codegen.names import camel_case, pascal_case, snake_case
from pyrpckit.codegen.python import PythonClientOptions


def test_identifier_styles_handle_acronyms_stably() -> None:
    assert snake_case("parseURL") == "parse_url"
    assert snake_case("HTTPStatus") == "http_status"
    assert camel_case("parseURL") == "parseUrl"
    assert pascal_case("browser-nav") == "BrowserNav"


def test_api_aliases_cannot_merge_distinct_wire_paths(
    document: dict[str, Any],
) -> None:
    collision = deepcopy(document)
    first = collision["methods"][0]
    second = deepcopy(first)
    first["name"] = "browser.nav.tabs.open"
    second["name"] = "browser.navigation.history.close"
    collision["methods"] = [first, second]

    with pytest.raises(UnsupportedSchemaError, match="both map to 'navigation'"):
        render_python_client(
            collision,
            PythonClientOptions(
                package="browser_client",
                api_root="browser",
                api_names={"nav": "navigation"},
            ),
        )


def test_root_operations_cannot_shadow_client_lifecycle(
    document: dict[str, Any],
) -> None:
    collision = deepcopy(document)
    collision["methods"][0]["name"] = "close"
    collision["methods"] = [collision["methods"][0]]

    with pytest.raises(UnsupportedSchemaError, match="root client"):
        render_python_client(
            collision,
            PythonClientOptions(package="client"),
        )
