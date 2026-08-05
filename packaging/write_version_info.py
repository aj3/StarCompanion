"""Generate Windows version resources from project metadata."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def _version(root: Path) -> tuple[str, tuple[int, int, int, int]]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    parts = project["version"].split(".")
    if len(parts) not in {3, 4} or any(not part.isdigit() for part in parts):
        raise ValueError("release version must contain three or four numeric parts")
    numbers = tuple(int(part) for part in parts)
    padded = (*numbers, *(0 for _ in range(4 - len(numbers))))
    return ".".join(str(part) for part in padded), padded


def _render(version: str, numbers: tuple[int, int, int, int], filename: str) -> str:
    description = (
        "StarCompanion command-line application"
        if filename.casefold().startswith("starcompanion-cli")
        else "StarCompanion desktop application"
    )
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'StarCompanion contributors'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{Path(filename).stem}'),
      StringStruct('LegalCopyright', 'Copyright 2026 StarCompanion contributors'),
      StringStruct('OriginalFilename', '{filename}'),
      StringStruct('ProductName', 'StarCompanion'),
      StringStruct('ProductVersion', '{version}')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write(root: Path, output: Path) -> str:
    version, numbers = _version(root)
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("StarCompanion.exe", "starcompanion-cli.exe"):
        destination = output / f"{filename}.version-info.txt"
        destination.write_text(
            _render(version, numbers, filename), encoding="utf-8", newline="\n"
        )
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("build/version-info"))
    parser.add_argument("--print-signing-version", action="store_true")
    args = parser.parse_args()
    version = write(args.root, args.output)
    if args.print_signing_version:
        print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
