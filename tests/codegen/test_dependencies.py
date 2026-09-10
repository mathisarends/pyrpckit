import subprocess
import sys


def test_schema_cli_import_does_not_require_jinja() -> None:
    code = """
import importlib.abc
import sys

class BlockJinja(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "jinja2" or fullname.startswith("jinja2."):
            raise ModuleNotFoundError("blocked jinja2")
        return None

sys.meta_path.insert(0, BlockJinja())
import pyrpckit
import pyrpckit.codegen
import pyrpckit.codegen.cli
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
