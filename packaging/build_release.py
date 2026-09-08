"""Build frozen artifacts without inheriting unrelated host DLL paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_environment() -> dict[str, str]:
    """Keep only Python and Windows system locations on the Windows DLL path."""
    environment = dict(os.environ)
    if os.name != "nt":
        return environment

    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = (
        Path(sys.executable).resolve().parent,
        Path(sys.prefix).resolve(),
        Path(sys.prefix).resolve() / "Scripts",
        Path(sys.base_prefix).resolve(),
        Path(sys.base_prefix).resolve() / "DLLs",
        windows / "System32",
        windows,
    )
    paths: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate)
        key = value.casefold()
        if candidate.exists() and key not in seen:
            seen.add(key)
            paths.append(value)
    environment["PATH"] = os.pathsep.join(paths)
    return environment


def main() -> int:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            str(ROOT / "packaging" / "starcompanion.spec"),
        ],
        cwd=ROOT,
        env=build_environment(),
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
