import json
import zipfile
from pathlib import Path

import pytest

from starcompanion.config import Profile
from starcompanion.sharing import (
    DeltaPackError,
    load_delta_pack,
    plan_delta_pack,
    write_delta_pack,
)
from starcompanion.user_edits import EditCommand, EditSession, UserEditStore


def authored_store(tmp_path: Path) -> UserEditStore:
    store = UserEditStore("LIVE", "english", root=tmp_path / "data")
    session = EditSession(store)
    session.execute(EditCommand.set(session.values, "Authored", "my wording"))
    session.execute(
        EditCommand.set(session.values, "Imported", "third-party wording"),
        origin="imported",
    )
    return store


def rewrite_pack(path: Path, mutate) -> None:
    with zipfile.ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    mutate(members)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)


def test_delta_pack_contains_only_digest_bound_authored_values(tmp_path):
    store = authored_store(tmp_path)
    plan = plan_delta_pack(store, Profile(name="shareable"))
    assert plan.authored_values == {"Authored": "my wording"}
    assert plan.excluded_values == 1

    destination = tmp_path / "share.zip"
    write_delta_pack(plan, destination)
    loaded = load_delta_pack(destination)

    assert loaded.source_channel == "LIVE"
    assert loaded.language == "english"
    assert loaded.values == {"Authored": "my wording"}
    assert loaded.profile.name == "shareable"
    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "profile.json",
            "README.txt",
            "user-deltas.ini",
        }
        assert b"third-party wording" not in archive.read("user-deltas.ini")
        assert b"own installed Data.p4k" in archive.read("README.txt")


def test_delta_pack_fails_closed_without_verified_authorship(tmp_path):
    store = UserEditStore("LIVE", root=tmp_path / "data")
    store.save({"Legacy": "unknown provenance"})
    with pytest.raises(DeltaPackError, match="verified user-authored"):
        plan_delta_pack(store, Profile())

    session = EditSession(store)
    session.execute(EditCommand.set(session.values, "Legacy", "now authored"))
    store.origins_path.write_text('{"origins":{},"origins":{}}', encoding="utf-8")
    with pytest.raises(DeltaPackError, match="verified user-authored"):
        plan_delta_pack(store, Profile())


def test_delta_pack_preview_rejects_value_origin_and_plan_mutation(tmp_path):
    store = authored_store(tmp_path)
    value_plan = plan_delta_pack(store, Profile())
    session = EditSession(store)
    session.execute(EditCommand.set(session.values, "Authored", "changed"))
    with pytest.raises(DeltaPackError, match="changed after preview"):
        write_delta_pack(value_plan, tmp_path / "value.zip")

    stable_plan = plan_delta_pack(store, Profile())
    stable_plan.authored_values["Authored"] = "mutated in memory"
    with pytest.raises(DeltaPackError, match="selection changed"):
        write_delta_pack(stable_plan, tmp_path / "plan.zip")


def test_delta_pack_never_overwrites_without_explicit_opt_in(tmp_path):
    plan = plan_delta_pack(authored_store(tmp_path), Profile())
    destination = tmp_path / "share.zip"
    write_delta_pack(plan, destination)
    before = destination.read_bytes()
    with pytest.raises(DeltaPackError, match="refusing to overwrite"):
        write_delta_pack(plan, destination)
    assert destination.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("source_channel", "../LIVE", "unsupported game channel"),
        ("language", "../english", "invalid localization language"),
        ("schema", True, "unsupported delta-pack manifest"),
    ],
)
def test_delta_pack_rejects_hostile_or_noncanonical_manifest_scope(
    tmp_path, field, value, match
):
    path = tmp_path / "share.zip"
    write_delta_pack(plan_delta_pack(authored_store(tmp_path), Profile()), path)

    def mutate(members):
        manifest = json.loads(members["manifest.json"])
        manifest[field] = value
        members["manifest.json"] = (json.dumps(manifest) + "\n").encode()

    rewrite_pack(path, mutate)
    with pytest.raises(DeltaPackError, match=match):
        load_delta_pack(path)


@pytest.mark.parametrize(
    "payload",
    [
        b"Authored=value\n",
        b"\xef\xbb\xbfAuthored=value\r\n",
        b"\xef\xbb\xbfbroken-line\nAuthored=value\n",
        b"\xef\xbb\xbfZulu=z\nAlpha=a\n",
    ],
)
def test_delta_pack_rejects_noncanonical_or_malformed_ini(tmp_path, payload):
    path = tmp_path / "share.zip"
    write_delta_pack(plan_delta_pack(authored_store(tmp_path), Profile()), path)

    def mutate(members):
        members["user-deltas.ini"] = payload
        manifest = json.loads(members["manifest.json"])
        import hashlib

        record = next(
            item for item in manifest["files"] if item["path"] == "user-deltas.ini"
        )
        record["size"] = len(payload)
        record["sha256"] = hashlib.sha256(payload).hexdigest()
        members["manifest.json"] = (json.dumps(manifest) + "\n").encode()

    rewrite_pack(path, mutate)
    with pytest.raises(DeltaPackError):
        load_delta_pack(path)


def test_delta_pack_rejects_duplicate_and_extra_zip_members(tmp_path):
    path = tmp_path / "share.zip"
    write_delta_pack(plan_delta_pack(authored_store(tmp_path), Profile()), path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("../outside", b"hostile")
    with pytest.raises(DeltaPackError, match="exactly the declared files"):
        load_delta_pack(path)
