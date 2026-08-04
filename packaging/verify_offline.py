"""Run frozen core workflows with Python network/DNS access denied."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from smoke_fixture import GLOBAL_INI, build as build_fixture
from ownership_smoke_fixture import build as build_ownership_fixture
from verify_network_surface import verify as verify_network_surface
from verify_sbom import verify as verify_sbom
from verify_authenticode_report import verify as verify_authenticode_report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    expected: int = 0,
) -> None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if completed.returncode != expected:
        raise RuntimeError(
            f"offline command returned {completed.returncode}, expected {expected}: "
            f"{' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def verify(
    cli: Path,
    gui: Path,
    sbom: Path,
    license_path: Path,
    notice_path: Path,
    authenticode_report: Path | None = None,
) -> dict[str, object]:
    for artifact in (cli, gui, sbom, license_path, notice_path):
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
    sbom_summary = verify_sbom(sbom, Path("pyproject.toml"))
    network_surface = verify_network_surface(Path("src"))
    env = dict(os.environ)
    env["STARCOMPANION_ENFORCE_OFFLINE"] = "1"

    with tempfile.TemporaryDirectory(prefix="starcompanion-offline-") as temporary:
        root = Path(temporary)
        install = build_fixture(root / "LIVE")
        cache = root / "cache.json"
        stock = root / "stock.ini"
        rendered = root / "rendered.json"
        rendered.write_text(
            json.dumps(
                {
                    "Foxwell_Test_title": "[Foxwell] Do the synthetic contract",
                    "Foxwell_Test_desc": "Complete the synthetic job safely.",
                }
            ),
            encoding="utf-8",
        )
        target = install / "data" / "Localization" / "english" / "global.ini"
        _run([str(cli), "profiles"], env=env)
        _run(
            [str(cli), "import", "--install", str(install), "--out", str(cache)],
            env=env,
        )
        _run(
            [
                str(cli),
                "stock",
                "--archive",
                str(install / "Data.p4k"),
                "--out",
                str(stock),
            ],
            env=env,
        )
        _run(
            [
                str(cli),
                "channel",
                "preview",
                "--install",
                str(install),
                "--rendered",
                str(rendered),
            ],
            env=env,
        )
        if target.exists():
            raise RuntimeError("packaged channel preview wrote to the game target")
        apply_command = [
            str(cli),
            "channel",
            "apply",
            "--install",
            str(install),
            "--rendered",
            str(rendered),
        ]
        _run(apply_command, env=env, expected=3)
        if target.exists():
            raise RuntimeError("unconfirmed packaged channel apply wrote to the target")
        _run([*apply_command, "--confirm"], env=env)
        if not target.is_file() or target.read_bytes() == GLOBAL_INI.encode("utf-8"):
            raise RuntimeError("confirmed packaged channel apply did not change the target")
        rollback_command = [
            str(cli),
            "channel",
            "rollback",
            "--install",
            str(install),
        ]
        _run(rollback_command, env=env, expected=3)
        _run([*rollback_command, "--confirm"], env=env)
        if target.read_bytes() != GLOBAL_INI.encode("utf-8"):
            raise RuntimeError("packaged channel rollback did not restore the baseline")
        if not cache.is_file() or not stock.is_file():
            raise RuntimeError("offline packaged workflow did not produce expected outputs")

        ownership_root = root / "ownership-smoke"
        ownership_cache, ownership_log = build_ownership_fixture(ownership_root)
        ownership_data = ownership_root / "player-data"
        ownership_common = [
            str(cli),
            "blueprints",
            "scan",
            "--cache",
            str(ownership_cache),
            "--channel",
            "LIVE",
            "--data-root",
            str(ownership_data),
            "--log",
            str(ownership_log),
        ]
        _run(ownership_common, env=env, expected=3)
        _run([*ownership_common, "--confirm"], env=env)
        first_event = ownership_log.read_text(encoding="utf-8")
        ownership_log.write_text(
            first_event + first_event.replace("17:15:41.684", "17:16:41.684"),
            encoding="utf-8",
        )
        _run([*ownership_common, "--confirm"], env=env)
        _run([*ownership_common, "--confirm"], env=env)

        state_path = ownership_data / "channels" / "LIVE" / "ownership.json"
        if not state_path.with_suffix(".json.bak").is_file():
            raise RuntimeError("packaged ownership workflow did not retain a backup")
        state_path.write_text("{crash-interrupted", encoding="utf-8")
        list_command = [
            str(cli), "blueprints", "list",
            "--cache", str(ownership_cache),
            "--channel", "LIVE", "--data-root", str(ownership_data),
            "--ownership", "owned",
        ]
        _run(list_command, env=env, expected=1)
        recover_command = [
            str(cli), "blueprints", "recover",
            "--cache", str(ownership_cache),
            "--channel", "LIVE", "--data-root", str(ownership_data),
        ]
        _run(recover_command, env=env, expected=3)
        _run([*recover_command, "--confirm"], env=env)
        _run(list_command, env=env)

        _run([str(cli), "channels", "list", "--root", str(install)], env=env)
        _run([str(cli), "languages", "list", "--install", str(install)], env=env)
        portable_data = root / "portable-data"
        language_pack = root / "local-language-pack.ini"
        language_pack.write_text("\ufeffLocal_Only=Local synthetic text\n", encoding="utf-8")
        language_command = [
            str(cli), "languages", "import",
            "--install", str(install), "--language", "english",
            "--file", str(language_pack), "--data-root", str(portable_data),
        ]
        _run(language_command, env=env, expected=3)
        _run([*language_command, "--confirm"], env=env)

        settings_archive = root / "settings.zip"
        export_settings = [
            str(cli), "settings", "export", "--data-root", str(portable_data),
            "--out", str(settings_archive),
        ]
        _run(export_settings, env=env, expected=3)
        _run([*export_settings, "--confirm"], env=env)
        restored_data = root / "restored-data"
        import_settings = [
            str(cli), "settings", "import", "--data-root", str(restored_data),
            "--file", str(settings_archive),
        ]
        _run(import_settings, env=env, expected=3)
        _run([*import_settings, "--confirm"], env=env)

        diagnostics = root / "diagnostics.json"
        diagnostics_command = [
            str(cli), "diagnostics", "export", "--install", str(install),
            "--data-root", str(portable_data), "--out", str(diagnostics),
        ]
        _run(diagnostics_command, env=env, expected=3)
        _run([*diagnostics_command, "--confirm"], env=env)
        diagnostic_text = diagnostics.read_text(encoding="utf-8")
        if str(root) in diagnostic_text or "Local synthetic text" in diagnostic_text:
            raise RuntimeError("packaged diagnostics leaked a path or local value")
        if '"ownership": "excluded"' not in diagnostic_text:
            raise RuntimeError("packaged diagnostics lacks its privacy declaration")

    artifacts = {
        gui.name: _sha256(gui),
        cli.name: _sha256(cli),
        sbom.name: _sha256(sbom),
        license_path.name: _sha256(license_path),
        notice_path.name: _sha256(notice_path),
    }
    authenticode: dict[str, object] = {"status": "not-requested"}
    if authenticode_report is not None:
        authenticode = verify_authenticode_report(
            authenticode_report,
            {"StarCompanion.exe": gui, "starcompanion-cli.exe": cli},
        )
        artifacts[authenticode_report.name] = _sha256(authenticode_report)

    return {
        "offline_guard": True,
        "network_surface": network_surface,
        "sbom": sbom_summary,
        "authenticode": authenticode,
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", type=Path, default=Path("dist/starcompanion-cli.exe"))
    parser.add_argument("--gui", type=Path, default=Path("dist/StarCompanion.exe"))
    parser.add_argument(
        "--sbom", type=Path, default=Path("sbom/starcompanion-runtime.cdx.json")
    )
    parser.add_argument("--license", type=Path, default=Path("LICENSE"))
    parser.add_argument("--notice", type=Path, default=Path("NOTICE"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--authenticode-report", type=Path)
    args = parser.parse_args(argv)
    result = verify(
        args.cli.resolve(),
        args.gui.resolve(),
        args.sbom.resolve(),
        args.license.resolve(),
        args.notice.resolve(),
        args.authenticode_report.resolve()
        if args.authenticode_report is not None
        else None,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.manifest is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
