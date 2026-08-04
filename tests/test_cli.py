import json
from pathlib import Path

import pytest

from starcompanion.__main__ import EXIT_ERROR, EXIT_INVALID, EXIT_OK, EXIT_REFUSED, main
from starcompanion.ini import BOM, LocalizationFile
from starcompanion import cache
from starcompanion.model import BlueprintPool, Contract, ContractSet, Evidence, Org, Reward

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


def blueprint_cache(workspace: Path) -> Path:
    org = Org("test", "Test Org")
    contracts = ContractSet(
        [
            Contract(
                "Test_Mission",
                org,
                "Mission",
                reward=Reward(
                    blueprint_pools=[
                        BlueprintPool(
                            items=["Coda Pistol"],
                            item_ids={
                                "Coda Pistol": "11111111-1111-1111-1111-111111111111"
                            },
                            item_categories={"Coda Pistol": "weapons"},
                        )
                    ]
                ),
            )
        ],
        {org.id: org},
    )
    path = workspace / "blueprints-cache.json"
    cache.save(contracts, path, source="game:LIVE:test:english")
    return path


def ambiguous_blueprint_cache(workspace: Path) -> Path:
    org = Org("test", "Test Org")
    contracts = ContractSet(
        [
            Contract(
                f"Test_Mission_{index}",
                org,
                "Mission",
                reward=Reward(
                    blueprint_pools=[
                        BlueprintPool(
                            items=["Coda Pistol"],
                            item_ids={"Coda Pistol": blueprint_id},
                        )
                    ]
                ),
            )
            for index, blueprint_id in enumerate(
                (
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                )
            )
        ],
        {org.id: org},
    )
    path = workspace / "ambiguous-cache.json"
    cache.save(contracts, path, source="game:LIVE:test:english")
    return path


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


def test_blueprint_cli_scan_preview_confirm_query_and_diagnostics(workspace, capsys):
    source_cache = blueprint_cache(workspace)
    data_root = workspace / "player-data"
    log = workspace / "Game.log"
    log.write_text(
        '<2026-03-26T17:15:41.684Z> [Notice] '
        '<SHUDEvent_OnNotification> Added notification '
        '"Received Blueprint: Coda Pistol: " [23] to queue.\n',
        encoding="utf-8",
    )
    common = (
        "--cache", source_cache, "--channel", "LIVE", "--data-root", data_root
    )
    assert run("blueprints", "scan", *common, "--log", log) == EXIT_REFUSED
    assert not (data_root / "channels" / "LIVE" / "ownership.json").exists()
    assert run("blueprints", "scan", *common, "--log", log, "--confirm") == EXIT_OK
    state_path = data_root / "channels" / "LIVE" / "ownership.json"
    before = state_path.read_bytes()
    assert run("blueprints", "scan", *common, "--log", log, "--confirm") == EXIT_OK
    assert state_path.read_bytes() == before
    assert "no ownership or watermark write was needed" in capsys.readouterr().out
    assert run(
        "blueprints", "list", *common, "--ownership", "owned", "--search", "coda"
    ) == EXIT_OK
    assert "Coda Pistol" in capsys.readouterr().out
    assert run("blueprints", "diagnostics", *common) == EXIT_OK


def test_blueprint_cli_corruption_requires_previewed_backup_recovery(workspace, capsys):
    source_cache = blueprint_cache(workspace)
    data_root = workspace / "player-data"
    log = workspace / "Game.log"
    common = (
        "--cache", source_cache, "--channel", "LIVE", "--data-root", data_root
    )
    first = (
        '<2026-03-26T17:15:41.684Z> [Notice] '
        '<SHUDEvent_OnNotification> Added notification '
        '"Received Blueprint: Coda Pistol: " [23] to queue.\n'
    )
    second = first.replace("17:15:41.684", "17:16:41.684")
    log.write_text(first, encoding="utf-8")
    assert run("blueprints", "scan", *common, "--log", log, "--confirm") == EXIT_OK
    log.write_text(first + second, encoding="utf-8")
    assert run("blueprints", "scan", *common, "--log", log, "--confirm") == EXIT_OK

    state_path = data_root / "channels" / "LIVE" / "ownership.json"
    backup_path = state_path.with_suffix(".json.bak")
    assert backup_path.is_file()
    state_path.write_text("{crash-interrupted", encoding="utf-8")

    assert run("blueprints", "list", *common) == EXIT_ERROR
    assert "blueprints recover" in capsys.readouterr().err
    damaged = state_path.read_bytes()
    assert run("blueprints", "recover", *common) == EXIT_REFUSED
    assert state_path.read_bytes() == damaged
    assert "nothing was written" in capsys.readouterr().out
    assert run("blueprints", "recover", *common, "--confirm") == EXIT_OK
    assert run("blueprints", "list", *common, "--ownership", "owned") == EXIT_OK
    assert "Coda Pistol" in capsys.readouterr().out


def test_blueprint_cli_import_export_are_preview_first(workspace):
    source_cache = blueprint_cache(workspace)
    data_root = workspace / "player-data"
    imported = workspace / "scmdb.json"
    exported = workspace / "owned.csv"
    imported.write_text(
        json.dumps({"blueprints": [{"name": "Coda Pistol", "completed": True}]}),
        encoding="utf-8",
    )
    common = (
        "--cache", source_cache, "--channel", "LIVE", "--data-root", data_root
    )
    assert run("blueprints", "import", *common, "--file", imported) == EXIT_REFUSED
    assert run(
        "blueprints", "import", *common, "--file", imported, "--confirm"
    ) == EXIT_OK
    assert run("blueprints", "export", *common, "--out", exported) == EXIT_REFUSED
    assert not exported.exists()
    assert run(
        "blueprints", "export", *common, "--out", exported, "--confirm"
    ) == EXIT_OK
    assert "Coda Pistol" in exported.read_text(encoding="utf-8-sig")


def test_blueprint_cli_unresolved_resolution_is_explicit_and_preview_first(workspace, capsys):
    source_cache = ambiguous_blueprint_cache(workspace)
    data_root = workspace / "player-data"
    log = workspace / "Game.log"
    log.write_text(
        '<2026-03-26T17:15:41.684Z> [Notice] '
        '<SHUDEvent_OnNotification> Added notification '
        '"Received Blueprint: Coda Pistol: " [23] to queue.\n',
        encoding="utf-8",
    )
    common = (
        "--cache", source_cache, "--channel", "LIVE", "--data-root", data_root
    )
    assert run("blueprints", "scan", *common, "--log", log, "--confirm") == EXIT_OK
    state_path = data_root / "channels" / "LIVE" / "ownership.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    acquisition = state["unresolved"][0]["acquisition"]["acquisition_id"]
    selected = "cig:22222222-2222-2222-2222-222222222222"

    assert run("blueprints", "unresolved", *common) == EXIT_OK
    output = capsys.readouterr().out
    assert acquisition[:16] in output and selected in output
    assert run(
        "blueprints", "resolve", *common,
        "--acquisition", acquisition[:8], "--blueprint-id", selected,
    ) == EXIT_REFUSED
    assert run(
        "blueprints", "resolve", *common,
        "--acquisition", acquisition[:8], "--blueprint-id", selected, "--confirm",
    ) == EXIT_OK
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert not state["unresolved"]
    assert state["records"][0]["blueprint_id"] == selected


def test_render_can_write_per_key_provenance_sidecar(workspace):
    chain(workspace)
    contracts = cache.load(workspace / "cache.json")
    contracts.contracts[0].evidence.append(
        Evidence("local-test", "record", "record/path", "$.reward", 100)
    )
    cache.save(contracts, workspace / "cache.json")
    sidecar = workspace / "provenance.json"

    assert run(
        "render",
        "--cache", workspace / "cache.json",
        "--out", workspace / "rendered.json",
        "--provenance-out", sidecar,
    ) == EXIT_OK

    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    assert provenance["Org_x_title"][0]["provider"] == "local-test"
    assert provenance["Org_x_desc"][0]["field_path"] == "$.reward"


def test_render_uses_explicit_source_precedence_and_reports_conflicts(workspace):
    chain(workspace)
    data_root = workspace / "data"
    overlay = workspace / "overlay.ini"
    imported = workspace / "imported.ini"
    overlay.write_text(BOM + "Org_x_title=overlay\n", encoding="utf-8")
    imported.write_text(BOM + "Org_x_title=imported\n", encoding="utf-8")

    assert run(
        "user", "set", "--channel", "LIVE", "--data-root", data_root,
        "--key", "Org_x_title", "--value", "mine", "--confirm",
    ) == EXIT_OK
    assert run(
        "render", "--cache", workspace / "cache.json",
        "--channel", "LIVE", "--data-root", data_root,
        "--language-overlay", overlay, "--import-source", imported,
        "--out", workspace / "merged.json",
        "--sources-out", workspace / "sources.json",
        "--conflicts-out", workspace / "conflicts.json",
    ) == EXIT_OK

    rendered = json.loads((workspace / "merged.json").read_text(encoding="utf-8"))
    sources = json.loads((workspace / "sources.json").read_text(encoding="utf-8"))
    conflicts = json.loads((workspace / "conflicts.json").read_text(encoding="utf-8"))
    assert rendered["Org_x_title"] == "mine"
    assert sources["precedence"] == [
        "stock", "language-overlay", "import", "generated", "user"
    ]
    assert sources["entries"]["Org_x_title"]["winner_kind"] == "user"
    assert "Org_x_title" in conflicts["entries"]


def test_render_can_explicitly_exclude_user_edits(workspace):
    chain(workspace)
    data_root = workspace / "data"
    assert run(
        "user", "set", "--channel", "LIVE", "--data-root", data_root,
        "--key", "Org_x_title", "--value", "mine", "--confirm",
    ) == EXIT_OK
    assert run(
        "render", "--cache", workspace / "cache.json", "--channel", "LIVE",
        "--data-root", data_root, "--no-user-edits",
        "--out", workspace / "without-user.json",
    ) == EXIT_OK
    rendered = json.loads((workspace / "without-user.json").read_text(encoding="utf-8"))
    assert rendered["Org_x_title"] != "mine"


def test_render_reports_and_omits_invalid_user_winner(workspace):
    chain(workspace)
    data_root = workspace / "data"
    assert run(
        "user", "set", "--channel", "LIVE", "--data-root", data_root,
        "--key", "Org_x_title", "--value", "bad <script>", "--confirm",
    ) == EXIT_OK
    assert run(
        "render", "--cache", workspace / "cache.json", "--channel", "LIVE",
        "--data-root", data_root, "--out", workspace / "invalid-user.json",
    ) == EXIT_INVALID
    rendered = json.loads((workspace / "invalid-user.json").read_text(encoding="utf-8"))
    assert "Org_x_title" not in rendered


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


def game_install_archive(tmp_path) -> Path:
    import p4kbuilder as B

    root = tmp_path / "LIVE"
    root.mkdir()
    contents = (
        BOM
        + "Foxwell_Test_title=Do the contract\n"
        + "Foxwell_Test_desc=Go to ~mission(Location|Address) and finish the job.\n"
    )
    (root / "Data.p4k").write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", contents.encode("utf-8"))
        .add(
            "Data/Localization/french/global.ini",
            (BOM + "Foxwell_Test_title=Le contrat\nFoxwell_Test_desc=Description.\n").encode("utf-8"),
        )
        .build()
    )
    return root


def test_c5_channel_language_and_local_pack_workflow(tmp_path, workspace, capsys):
    game = game_install_archive(tmp_path)
    (game / "USER.cfg").write_text("g_language = french\n", encoding="utf-8")
    assert run("channels", "list", "--root", game) == EXIT_OK
    discovered = capsys.readouterr().out
    assert "LIVE" in discovered and "english, french" in discovered
    assert "configured: french" in discovered
    assert run("languages", "list", "--install", game) == EXIT_OK

    pack = tmp_path / "french-pack.ini"
    pack.write_text(BOM + "Local_Only=Texte local\n", encoding="utf-8")
    data_root = tmp_path / "portable-data"
    command = (
        "languages", "import", "--install", game, "--language", "french",
        "--file", pack, "--data-root", data_root,
    )
    assert run(*command) == EXIT_REFUSED
    assert not list(data_root.rglob("language-pack.ini"))
    assert run(*command, "--confirm") == EXIT_OK

    chain(workspace)
    output = tmp_path / "with-pack.json"
    assert run(
        "render", "--cache", workspace / "cache.json", "--channel", "LIVE",
        "--language", "french", "--data-root", data_root, "--out", output,
    ) == EXIT_OK
    assert json.loads(output.read_text(encoding="utf-8"))["Local_Only"] == "Texte local"
    without = tmp_path / "without-pack.json"
    assert run(
        "render", "--cache", workspace / "cache.json", "--channel", "LIVE",
        "--language", "french", "--data-root", data_root, "--no-language-pack",
        "--out", without,
    ) == EXIT_OK
    assert "Local_Only" not in json.loads(without.read_text(encoding="utf-8"))


def test_c5_settings_cli_is_preview_first_and_scope_safe(tmp_path):
    source = tmp_path / "source-data"
    target = tmp_path / "target-data"
    assert run(
        "user", "set", "--channel", "PTU", "--language", "german_(germany)",
        "--data-root", source, "--key", "Mine", "--value", "Wert", "--confirm",
    ) == EXIT_OK
    archive = tmp_path / "settings.zip"
    assert run("settings", "export", "--data-root", source, "--out", archive) == EXIT_REFUSED
    assert not archive.exists()
    assert run(
        "settings", "export", "--data-root", source, "--out", archive, "--confirm"
    ) == EXIT_OK
    assert run(
        "settings", "import", "--data-root", target, "--file", archive
    ) == EXIT_REFUSED
    assert not target.exists()
    assert run(
        "settings", "import", "--data-root", target, "--file", archive, "--confirm"
    ) == EXIT_OK
    assert run(
        "user", "list", "--channel", "PTU", "--language", "german_(germany)",
        "--data-root", target,
    ) == EXIT_OK
    assert not (target / "channels" / "LIVE").exists()


def test_c5_diagnostics_cli_exports_only_redacted_aggregates(tmp_path, capsys):
    game = game_install_archive(tmp_path)
    data_root = tmp_path / "Person Name" / "data"
    secret = "DO_NOT_EXPORT_THIS_VALUE"
    assert run(
        "user", "set", "--channel", "LIVE", "--data-root", data_root,
        "--key", "Private_Key", "--value", secret, "--confirm",
    ) == EXIT_OK
    capsys.readouterr()
    (game / "Game.log").write_text(secret, encoding="utf-8")
    output = tmp_path / "diagnostics.json"

    assert run(
        "diagnostics", "preview", "--install", game, "--data-root", data_root
    ) == EXIT_OK
    preview = capsys.readouterr().out
    assert secret not in preview and "Private_Key" not in preview
    assert str(game) not in preview and "Person Name" not in preview
    assert run(
        "diagnostics", "export", "--install", game, "--data-root", data_root,
        "--out", output,
    ) == EXIT_REFUSED
    assert not output.exists()
    assert run(
        "diagnostics", "export", "--install", game, "--data-root", data_root,
        "--out", output, "--confirm",
    ) == EXIT_OK
    rendered = output.read_text(encoding="utf-8")
    assert secret not in rendered and "Private_Key" not in rendered
    assert str(tmp_path.resolve()) not in rendered


def channel_rendered(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "Foxwell_Test_title": "[Foxwell] Do the contract",
        "Foxwell_Test_desc": "Complete the contract safely.",
    }
    values.update(overrides)
    path = tmp_path / "rendered.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


def authored_fallbacks(tmp_path: Path) -> Path:
    path = tmp_path / "fallbacks.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game_version": None,
                "language": "english",
                "unresolved": [
                    {
                        "source_id": "synthetic-mission",
                        "reason": "localization-missing",
                        "keys": ["Missing_Title", "Missing_Desc"],
                    }
                ],
                "values": {
                    "Missing_Title": "My title",
                    "Missing_Desc": "My description.",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_channel_preview_prepares_clean_install_without_writing(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"

    assert run(
        "channel", "preview", "--install", install,
        "--rendered", channel_rendered(tmp_path),
    ) == EXIT_OK

    output = capsys.readouterr().out
    assert "baseline : archive" in output
    assert "nothing was written" in output
    assert not target.exists()
    assert not (target.parent / "backups").exists()


def test_channel_apply_requires_confirmation_and_leaves_no_artifacts(tmp_path):
    install = game_install_archive(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"

    assert run(
        "channel", "apply", "--install", install,
        "--rendered", channel_rendered(tmp_path),
    ) == EXIT_REFUSED

    assert not target.exists()
    assert not (target.parent / "backups").exists()


def test_channel_apply_and_rollback_complete_clean_install_workflow(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"

    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--confirm",
    ) == EXIT_OK
    applied = LocalizationFile.load(target)
    assert applied.get("Foxwell_Test_title") == "[Foxwell] Do the contract"
    backups = list((target.parent / "backups").glob("global.*.ini"))
    assert len(backups) == 1
    assert LocalizationFile.load(backups[0]).get("Foxwell_Test_title") == "Do the contract"

    assert run("channel", "rollback", "--install", install) == EXIT_REFUSED
    assert LocalizationFile.load(target).get("Foxwell_Test_title") == "[Foxwell] Do the contract"

    assert run(
        "channel", "rollback", "--install", install, "--confirm"
    ) == EXIT_OK
    restored = LocalizationFile.load(target)
    assert restored.get("Foxwell_Test_title") == "Do the contract"
    assert "rollback : complete" in capsys.readouterr().out


def test_custom_backup_root_is_partitioned_by_channel_and_language(tmp_path):
    live = game_install_archive(tmp_path)
    ptu_parent = tmp_path / "ptu-install"
    ptu_parent.mkdir()
    ptu = game_install_archive(ptu_parent)
    renamed_ptu = ptu.with_name("PTU")
    ptu.rename(renamed_ptu)
    backups = tmp_path / "portable-backups"
    rendered = channel_rendered(tmp_path)

    assert run(
        "channel", "apply", "--install", live, "--language", "english",
        "--rendered", rendered, "--backup-dir", backups, "--confirm",
    ) == EXIT_OK
    assert run(
        "channel", "apply", "--install", live, "--language", "french",
        "--rendered", rendered, "--backup-dir", backups, "--confirm",
    ) == EXIT_OK
    assert run(
        "channel", "apply", "--install", renamed_ptu, "--language", "english",
        "--rendered", rendered, "--backup-dir", backups, "--confirm",
    ) == EXIT_OK

    assert (backups / "LIVE" / "english").is_dir()
    assert (backups / "LIVE" / "french").is_dir()
    assert (backups / "PTU" / "english").is_dir()
    assert run(
        "channel", "rollback", "--install", live, "--language", "english",
        "--backup-dir", backups, "--list",
    ) == EXIT_OK


def test_channel_plan_file_unifies_sources_diff_and_confirmed_apply(tmp_path):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    sources = tmp_path / "sources.json"
    sources.write_text(
        json.dumps(
            {
                "precedence": ["stock", "generated", "user"],
                "entries": {
                    "Foxwell_Test_title": {
                        "winner": "user:LIVE:english",
                        "winner_kind": "user",
                        "conflicted": True,
                        "contributions": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    reviewed = tmp_path / "reviewed-plan.json"

    assert run(
        "channel", "preview", "--install", install, "--rendered", rendered,
        "--sources", sources, "--plan-out", reviewed,
    ) == EXIT_OK
    data = json.loads(reviewed.read_text(encoding="utf-8"))
    assert data["plan_id"]
    assert data["target_fingerprint"]["exists"] is False
    assert data["outcomes"]["change"] == [
        "Foxwell_Test_desc", "Foxwell_Test_title"
    ]
    assert data["sources"]["Foxwell_Test_title"]["winner_kind"] == "user"

    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--sources", sources, "--expect-plan", reviewed, "--confirm",
    ) == EXIT_OK
    assert LocalizationFile.load(
        install / "data" / "Localization" / "english" / "global.ini"
    ).get("Foxwell_Test_title") == "[Foxwell] Do the contract"


def test_expected_plan_refuses_external_override_change(tmp_path):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    reviewed = tmp_path / "reviewed-plan.json"
    assert run(
        "channel", "preview", "--install", install, "--rendered", rendered,
        "--plan-out", reviewed,
    ) == EXIT_OK
    target = install / "data" / "Localization" / "english" / "global.ini"
    target.parent.mkdir(parents=True)
    target.write_text(
        BOM + "Foxwell_Test_title=external\nFoxwell_Test_desc=external body\n",
        encoding="utf-8",
    )

    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--expect-plan", reviewed, "--confirm",
    ) == EXIT_INVALID
    assert LocalizationFile.load(target).get("Foxwell_Test_title") == "external"


def test_channel_diagnostics_reports_verified_apply_and_rollback(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--confirm",
    ) == EXIT_OK
    assert run("channel", "diagnostics", "--install", install) == EXIT_OK
    apply_output = capsys.readouterr().out
    assert "last op   : apply / complete" in apply_output
    assert "recovery  : clean" in apply_output

    assert run("channel", "rollback", "--install", install, "--confirm") == EXIT_OK
    assert run("channel", "diagnostics", "--install", install) == EXIT_OK
    rollback_output = capsys.readouterr().out
    assert "last op   : rollback / complete" in rollback_output
    assert "last result:" in rollback_output


def test_channel_adds_only_keys_authorized_by_user_fallback_file(tmp_path):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(
        tmp_path,
        Missing_Title="[User] My title",
        Missing_Desc="My description.\\n\\nReward details.",
        Unknown_Key="must remain skipped",
    )
    target = install / "data" / "Localization" / "english" / "global.ini"

    assert run(
        "channel", "apply",
        "--install", install,
        "--rendered", rendered,
        "--fallbacks", authored_fallbacks(tmp_path),
        "--confirm",
    ) == EXIT_OK

    applied = LocalizationFile.load(target)
    assert applied.get("Missing_Title") == "[User] My title"
    assert applied.get("Missing_Desc") == "My description.\\n\\nReward details."
    assert applied.get("Unknown_Key") is None

    assert run("channel", "rollback", "--install", install, "--confirm") == EXIT_OK
    restored = LocalizationFile.load(target)
    assert restored.get("Missing_Title") is None


def test_channel_applies_when_plan_contains_only_fallback_additions(tmp_path):
    install = game_install_archive(tmp_path)
    rendered = tmp_path / "fallback-only.json"
    rendered.write_text(
        json.dumps(
            {
                "Missing_Title": "[User] My title",
                "Missing_Desc": "My description.",
            }
        ),
        encoding="utf-8",
    )

    assert run(
        "channel", "apply",
        "--install", install,
        "--rendered", rendered,
        "--fallbacks", authored_fallbacks(tmp_path),
        "--confirm",
    ) == EXIT_OK

    target = install / "data" / "Localization" / "english" / "global.ini"
    assert LocalizationFile.load(target).get("Missing_Title") == "[User] My title"


def test_fallback_additions_survive_omission_and_require_authorized_remove(tmp_path):
    install = game_install_archive(tmp_path)
    fallback = authored_fallbacks(tmp_path)
    added_render = channel_rendered(
        tmp_path,
        Missing_Title="My title",
        Missing_Desc="My description.",
    )
    assert run(
        "channel", "apply", "--install", install, "--rendered", added_render,
        "--fallbacks", fallback, "--confirm",
    ) == EXIT_OK
    target = install / "data" / "Localization" / "english" / "global.ini"

    ordinary_render = channel_rendered(tmp_path)
    assert run(
        "channel", "apply", "--install", install, "--rendered", ordinary_render,
        "--confirm",
    ) == EXIT_OK
    assert LocalizationFile.load(target).get("Missing_Title") == "My title"

    assert run(
        "channel", "apply", "--install", install, "--rendered", ordinary_render,
        "--remove-key", "Missing_Title", "--remove-key", "Missing_Desc",
        "--confirm",
    ) == EXIT_INVALID
    assert LocalizationFile.load(target).get("Missing_Title") == "My title"

    assert run(
        "channel", "apply", "--install", install, "--rendered", ordinary_render,
        "--fallbacks", fallback,
        "--remove-key", "Missing_Title", "--remove-key", "Missing_Desc",
        "--confirm",
    ) == EXIT_OK
    assert LocalizationFile.load(target).get("Missing_Title") is None


def test_channel_preview_uses_existing_override_in_merge_mode(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"
    target.parent.mkdir(parents=True)
    target.write_text(
        BOM + "Foxwell_Test_title=Existing override\nFoxwell_Test_desc=Existing body\n",
        encoding="utf-8",
    )
    before = target.read_bytes()

    assert run(
        "channel", "preview", "--install", install,
        "--rendered", channel_rendered(tmp_path),
    ) == EXIT_OK

    assert "baseline : override" in capsys.readouterr().out
    assert target.read_bytes() == before


def test_channel_workflow_overlays_and_persists_user_ini_before_apply(tmp_path):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    data_root = tmp_path / "user-data"
    assert run(
        "user", "set", "--channel", "LIVE", "--data-root", data_root,
        "--key", "Foxwell_Test_title", "--value", "Personal title", "--confirm",
    ) == EXIT_OK

    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--data-root", data_root, "--confirm",
    ) == EXIT_OK
    target = install / "data" / "Localization" / "english" / "global.ini"
    assert LocalizationFile.load(target).get("Foxwell_Test_title") == "Personal title"
    assert next(data_root.rglob("user.ini")).is_file()


def test_channel_overwrite_previews_pristine_archive_not_override(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"
    target.parent.mkdir(parents=True)
    target.write_text(
        BOM + "Foxwell_Test_title=Existing override\nFoxwell_Test_desc=Existing body\n",
        encoding="utf-8",
    )

    assert run(
        "channel", "preview", "--install", install,
        "--rendered", channel_rendered(tmp_path), "--mode", "overwrite",
    ) == EXIT_OK

    assert "baseline : archive" in capsys.readouterr().out


def test_channel_invalid_plan_never_writes_even_when_confirmed(tmp_path):
    install = game_install_archive(tmp_path)
    target = install / "data" / "Localization" / "english" / "global.ini"

    assert run(
        "channel", "apply", "--install", install,
        "--rendered", channel_rendered(tmp_path, Foxwell_Test_title="bad\nvalue"),
        "--confirm",
    ) == EXIT_INVALID

    assert not target.exists()
    assert not (target.parent / "backups").exists()


def test_channel_rollback_lists_and_scopes_backups(tmp_path, capsys):
    install = game_install_archive(tmp_path)
    rendered = channel_rendered(tmp_path)
    assert run(
        "channel", "apply", "--install", install, "--rendered", rendered,
        "--confirm",
    ) == EXIT_OK

    assert run("channel", "rollback", "--install", install, "--list") == EXIT_OK
    assert "global." in capsys.readouterr().out

    outside = tmp_path / "outside.ini"
    outside.write_text(BOM + "Foxwell_Test_title=outside\n", encoding="utf-8")
    assert run(
        "channel", "rollback", "--install", install,
        "--backup", outside, "--confirm",
    ) == EXIT_ERROR


def test_channel_reports_missing_install_without_traceback(tmp_path, capsys):
    assert run(
        "channel", "preview", "--install", tmp_path / "missing",
        "--rendered", channel_rendered(tmp_path),
    ) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


# --- persistent user.ini ----------------------------------------------------


def test_user_commands_are_gated_and_undoable(tmp_path):
    data_root = tmp_path / "data"
    scope = ("--channel", "PTU", "--language", "german_(germany)",
             "--data-root", data_root)

    assert run("user", "set", *scope, "--key", "Mission_Title",
               "--value", "mine") == EXIT_REFUSED
    assert not list(data_root.rglob("user.ini"))

    assert run("user", "set", *scope, "--key", "Mission_Title",
               "--value", "mine", "--confirm") == EXIT_OK
    user_ini = next(data_root.rglob("user.ini"))
    assert LocalizationFile.load(user_ini).get("Mission_Title") == "mine"

    assert run("user", "undo", *scope, "--confirm") == EXIT_OK
    assert LocalizationFile.load(user_ini).get("Mission_Title") is None
    assert run("user", "redo", *scope, "--confirm") == EXIT_OK
    assert LocalizationFile.load(user_ini).get("Mission_Title") == "mine"


def test_user_import_requires_explicit_conflict_choice_and_can_undo(tmp_path):
    data_root = tmp_path / "data"
    incoming = tmp_path / "incoming.ini"
    incoming.write_text(BOM + "Conflict=new\nAdded=value\n", encoding="utf-8")
    scope = ("--channel", "LIVE", "--data-root", data_root)

    assert run("user", "set", *scope, "--key", "Conflict", "--value", "old",
               "--confirm") == EXIT_OK
    assert run("user", "import", *scope, "--file", incoming,
               "--confirm") == EXIT_INVALID
    assert run("user", "import", *scope, "--file", incoming,
               "--on-conflict", "incoming") == EXIT_REFUSED
    assert run("user", "import", *scope, "--file", incoming,
               "--on-conflict", "incoming", "--confirm") == EXIT_OK

    exported = tmp_path / "exported.ini"
    assert run("user", "export", *scope, "--out", exported,
               "--confirm") == EXIT_OK
    values = {entry.key: entry.value for entry in LocalizationFile.load(exported).entries()}
    assert values == {"Added": "value", "Conflict": "new"}
    assert run("user", "undo", *scope, "--confirm") == EXIT_OK
    user_ini = next(data_root.rglob("user.ini"))
    assert {entry.key: entry.value for entry in LocalizationFile.load(user_ini).entries()} == {
        "Conflict": "old"
    }


def test_import_from_install_uses_archive_operation(tmp_path, capsys):
    output = tmp_path / "game-cache.json"

    assert run(
        "import", "--install", game_install_archive(tmp_path), "--out", output
    ) == EXIT_OK

    assert output.is_file()
    assert "imported 1 contracts" in capsys.readouterr().out


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


def test_failed_streaming_stock_export_preserves_existing_output(tmp_path):
    bad = tmp_path / "bad.p4k"
    bad.write_bytes(b"not an archive at all")
    output = tmp_path / "out.ini"
    output.write_bytes(b"keep this")

    assert run("stock", "--archive", bad, "--out", output) != EXIT_OK

    assert output.read_bytes() == b"keep this"
    assert list(tmp_path.glob(".out.ini.*.tmp")) == []


def test_stock_surfaces_cig_method_100_integrity_warning(tmp_path, capsys):
    import struct
    import p4kbuilder as B

    archive = tmp_path / "Data.p4k"
    archive.write_bytes(
        B.Builder()
        .add(
            "Data/Localization/english/global.ini",
            STOCK.encode("utf-8"),
            method=B.METHOD_ZSTD,
            cig_aligned=True,
        )
        .build()
    )
    raw = bytearray(archive.read_bytes())
    directory = raw.rfind(struct.pack("<I", B.CENTRAL_DIR_SIGNATURE))
    struct.pack_into("<I", raw, directory + 16, 0x12345678)
    struct.pack_into("<I", raw, 14, 0x12345678)
    archive.write_bytes(raw)

    assert run("stock", "--archive", archive, "--out", tmp_path / "stock.ini") == EXIT_OK
    assert "non-ZIP CRC" in capsys.readouterr().err


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
