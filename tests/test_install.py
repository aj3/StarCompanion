from pathlib import Path

import pytest

from starcompanion import install


def make_install(root: Path, channel: str = "LIVE", version: str | None = None) -> Path:
    game = root / "Roberts Space Industries" / "StarCitizen" / channel
    (game / "data" / "Localization" / "english").mkdir(parents=True)
    (game / install.ARCHIVE_NAME).write_bytes(b"not really an archive")
    if version:
        (game / "build_manifest.id").write_text(
            '{"Data": {"Branch": "sc-alpha-4.9.0", "Version": "%s"}}' % version
        )
    return game


def test_identifies_a_folder_containing_the_archive(tmp_path):
    game = make_install(tmp_path)
    found = install.identify(game)

    assert found is not None
    assert found.root == game
    assert found.channel == "LIVE"


def test_identifies_from_a_folder_inside_the_install(tmp_path):
    """Someone browsing for their game often lands on a subfolder."""
    game = make_install(tmp_path)
    found = install.identify(game / "data" / "Localization" / "english")

    assert found is not None and found.root == game


def test_unrelated_folder_is_not_an_install(tmp_path):
    assert install.identify(tmp_path) is None


def test_reads_the_build_version(tmp_path):
    game = make_install(tmp_path, version="4.9.188.23497")
    assert install.identify(game).version == "4.9.188.23497"


def test_missing_manifest_leaves_version_unknown(tmp_path):
    game = make_install(tmp_path)
    assert install.identify(game).version is None


def test_corrupt_manifest_does_not_raise(tmp_path):
    game = make_install(tmp_path)
    (game / "build_manifest.id").write_text("{not json")
    assert install.identify(game).version is None


def test_derives_the_file_to_modify(tmp_path):
    game = make_install(tmp_path)
    found = install.identify(game)

    assert found.localization() == game / "data" / "Localization" / "english" / "global.ini"
    assert found.localization("german_(germany)").parent.name == "german_(germany)"

    with pytest.raises(ValueError, match="invalid localization language"):
        found.localization("../../LIVE")


def test_unknown_channel_folder_is_not_accepted_as_scoped_install(tmp_path):
    game = tmp_path / "StarCitizen" / "PRIVATE"
    game.mkdir(parents=True)
    (game / install.ARCHIVE_NAME).write_bytes(b"archive")

    assert install.identify(game) is None
    with pytest.raises(ValueError, match="unsupported game channel"):
        install.GameInstall(game, "PRIVATE")


def test_detects_an_existing_override(tmp_path):
    game = make_install(tmp_path)
    found = install.identify(game)
    assert not found.has_override

    found.localization().write_text("x")
    assert found.has_override


def test_detects_whether_the_language_setting_is_present(tmp_path):
    """Without g_language the override is ignored, so this is worth warning about."""
    game = make_install(tmp_path)
    found = install.identify(game)
    assert not found.language_configured

    found.user_cfg.write_text("g_language = english\n")
    assert found.language_configured


def test_label_is_human_readable(tmp_path):
    game = make_install(tmp_path, version="4.9.1")
    label = install.identify(game).label
    assert "LIVE" in label and "4.9.1" in label


def test_finds_installs_under_a_supplied_root(tmp_path):
    make_install(tmp_path, "LIVE")
    make_install(tmp_path, "PTU")

    base = tmp_path / "Roberts Space Industries" / "StarCitizen"
    found = install.find_installs(roots=[base])

    assert [i.channel for i in found] == ["LIVE", "PTU"], "LIVE must come first"


def test_finds_a_direct_channel_root(tmp_path):
    game = make_install(tmp_path, "EPTU")
    assert install.find_installs(roots=[game])[0].channel == "EPTU"


def test_no_installs_found_is_not_an_error(tmp_path):
    assert install.find_installs(roots=[tmp_path / "nothing"]) == []


def test_install_discovery_has_bounded_cancellation_checkpoints(tmp_path):
    calls = []

    def checkpoint():
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        install.find_installs(
            roots=[tmp_path / "one", tmp_path / "two"],
            checkpoint=checkpoint,
        )
    assert len(calls) == 2


def test_unreadable_directories_are_skipped(tmp_path):
    """Scanning drives must not raise on folders we cannot read."""
    assert install.find_installs(roots=[Path("//nonexistent-host/share")]) == []


@pytest.mark.skipif(
    install.find_default() is None, reason="no Star Citizen install on this machine"
)
def test_real_install_is_found():
    found = install.find_default()
    assert found.archive.is_file()
    assert found.localization().name == "global.ini"
