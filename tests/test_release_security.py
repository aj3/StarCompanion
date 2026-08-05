from __future__ import annotations

import json
import hashlib
import runpy
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

VERIFY = runpy.run_path(
    str(ROOT / "packaging" / "verify_network_surface.py")
)["verify"]
FINALIZE_SBOM = runpy.run_path(
    str(Path(__file__).parents[1] / "packaging" / "finalize_sbom.py")
)["finalize"]
VERIFY_SBOM = runpy.run_path(
    str(Path(__file__).parents[1] / "packaging" / "verify_sbom.py")
)["verify"]
VERIFY_AUTHENTICODE_REPORT = runpy.run_path(
    str(
        Path(__file__).parents[1]
        / "packaging"
        / "verify_authenticode_report.py"
    )
)["verify"]
BUILD_OWNERSHIP_SMOKE_FIXTURE = runpy.run_path(
    str(
        Path(__file__).parents[1]
        / "packaging"
        / "ownership_smoke_fixture.py"
    )
)["build"]


def test_network_surface_allows_only_the_socket_blocking_guard(tmp_path: Path) -> None:
    package = tmp_path / "starcompanion"
    package.mkdir()
    (package / "offline.py").write_text("import socket\n", encoding="utf-8")
    (package / "help.py").write_text(
        'URL = "https://example.invalid/help"\n', encoding="utf-8"
    )

    result = VERIFY(tmp_path)

    assert result["network_imports"] == "offline guard only"
    assert result["external_urls"] == ["https://example.invalid/help"]


@pytest.mark.parametrize(
    "source",
    [
        "import socket\n",
        "import requests\n",
        "from urllib.request import urlopen\n",
        "from PySide6 import QtNetwork\n",
    ],
)
def test_network_surface_rejects_unreviewed_network_imports(
    tmp_path: Path, source: str
) -> None:
    package = tmp_path / "starcompanion"
    package.mkdir()
    (package / "offline.py").write_text("import socket\n", encoding="utf-8")
    (package / "unexpected.py").write_text(source, encoding="utf-8")

    with pytest.raises(RuntimeError, match="unreviewed network-capable imports"):
        VERIFY(tmp_path)


def test_frozen_gui_explicitly_excludes_qt_network() -> None:
    root = Path(__file__).parents[1]
    spec = (root / "packaging" / "starcompanion.spec").read_text(encoding="utf-8")

    assert '"PySide6.QtNetwork"' in spec


def test_project_declares_apache_license_and_notice() -> None:
    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == [
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "licenses/GPL-3.0-only.txt",
        "licenses/LGPL-3.0-only.txt",
    ]
    assert "Apache License" in (root / "LICENSE").read_text(encoding="utf-8")
    assert "StarCompanion contributors" in (root / "NOTICE").read_text(
        encoding="utf-8"
    )


def test_qt_runtime_uses_reviewed_lgpl_license_text() -> None:
    from runtime_licenses import RUNTIME_LICENSES

    for name in ("pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6"):
        assert RUNTIME_LICENSES[name] == "LGPL-3.0-only"
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (
        ROOT / "licenses" / "LGPL-3.0-only.txt"
    ).read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in (
        ROOT / "licenses" / "GPL-3.0-only.txt"
    ).read_text(encoding="utf-8")


def test_package_and_project_versions_stay_in_sync() -> None:
    from starcompanion import __version__

    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert __version__ == project["version"]


def test_sbom_finalizer_records_and_verifier_requires_project_license(
    tmp_path: Path,
) -> None:
    project = ROOT / "pyproject.toml"
    sbom = tmp_path / "sbom.json"
    sbom.write_bytes((ROOT / "sbom" / "starcompanion-runtime.cdx.json").read_bytes())

    FINALIZE_SBOM(sbom, project)
    assert VERIFY_SBOM(sbom, project)["component"] == "starcompanion==0.2.0"

    changed = json.loads(sbom.read_text(encoding="utf-8"))
    changed["metadata"]["component"].pop("licenses")
    sbom.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="root license"):
        VERIFY_SBOM(sbom, project)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signpath_report(gui: Path, cli: Path, thumbprint: str) -> dict[str, object]:
    timestamp = {
        "subject": "CN=Test Timestamp Authority",
        "thumbprint": "C" * 40,
        "not_after_utc": "2030-08-04T12:00:00Z",
    }
    return {
        "schema": "starcompanion.authenticode.v2",
        "status": "valid",
        "provider": "SignPath.io",
        "certificate": {
            "subject": "CN=SignPath Foundation",
            "thumbprint": thumbprint,
            "not_after_utc": "2027-08-04T12:00:00Z",
        },
        "timestamp_authorities": {
            gui.name: timestamp,
            cli.name: timestamp,
        },
        "artifacts": {
            gui.name: _sha256(gui),
            cli.name: _sha256(cli),
        },
    }


def test_authenticode_report_binds_both_signed_artifacts(tmp_path: Path) -> None:
    gui = tmp_path / "StarCompanion.exe"
    cli = tmp_path / "starcompanion-cli.exe"
    gui.write_bytes(b"signed-gui")
    cli.write_bytes(b"signed-cli")
    report = tmp_path / "authenticode-report.json"
    report.write_text(
        json.dumps(_signpath_report(gui, cli, "A" * 40)),
        encoding="utf-8",
    )

    result = VERIFY_AUTHENTICODE_REPORT(
        report, {gui.name: gui, cli.name: cli}
    )

    assert result["status"] == "valid"
    assert result["certificate_thumbprint"] == "A" * 40


def test_authenticode_report_rejects_post_verification_change(tmp_path: Path) -> None:
    gui = tmp_path / "StarCompanion.exe"
    cli = tmp_path / "starcompanion-cli.exe"
    gui.write_bytes(b"signed-gui")
    cli.write_bytes(b"signed-cli")
    report = tmp_path / "authenticode-report.json"
    report.write_text(
        json.dumps(_signpath_report(gui, cli, "B" * 40)),
        encoding="utf-8",
    )
    cli.write_bytes(b"changed-after-signing")

    with pytest.raises(ValueError, match="changed after verification"):
        VERIFY_AUTHENTICODE_REPORT(report, {gui.name: gui, cli.name: cli})


def test_signing_workflow_is_manual_protected_and_fail_closed() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    script = (root / "packaging" / "report_signpath.ps1").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.sign_release" in workflow
    assert "environment: release-signing" in workflow
    assert "StarCompanion-windows-signed" in workflow
    assert "signpath/github-action-submit-signing-request@b9d91e" in workflow
    assert "secrets.SIGNPATH_API_TOKEN" in workflow
    assert "github-artifact-id: ${{ needs.build-windows.outputs.artifact-id }}" in workflow
    assert "WINDOWS_CODESIGN" not in workflow
    assert not (root / "packaging" / "sign_windows.ps1").exists()
    assert "Get-AuthenticodeSignature" in script
    assert "SignPath Foundation" in script
    assert "TimeStamperCertificate" in script
    assert "starcompanion.authenticode.v2" in script


def test_signpath_configuration_restricts_identity_and_artifact_set() -> None:
    root = Path(__file__).parents[1]
    configuration = (root / ".signpath" / "artifact-configuration.xml").read_text(
        encoding="utf-8"
    )

    assert configuration.count("<include path=") == 2
    assert 'product-name="StarCompanion"' in configuration
    assert 'product-version="${version}"' in configuration
    assert 'company-name="StarCompanion contributors"' in configuration
    assert "dist/StarCompanion.exe" in configuration
    assert "dist/starcompanion-cli.exe" in configuration


def test_windows_metadata_is_generated_from_project_version(tmp_path: Path) -> None:
    writer = runpy.run_path(str(ROOT / "packaging" / "write_version_info.py"))["write"]

    version = writer(ROOT, tmp_path)

    assert version == "0.2.0.0"
    gui = (tmp_path / "StarCompanion.exe.version-info.txt").read_text(
        encoding="utf-8"
    )
    assert "StringStruct('ProductName', 'StarCompanion')" in gui
    assert "StringStruct('ProductVersion', '0.2.0.0')" in gui
    assert "StringStruct('OriginalFilename', 'StarCompanion.exe')" in gui


def test_release_workflow_builds_and_offline_smokes_both_platforms() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "build-windows:" in workflow
    assert "build-ubuntu:" in workflow
    assert "StarCompanion-windows-unsigned" in workflow
    assert "StarCompanion-ubuntu-unsigned" in workflow
    assert "pytest -q --ignore=tests/test_gui.py" in workflow
    assert "pytest -vv -s tests/test_gui.py" in workflow
    assert "pytest -q tests/test_gui_screenshots.py" in workflow
    assert workflow.count("packaging/verify_offline.py") == 1
    assert workflow.count("packaging\\verify_offline.py") == 2

    gui_entry = (root / "src" / "starcompanion" / "gui" / "app.py").read_text(
        encoding="utf-8"
    )
    packaged_smoke = (root / "packaging" / "verify_offline.py").read_text(
        encoding="utf-8"
    )
    assert '"--smoke-test"' in gui_entry
    assert '_run([str(gui), "--smoke-test"]' in packaged_smoke
    assert 'env["STARCOMPANION_ENFORCE_OFFLINE"] = "1"' in packaged_smoke


def test_dependency_free_ownership_smoke_fixture_matches_cache_schema(
    tmp_path: Path,
) -> None:
    from starcompanion import cache

    cache_path, log_path = BUILD_OWNERSHIP_SMOKE_FIXTURE(tmp_path)

    contracts = cache.load(cache_path)
    pool = contracts.contracts[0].reward.blueprint_pools[0]
    assert pool.item_ids == {
        "Synthetic Coda": "11111111-1111-1111-1111-111111111111"
    }
    assert "Received Blueprint: Synthetic Coda" in log_path.read_text(
        encoding="utf-8"
    )
