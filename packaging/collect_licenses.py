"""Collect pinned distribution and CPython license material."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from runtime_licenses import RUNTIME_LICENSES


MAX_LICENSE_BYTES = 2 * 1024 * 1024
ROOT = Path(__file__).parents[1]
QT_COMPONENTS = {
    "pyside6",
    "pyside6-addons",
    "pyside6-essentials",
    "shiboken6",
}
QT_LICENSES = (
    ROOT / "licenses" / "LGPL-3.0-only.txt",
    ROOT / "licenses" / "GPL-3.0-only.txt",
)


def _license_files(name: str) -> list[tuple[str, str]]:
    try:
        item = distribution(name)
    except PackageNotFoundError as exc:
        raise ValueError(f"runtime distribution is absent: {name}") from exc
    result: list[tuple[str, str]] = []
    for relative in item.files or ():
        lowered = str(relative).casefold()
        if not any(token in lowered for token in ("license", "copying", "notice")):
            continue
        path = Path(item.locate_file(relative))
        if not path.is_file() or path.stat().st_size > MAX_LICENSE_BYTES:
            continue
        result.append((str(relative), path.read_text(encoding="utf-8", errors="replace")))
    if not result:
        raise ValueError(f"distribution contains no license material: {name}")
    return sorted(result)


def _python_license() -> tuple[str, str]:
    candidates = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sys.base_prefix) / f"LICENSE.{sys.version_info.major}{sys.version_info.minor}.txt",
    )
    for path in candidates:
        if path.is_file() and path.stat().st_size <= MAX_LICENSE_BYTES:
            return str(path.name), path.read_text(encoding="utf-8", errors="replace")
    raise ValueError("CPython license file was not found")


def collect(sbom_path: Path, output: Path) -> dict[str, object]:
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    versions = {
        component["name"].casefold(): component["version"]
        for component in sbom.get("components", ())
    }
    if set(versions) != set(RUNTIME_LICENSES):
        raise ValueError("SBOM components do not match the reviewed runtime licenses")

    sections = [
        "StarCompanion third-party license material",
        "Generated from the pinned build environment and committed SBOM.",
        "",
    ]
    records = 0
    for name in sorted(RUNTIME_LICENSES):
        sections.extend(
            [
                "=" * 78,
                f"{name}=={versions[name]}",
                f"SPDX: {RUNTIME_LICENSES[name]}",
                "=" * 78,
            ]
        )
        if name in QT_COMPONENTS:
            sections.extend(
                ["Qt for Python family license text is consolidated below.", ""]
            )
            continue
        for relative, text in _license_files(name):
            sections.extend([f"--- {relative} ---", text.rstrip(), ""])
            records += 1

    sections.extend(
        [
            "=" * 78,
            "Qt for Python family",
            "SPDX: LGPL-3.0-only",
            "=" * 78,
        ]
    )
    for path in QT_LICENSES:
        if not path.is_file() or path.stat().st_size > MAX_LICENSE_BYTES:
            raise ValueError(f"reviewed Qt license file is absent or invalid: {path.name}")
        sections.extend(
            [
                f"--- licenses/{path.name} ---",
                path.read_text(encoding="utf-8").rstrip(),
                "",
            ]
        )
        records += 1

    python_name, python_text = _python_license()
    sections.extend(
        [
            "=" * 78,
            f"CPython {sys.version.split()[0]}",
            "SPDX: PSF-2.0",
            "=" * 78,
            f"--- {python_name} ---",
            python_text.rstrip(),
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(sections), encoding="utf-8", newline="\n")
    return {"components": len(versions) + 1, "license_files": records + 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sbom", type=Path, default=Path("sbom/starcompanion-runtime.cdx.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("dist/THIRD_PARTY_LICENSES.txt")
    )
    args = parser.parse_args()
    print(json.dumps(collect(args.sbom, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
