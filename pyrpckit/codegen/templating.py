from functools import lru_cache
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("pyrpckit.codegen", "templates"),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def render_template(name: str, **context: Any) -> str:
    return _environment().get_template(name).render(**context)
