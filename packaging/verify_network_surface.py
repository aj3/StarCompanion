"""Reject application imports that can introduce an unreviewed network path."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

_FORBIDDEN_ROOTS = {
    "aiohttp",
    "ftplib",
    "http",
    "httpx",
    "requests",
    "socket",
    "ssl",
    "urllib",
    "websockets",
}
_URL = re.compile(r"https?://[^\s\])>'\"`]+")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def verify(source_root: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    guard = (source_root / "starcompanion" / "offline.py").resolve()
    forbidden: list[str] = []
    urls: set[str] = set()
    files = sorted(source_root.rglob("*.py"))
    for path in files:
        source = path.read_text(encoding="utf-8")
        urls.update(_URL.findall(source))
        for name in sorted(_imports(path)):
            root = name.split(".", 1)[0]
            blocked = root in _FORBIDDEN_ROOTS or name.startswith("PySide6.QtNetwork")
            if name == "PySide6.QtNetwork" or name.endswith(".QtNetwork"):
                blocked = True
            if blocked and not (path.resolve() == guard and root == "socket"):
                forbidden.append(f"{path.relative_to(source_root)}: {name}")
    if forbidden:
        raise RuntimeError("unreviewed network-capable imports:\n" + "\n".join(forbidden))
    return {
        "python_files_scanned": len(files),
        "network_imports": "offline guard only",
        "external_urls": sorted(urls),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", nargs="?", type=Path, default=Path("src"))
    args = parser.parse_args(argv)
    print(json.dumps(verify(args.source_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
