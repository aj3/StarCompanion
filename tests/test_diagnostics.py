import json

import p4kbuilder as B

from starcompanion.diagnostics import build_diagnostics, render_diagnostics, write_diagnostics
from starcompanion.install import GameInstall
from starcompanion.portability import LanguagePackStore, PreferencesStore
from starcompanion.user_edits import UserEditStore


def test_diagnostics_redact_paths_values_logs_ownership_and_game_strings(tmp_path):
    secret = "ULTRA_PRIVATE_VALUE_7821"
    data = tmp_path / "User Name" / "portable-data"
    install_root = tmp_path / "User Name" / "Games" / "LIVE"
    install_root.mkdir(parents=True)
    (install_root / "Data.p4k").write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", f"Secret_Key={secret}\n".encode())
        .build()
    )
    game = GameInstall(install_root, "LIVE", str(tmp_path / "private-build"))
    PreferencesStore(data).save({"default_channel": "LIVE", "theme": "dark"})
    UserEditStore("LIVE", "english", root=data).save({"Secret_Key": secret})
    LanguagePackStore("LIVE", "french", data).save({"Secret_Key": secret})
    (data / "channels" / "LIVE" / "ownership.json").write_text(secret, encoding="utf-8")
    (install_root / "Game.log").write_text(secret, encoding="utf-8")

    report = build_diagnostics([game], root=data)
    rendered = render_diagnostics(report).decode("utf-8")

    assert secret not in rendered
    assert "Secret_Key" not in rendered
    assert str(tmp_path.resolve()) not in rendered
    assert "User Name" not in rendered
    assert report["privacy"]["ownership"] == "excluded"
    assert report["privacy"]["log_content"] == "excluded"
    assert report["portable_data"]["scopes"][0]["user_overrides"]["entries"] == 1
    assert report["installs"][0]["languages"] == ["english"]
    assert report["network"]["automatic_telemetry"] is False


def test_diagnostics_export_is_atomic_and_overwrite_guarded(tmp_path):
    report = build_diagnostics(root=tmp_path / "data")
    output = tmp_path / "diagnostics.json"
    write_diagnostics(report, output)
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == 1

    import pytest
    from starcompanion.diagnostics import DiagnosticsError

    with pytest.raises(DiagnosticsError, match="overwrite"):
        write_diagnostics(report, output)
    write_diagnostics(report, output, overwrite=True)
    assert not list(tmp_path.glob(".diagnostics.json.*.tmp"))


def test_corrupt_private_files_report_only_invalid_status(tmp_path):
    data = tmp_path / "data"
    user = UserEditStore("PTU", "english", root=data).path
    user.parent.mkdir(parents=True)
    user.write_bytes(b"\xffnot-utf8")
    report = build_diagnostics(root=data)
    serialized = json.dumps(report)
    assert "not-utf8" not in serialized
    assert report["portable_data"]["scopes"][0]["user_overrides"]["status"] == "invalid"
