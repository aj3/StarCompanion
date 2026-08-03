import json
from pathlib import Path

import pytest

from starcompanion.__main__ import EXIT_INVALID, EXIT_OK, EXIT_REFUSED, main
from starcompanion.ini import BOM, LocalizationFile

SAMPLES = Path(__file__).parent / "samples"

STOCK = BOM + "Org_x_title=Original title\nOrg_x_desc=Original body.\nOther=untouched\n"

MINI_CONTRACTS = (
    BOM
    + "Org_x_title=Do a thing <EM4>[100 Rep]</EM4>\n"
    + r"Org_x_desc=Body text.\n\n<EM4>Reputation Awarded:</EM4> 100"
    + "\n"
)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "contracts.ini").write_bytes(MINI_CONTRACTS.encode("utf-8"))
    (tmp_path / "global.ini").write_bytes(STOCK.encode("utf-8"))
    return tmp_path


def run(*argv) -> int:
    return main([str(a) for a in argv])


def chain(workspace) -> None:
    """import -> render, leaving cache.json and rendered.json in place."""
    assert run("import", "--contracts", workspace / "contracts.ini",
               "--out", workspace / "cache.json") == EXIT_OK
    assert run("render", "--cache", workspace / "cache.json",
               "--out", workspace / "rendered.json") == EXIT_OK


# --- happy path --------------------------------------------------------------


def test_import_writes_a_cache(workspace, capsys):
    assert run("import", "--contracts", workspace / "contracts.ini",
               "--out", workspace / "cache.json") == EXIT_OK
    assert (workspace / "cache.json").exists()
    assert "imported 1 contracts" in capsys.readouterr().out


def test_render_writes_key_value_pairs(workspace):
    chain(workspace)
    rendered = json.loads((workspace / "rendered.json").read_text(encoding="utf-8"))
    assert set(rendered) == {"Org_x_title", "Org_x_desc"}
    assert "Reputation Awarded" in rendered["Org_x_desc"]


def test_render_honours_the_chosen_profile(workspace):
    chain(workspace)
    assert run("render", "--cache", workspace / "cache.json",
               "--profile", "minimal", "--out", workspace / "min.json") == EXIT_OK

    rendered = json.loads((workspace / "min.json").read_text(encoding="utf-8"))
    assert "Reputation Awarded" not in rendered["Org_x_desc"]


def test_render_accepts_a_profile_file(workspace, tmp_path):
    chain(workspace)
    profile = tmp_path / "custom.json"
    profile.write_text(json.dumps({"schema_version": 1, "name": "c",
                                   "fields": {"reputation": False}}))

    assert run("render", "--cache", workspace / "cache.json",
               "--profile", profile, "--out", workspace / "c.json") == EXIT_OK
    rendered = json.loads((workspace / "c.json").read_text(encoding="utf-8"))
    assert "Reputation Awarded" not in rendered["Org_x_desc"]


def test_profiles_lists_builtins(capsys):
    assert run("profiles") == EXIT_OK
    out = capsys.readouterr().out
    assert "default" in out and "minimal" in out and "rank-first" in out


def test_inspect_reports_cache_header(workspace, capsys):
    chain(workspace)
    assert run("inspect", "--cache", workspace / "cache.json") == EXIT_OK
    assert "cache_version" in capsys.readouterr().out


# --- plan is read-only -------------------------------------------------------


def test_plan_writes_nothing(workspace, capsys):
    chain(workspace)
    before = (workspace / "global.ini").read_bytes()

    assert run("plan", "--rendered", workspace / "rendered.json",
               "--target", workspace / "global.ini") == EXIT_OK

    assert (workspace / "global.ini").read_bytes() == before
    assert "nothing was written" in capsys.readouterr().out


# --- apply is gated ----------------------------------------------------------


def test_apply_without_confirm_refuses_and_exits_nonzero(workspace):
    chain(workspace)
    before = (workspace / "global.ini").read_bytes()

    code = run("apply", "--rendered", workspace / "rendered.json",
               "--target", workspace / "global.ini")

    assert code == EXIT_REFUSED and code != EXIT_OK
    assert (workspace / "global.ini").read_bytes() == before


def test_apply_with_confirm_writes(workspace):
    chain(workspace)
    assert run("apply", "--rendered", workspace / "rendered.json",
               "--target", workspace / "global.ini",
               "--backup-dir", workspace / "backups", "--confirm") == EXIT_OK

    result = LocalizationFile.load(workspace / "global.ini")
    assert "Reputation Awarded" in result.get("Org_x_desc")
    assert result.get("Other") == "untouched"


def test_apply_backs_up_before_writing(workspace):
    chain(workspace)
    before = (workspace / "global.ini").read_bytes()

    run("apply", "--rendered", workspace / "rendered.json",
        "--target", workspace / "global.ini",
        "--backup-dir", workspace / "backups", "--confirm")

    backups = list((workspace / "backups").iterdir())
    assert len(backups) == 1 and backups[0].read_bytes() == before


def test_restore_returns_the_file_byte_identical(workspace):
    chain(workspace)
    original = (workspace / "global.ini").read_bytes()

    run("apply", "--rendered", workspace / "rendered.json",
        "--target", workspace / "global.ini",
        "--backup-dir", workspace / "backups", "--confirm")
    assert (workspace / "global.ini").read_bytes() != original

    backup = next(iter((workspace / "backups").iterdir()))
    assert run("restore", "--backup", backup, "--target", workspace / "global.ini") == EXIT_OK
    assert (workspace / "global.ini").read_bytes() == original


def test_overwrite_mode_requires_a_stock_file(workspace):
    chain(workspace)
    assert run("apply", "--rendered", workspace / "rendered.json",
               "--target", workspace / "global.ini",
               "--mode", "overwrite", "--confirm") == EXIT_INVALID


# --- game install guard ------------------------------------------------------


@pytest.fixture
def fake_install(tmp_path):
    """A directory shaped like a Star Citizen LIVE folder."""
    root = tmp_path / "LIVE"
    (root / "data" / "Localization" / "english").mkdir(parents=True)
    (root / "Data.p4k").write_bytes(b"not really an archive")
    target = root / "data" / "Localization" / "english" / "global.ini"
    target.write_bytes(STOCK.encode("utf-8"))
    return target


def test_apply_refuses_inside_a_detected_game_install(workspace, fake_install, capsys):
    chain(workspace)
    before = fake_install.read_bytes()

    code = run("apply", "--rendered", workspace / "rendered.json",
               "--target", fake_install, "--confirm")

    assert code == EXIT_REFUSED
    assert fake_install.read_bytes() == before
    assert "--allow-game-folder" in capsys.readouterr().err


def test_apply_proceeds_in_game_install_when_explicitly_allowed(workspace, fake_install):
    chain(workspace)
    before = fake_install.read_bytes()

    assert run("apply", "--rendered", workspace / "rendered.json",
               "--target", fake_install, "--confirm", "--allow-game-folder") == EXIT_OK
    assert fake_install.read_bytes() != before


# --- failure handling --------------------------------------------------------


def test_missing_input_reports_an_error_not_a_traceback(workspace, capsys):
    assert run("import", "--contracts", workspace / "nope.ini",
               "--out", workspace / "cache.json") != EXIT_OK
    assert "error:" in capsys.readouterr().err


def test_unknown_profile_reports_an_error(workspace, capsys):
    chain(workspace)
    assert run("render", "--cache", workspace / "cache.json",
               "--profile", "nonexistent", "--out", workspace / "x.json") != EXIT_OK
    assert "error:" in capsys.readouterr().err


# --- against real data -------------------------------------------------------


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists() or not (SAMPLES / "global.ini").exists(),
    reason="real samples not present",
)
def test_full_chain_against_the_real_corpus(tmp_path):
    """Runs on a scratch copy -- never a real install."""
    target = tmp_path / "global.ini"
    target.write_bytes((SAMPLES / "global.ini").read_bytes())
    original = target.read_bytes()

    assert run("import", "--contracts", SAMPLES / "contracts.ini",
               "--out", tmp_path / "cache.json") == EXIT_OK
    assert run("render", "--cache", tmp_path / "cache.json",
               "--profile", "rank-first", "--out", tmp_path / "rendered.json") == EXIT_OK

    rendered = json.loads((tmp_path / "rendered.json").read_text(encoding="utf-8"))
    assert len(rendered) == 1449

    assert run("apply", "--rendered", tmp_path / "rendered.json", "--target", target,
               "--backup-dir", tmp_path / "backups", "--confirm") == EXIT_OK

    result = LocalizationFile.load(target)
    assert len(result) == 90121, "key count must be preserved"
    assert result.get("Foxwell_ShipAmbush_M_title_001").startswith("[Foxwell 3]")

    backup = next(iter((tmp_path / "backups").iterdir()))
    assert run("restore", "--backup", backup, "--target", target) == EXIT_OK
    assert target.read_bytes() == original


# --- stock extraction --------------------------------------------------------


def stock_archive(tmp_path) -> Path:
    """Built in its own subdirectory: a Data.p4k beside the target would make
    the target look like a game install and trip the write guard."""
    import p4kbuilder as B

    directory = tmp_path / "archive"
    directory.mkdir(exist_ok=True)
    path = directory / "Data.p4k"
    path.write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", STOCK.encode("utf-8"))
        .add("Data/Localization/french/global.ini", b"\xef\xbb\xbfOrg_x_title=Titre\n")
        .build()
    )
    return path


def test_stock_extracts_the_pristine_localization(tmp_path):
    out = tmp_path / "stock-global.ini"
    assert run("stock", "--archive", stock_archive(tmp_path), "--out", out) == EXIT_OK
    assert out.read_bytes() == STOCK.encode("utf-8")


def test_stock_lists_languages(tmp_path, capsys):
    assert run("stock", "--archive", stock_archive(tmp_path), "--list-languages") == EXIT_OK
    assert capsys.readouterr().out.split() == ["english", "french"]


def test_stock_leaves_the_archive_untouched(tmp_path):
    archive = stock_archive(tmp_path)
    before = archive.read_bytes()

    run("stock", "--archive", archive, "--out", tmp_path / "out.ini")

    assert archive.read_bytes() == before


def test_stock_reports_a_missing_language(tmp_path, capsys):
    code = run(
        "stock", "--archive", stock_archive(tmp_path),
        "--language", "klingon", "--out", tmp_path / "out.ini",
    )
    assert code != EXIT_OK
    assert "english, french" in capsys.readouterr().err


def test_stock_reports_a_bad_archive(tmp_path, capsys):
    bad = tmp_path / "bad.p4k"
    bad.write_bytes(b"not an archive at all")
    assert run("stock", "--archive", bad, "--out", tmp_path / "out.ini") != EXIT_OK
    assert "error:" in capsys.readouterr().err


def test_extracted_stock_feeds_overwrite_mode(workspace, tmp_path):
    """The whole point: overwrite mode no longer needs a hand-supplied file."""
    chain(workspace)
    stock = tmp_path / "stock-global.ini"
    run("stock", "--archive", stock_archive(tmp_path), "--out", stock)

    target = workspace / "global.ini"
    target.write_bytes((BOM + "Org_x_title=someone elses pack\nOrg_x_desc=x\nOther=y\n").encode())

    assert run("apply", "--rendered", workspace / "rendered.json", "--target", target,
               "--mode", "overwrite", "--stock", stock,
               "--backup-dir", workspace / "backups", "--confirm") == EXIT_OK

    result = LocalizationFile.load(target)
    assert "Reputation Awarded" in result.get("Org_x_desc")
    assert result.get("Other") == "untouched", "rebuilt from stock, not the modified file"
