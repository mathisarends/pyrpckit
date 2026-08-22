from collections.abc import Mapping
from pathlib import Path


def write_files(
    output_dir: Path,
    files: Mapping[str, str],
    *,
    check: bool = False,
) -> tuple[Path, ...]:
    """Write the rendered files and report which ones differed.

    With ``check`` nothing is written, so the returned paths are the files that
    are out of date.
    """
    changed: list[Path] = []
    for relative_path, content in files.items():
        path = output_dir / relative_path
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        changed.append(path)
        if check:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return tuple(changed)
