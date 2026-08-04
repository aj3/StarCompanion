"""Verify that built package metadata and bundled notices declare Apache-2.0."""

from __future__ import annotations

import argparse
import json
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def verify(wheel: Path, root: Path) -> dict[str, object]:
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("wheel must contain exactly one METADATA file")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata.get("License-Expression") != "Apache-2.0":
            raise ValueError("wheel License-Expression must be Apache-2.0")
        if set(metadata.get_all("License-File", ())) != {"LICENSE", "NOTICE"}:
            raise ValueError("wheel must declare LICENSE and NOTICE")

        dist_info = metadata_names[0].rsplit("/", 1)[0]
        for filename in ("LICENSE", "NOTICE"):
            member = f"{dist_info}/licenses/{filename}"
            if member not in names:
                raise ValueError(f"wheel does not bundle {filename}")
            if archive.read(member) != (root / filename).read_bytes():
                raise ValueError(f"wheel {filename} differs from repository copy")

    return {"wheel": wheel.name, "license": "Apache-2.0", "files": ["LICENSE", "NOTICE"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path, help="wheel file or directory containing one")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    artifact = args.artifact
    if artifact.is_dir():
        wheels = sorted(artifact.glob("starcompanion-*.whl"))
        if len(wheels) != 1:
            raise ValueError(f"expected one StarCompanion wheel in {artifact}")
        artifact = wheels[0]
    print(json.dumps(verify(artifact, args.root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
