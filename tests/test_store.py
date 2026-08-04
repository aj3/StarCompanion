import os

from starcompanion import store
from starcompanion.install import GameInstall
from starcompanion.model import ContractSet


def make_install(tmp_path, name="LIVE", *, version=None, contents=b"one"):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "Data.p4k").write_bytes(contents)
    return GameInstall(root=root, channel="LIVE", version=version)


def test_unversioned_cache_is_fingerprinted_not_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    install = make_install(tmp_path, "game")

    path = store.cache_path(install)

    assert path is not None
    assert "no-manifest" in path.name
    assert "unknown" not in path.name


def test_archive_change_invalidates_cache_path(tmp_path, monkeypatch):
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    install = make_install(tmp_path, "game", version="4.2")
    before = store.cache_path(install)

    install.archive.write_bytes(b"a different archive size")
    os.utime(install.archive, None)

    assert store.cache_path(install) != before


def test_cache_is_scoped_by_install_and_language(tmp_path, monkeypatch):
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    first = make_install(tmp_path, "first", version="4.2")
    second = make_install(tmp_path, "second", version="4.2")

    assert store.cache_path(first) != store.cache_path(second)
    assert store.cache_path(first, "english") != store.cache_path(first, "french")
    assert store.cache_path(first, "French") == store.cache_path(first, "french")
    import pytest
    with pytest.raises(ValueError, match="invalid localization language"):
        store.cache_path(first, "../french")


def test_missing_archive_is_not_cacheable(tmp_path, monkeypatch):
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    install = GameInstall(root=tmp_path / "missing", channel="LIVE")

    assert store.cache_path(install) is None
    assert store.load(install) is None
    assert store.save(install, ContractSet()) is None


def test_save_and_load_exact_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("STARCOMPANION_CACHE", str(tmp_path / "cache"))
    install = make_install(tmp_path, "game", version="4.2")
    contracts = ContractSet()

    saved = store.save(install, contracts)

    assert saved is not None and saved.is_file()
    assert store.load(install) == contracts
