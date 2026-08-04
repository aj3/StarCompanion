"""Validate the committed runtime SBOM against project metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path


EXCLUDED_TOOLING = {
    "pytest",
    "pyinstaller",
    "pip-audit",
    "cyclonedx-bom",
}


def verify(sbom_path: Path, project_path: Path) -> dict[str, object]:
    project = tomllib.loads(project_path.read_text(encoding="utf-8"))["project"]
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        raise ValueError("SBOM must be CycloneDX JSON specification 1.6")

    root = sbom.get("metadata", {}).get("component", {})
    if root.get("name") != project["name"] or root.get("version") != project["version"]:
        raise ValueError("SBOM root component does not match pyproject.toml")
    expected_license = [{"license": {"id": project["license"]}}]
    if root.get("licenses") != expected_license:
        raise ValueError("SBOM root license does not match pyproject.toml")

    components = {
        component.get("name", "").casefold(): component
        for component in sbom.get("components", ())
    }
    direct = {
        dependency.split("==", 1)[0].casefold(): dependency.split("==", 1)[1]
        for dependency in project["dependencies"]
    }
    for name, version in direct.items():
        component = components.get(name)
        if component is None or component.get("version") != version:
            raise ValueError(f"SBOM is missing locked runtime component {name}=={version}")

    root_ref = root.get("bom-ref")
    root_graph = next(
        (item for item in sbom.get("dependencies", ()) if item.get("ref") == root_ref),
        None,
    )
    direct_refs = {
        components[name]["bom-ref"]
        for name in direct
    }
    if root_graph is None or set(root_graph.get("dependsOn", ())) != direct_refs:
        raise ValueError("SBOM root dependency graph is incomplete")

    included_tools = sorted(EXCLUDED_TOOLING & components.keys())
    if included_tools:
        raise ValueError(f"runtime SBOM includes non-runtime tooling: {included_tools}")

    digest = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
    return {
        "component": f"{project['name']}=={project['version']}",
        "dependencies": len(components),
        "sha256": digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sbom", type=Path)
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    result = verify(args.sbom, args.project)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
