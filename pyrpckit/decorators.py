import inspect
from typing import Any


def _docstring_summary(handler: Any) -> str | None:
    docstring = inspect.getdoc(handler)
    if not docstring:
        return None
    return docstring.splitlines()[0].strip() or None
