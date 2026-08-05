from __future__ import annotations

import json
import hashlib
import runpy
import tomllib
from pathlib import Path

import pytest


VERIFY = runpy.run_path(
    str(Path(__file__).parents[1] / "packaging" / "verify_network_surface.py")
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
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert "Apache License" in (root / "LICENSE").read_text(encoding="utf-8")
    assert "StarCompanion contributors" in (root / "NOTICE").read_text(
        encoding="utf-8"
    )


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
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname="example"\nversion="1.0"\nlicense="Apache-2.0"\n'
        "dependencies=[]\n",
        encoding="utf-8",
    )
    sbom = tmp_path / "sbom.json"
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {
            "component": {
                "bom-ref": "root",
                "name": "example",
                "type": "application",
                "version": "1.0",
            }
        },
        "components": [],
        "dependencies": [{"ref": "root", "dependsOn": []}],
    }
    sbom.write_text(json.dumps(document), encoding="utf-8")

    FINALIZE_SBOM(sbom, project)
    assert VERIFY_SBOM(sbom, project)["component"] == "example==1.0"

    changed = json.loads(sbom.read_text(encoding="utf-8"))
    changed["metadata"]["component"].pop("licenses")
    sbom.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="root license"):
        VERIFY_SBOM(sbom, project)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_authenticode_report_binds_both_signed_artifacts(tmp_path: Path) -> None:
    gui = tmp_path / "StarCompanion.exe"
    cli = tmp_path / "starcompanion-cli.exe"
    gui.write_bytes(b"signed-gui")
    cli.write_bytes(b"signed-cli")
    report = tmp_path / "authenticode-report.json"
    report.write_text(
        json.dumps(
            {
                "schema": "starcompanion.authenticode.v1",
                "status": "valid",
                "timestamp_url": "https://timestamp.example.invalid",
                "certificate": {
                    "subject": "CN=StarCompanion Test",
                    "thumbprint": "A" * 40,
                    "not_after_utc": "2027-08-04T12:00:00Z",
                },
                "artifacts": {
                    gui.name: _sha256(gui),
                    cli.name: _sha256(cli),
                },
            }
        ),
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
        json.dumps(
            {
                "schema": "starcompanion.authenticode.v1",
                "status": "valid",
                "timestamp_url": "https://timestamp.example.invalid",
                "certificate": {
                    "subject": "CN=StarCompanion Test",
                    "thumbprint": "B" * 40,
                    "not_after_utc": "2027-08-04T12:00:00Z",
                },
                "artifacts": {
                    gui.name: _sha256(gui),
                    cli.name: _sha256(cli),
                },
            }
        ),
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
    script = (root / "packaging" / "sign_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.sign_release" in workflow
    assert "environment: release-signing" in workflow
    assert "StarCompanion-windows-signed" in workflow
    assert "STARCOMPANION_SIGNING_PFX_BASE64: ${{ secrets." in workflow
    assert '"/p"' not in script.lower()
    assert "Import-PfxCertificate" in script
    assert '"verify", "/pa", "/all", "/v"' in script
    assert "TimeStamperCertificate" in script
    assert "finally" in script


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
