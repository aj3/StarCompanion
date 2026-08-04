"""Complete and canonicalize CycloneDX root dependency relationships."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


def finalize(sbom_path: Path, project_path: Path) -> None:
    project = tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    root = sbom["metadata"]["component"]
    root["licenses"] = [{"license": {"id": project["license"]}}]
    direct_names = {
        dependency.split("==", 1)[0].casefold()
        for dependency in project["dependencies"]
    }
    direct_refs = sorted(
        component["bom-ref"]
        for component in sbom.get("components", ())
        if component.get("name", "").casefold() in direct_names
    )
    if len(direct_refs) != len(direct_names):
        raise ValueError("cannot map every direct dependency into the generated SBOM")

    root_ref = root["bom-ref"]
    root_dependencies = next(
        (entry for entry in sbom.get("dependencies", ()) if entry.get("ref") == root_ref),
        None,
    )
    if root_dependencies is None:
        raise ValueError("generated SBOM has no root dependency record")
    root_dependencies["dependsOn"] = direct_refs
    sbom_path.write_text(
        json.dumps(sbom, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sbom", type=Path)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    finalize(args.sbom, args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
