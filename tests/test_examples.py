import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = tuple(sorted((Path(__file__).parents[1] / "examples").glob("*.py")))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_example_runs(example: Path) -> None:
    subprocess.run(
        [sys.executable, str(example)],
        check=True,
        capture_output=True,
        text=True,
    )
