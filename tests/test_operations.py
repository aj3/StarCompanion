import time
import tempfile
import struct
from pathlib import Path
from threading import Event

import p4kbuilder as B
import pytest

from starcompanion.inject import MergeMode
from starcompanion.ini import BOM, LocalizationFile
from starcompanion.install import GameInstall
from starcompanion.operations import prepare_update, read_contracts
from starcompanion.tasks import CancellationToken, OperationCancelled, OperationStage


STOCK = (
    "\ufeff"
    "Foxwell_Test_title=Do the contract\n"
    "Foxwell_Test_desc=Go to ~mission(Location|Address) and complete the "
    "contract for Foxwell. This description is deliberately long enough to "
    "be recognized as a mission rather than an unrelated interface label.\n"
    "Other=untouched\n"
)


def make_install(tmp_path: Path, *, extra_entries: int = 0) -> GameInstall:
    root = tmp_path / "LIVE"
    root.mkdir()
    builder = B.Builder().add(
        "Data/Localization/english/global.ini",
        STOCK.encode("utf-8"),
    )
    for index in range(extra_entries):
        builder.add(f"Data/filler/{index:05}.bin", b"x")
    (root / "Data.p4k").write_bytes(builder.build())
    return GameInstall(root=root, channel="LIVE", version="test")


def keep_helper_workspaces_under(tmp_path: Path, monkeypatch) -> None:
    import starcompanion.helper_artifacts as helper_artifacts

    original_mkdtemp = tempfile.mkdtemp

    def local_mkdtemp(**kwargs):
        return original_mkdtemp(dir=tmp_path, **kwargs)

    monkeypatch.setattr(helper_artifacts.tempfile, "mkdtemp", local_mkdtemp)


def test_read_contracts_reports_ordered_stages(tmp_path, monkeypatch):
    keep_helper_workspaces_under(tmp_path, monkeypatch)
    events = []

    contracts = read_contracts(make_install(tmp_path), reporter=events.append)

    stages = [event.stage for event in events]
    assert len(contracts.contracts) == 1
    assert stages[0] is OperationStage.OPEN_ARCHIVE
    assert OperationStage.INDEX_ARCHIVE in stages
    assert OperationStage.PARSE_CONTRACTS in stages
    assert stages[-1] is OperationStage.COMPLETE
    assert events[-1].fraction == 1
    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_cancellation_interrupts_central_directory_index(tmp_path):
    install = make_install(tmp_path, extra_entries=5000)
    token = CancellationToken()

    def cancel_during_index(event):
        if event.stage is OperationStage.INDEX_ARCHIVE and (event.current or 0) > 0:
            token.cancel()

    started = time.monotonic()
    with pytest.raises(OperationCancelled):
        read_contracts(install, token=token, reporter=cancel_during_index)
    assert time.monotonic() - started < 3

    # Constructor failure must still close the archive handle on Windows.
    with install.archive.open("ab") as stream:
        stream.write(b"")


def test_cancellation_interrupts_a_large_entry_read(tmp_path):
    root = tmp_path / "large" / "LIVE"
    root.mkdir(parents=True)
    payload = STOCK.encode("utf-8") + (b"x" * (3 * 1024 * 1024))
    (root / "Data.p4k").write_bytes(
        B.Builder()
        .add("Data/Localization/english/global.ini", payload)
        .build()
    )
    install = GameInstall(root=root, channel="LIVE")
    token = CancellationToken()
    byte_events = []

    def cancel_after_first_chunk(event):
        if (
            event.stage is OperationStage.READ_LOCALIZATION
            and event.current is not None
            and 0 < event.current < 450
        ):
            byte_events.append(event)
            token.cancel()

    with pytest.raises(OperationCancelled):
        read_contracts(install, token=token, reporter=cancel_after_first_chunk)

    assert byte_events
    assert not install.localization().exists()


def test_cancelled_prepare_never_creates_an_override(tmp_path):
    install = make_install(tmp_path)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(OperationCancelled):
        prepare_update(
            install,
            {"Foxwell_Test_title": "changed"},
            mode=MergeMode.MERGE,
            token=token,
        )

    assert not install.localization().exists()


def test_prepare_update_returns_preview_without_writing(tmp_path, monkeypatch):
    keep_helper_workspaces_under(tmp_path, monkeypatch)
    install = make_install(tmp_path)

    prepared = prepare_update(
        install,
        {"Foxwell_Test_title": "changed"},
    )

    assert prepared.plan.updated == ["Foxwell_Test_title"]
    assert prepared.plan.plan_id
    assert prepared.plan.target_fingerprint is not None
    assert not prepared.plan.target_fingerprint.exists
    assert prepared.plan.baseline_sha256
    assert prepared.plan.desired_sha256
    assert not install.localization().exists()
    assert prepared.localization.baseline_path.is_file()
    assert prepared.localization.baseline_path.name == "baseline.ini"
    assert not (prepared.localization.baseline_path.parent / "result.jsonl").exists()
    prepared.localization.cleanup()
    assert not prepared.localization.baseline_path.exists()
    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_isolated_prepare_preserves_cig_integrity_warnings(tmp_path):
    root = tmp_path / "warning" / "LIVE"
    root.mkdir(parents=True)
    archive = root / "Data.p4k"
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

    with prepare_update(
        GameInstall(root, "LIVE"), {"Foxwell_Test_title": "Changed"}
    ) as prepared:
        assert prepared.localization.integrity_warnings
        assert "non-ZIP CRC" in prepared.localization.integrity_warnings[0]


def test_overwrite_plan_reports_keys_discarded_from_existing_override(tmp_path):
    install = make_install(tmp_path)
    target = install.localization()
    target.parent.mkdir(parents=True)
    target.write_text(
        BOM
        + "Foxwell_Test_title=Prior pack\n"
        + "Foxwell_Test_desc=Prior body\n"
        + "Prior_Only=remove me\n",
        encoding="utf-8",
    )
    with prepare_update(
        install,
        {"Foxwell_Test_title": "Generated title"},
        mode=MergeMode.OVERWRITE,
    ) as prepared:
        assert "Prior_Only" in prepared.plan.removed
        assert "Foxwell_Test_title" in prepared.plan.updated
        prepared.commit(confirmed=True)

    written = LocalizationFile.load(target)
    assert written.get("Prior_Only") is None
    assert written.get("Foxwell_Test_title") == "Generated title"


def test_source_winner_metadata_round_trips_through_prepare_helper(tmp_path):
    report = {
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
    with prepare_update(
        make_install(tmp_path),
        {"Foxwell_Test_title": "Mine"},
        source_report=report,
    ) as prepared:
        assert prepared.plan.source_precedence[-1] == "user"
        assert prepared.plan.sources["Foxwell_Test_title"] == {
            "winner": "user:LIVE:english",
            "winner_kind": "user",
            "conflicted": True,
        }


def test_prepared_update_context_releases_transferred_workspace(tmp_path, monkeypatch):
    keep_helper_workspaces_under(tmp_path, monkeypatch)

    with prepare_update(
        make_install(tmp_path), {"Foxwell_Test_title": "changed"}
    ) as prepared:
        baseline = prepared.localization.baseline_path
        assert baseline.is_file()

    assert not baseline.exists()
    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_zero_grace_forcibly_terminates_helper_within_bound(tmp_path):
    install = make_install(tmp_path, extra_entries=5000)
    token = CancellationToken()

    def cancel_immediately(event):
        token.cancel()

    started = time.monotonic()
    with pytest.raises(OperationCancelled, match="terminated|cancelled"):
        read_contracts(
            install,
            token=token,
            reporter=cancel_immediately,
            cancel_grace_seconds=0.0,
        )

    assert time.monotonic() - started < 2
    with install.archive.open("ab") as stream:
        stream.write(b"")


def test_forced_prepare_cancellation_removes_parent_owned_baseline(tmp_path, monkeypatch):
    keep_helper_workspaces_under(tmp_path, monkeypatch)
    install = make_install(tmp_path, extra_entries=5000)
    token = CancellationToken()

    def cancel_after_start(event):
        token.cancel()

    with pytest.raises(OperationCancelled, match="terminated|cancelled"):
        prepare_update(
            install,
            {"Foxwell_Test_title": "changed"},
            token=token,
            reporter=cancel_after_start,
            cancel_grace_seconds=0.0,
        )

    assert list(tmp_path.glob("starcompanion-helper-*")) == []
    assert not install.localization().exists()


def test_helper_error_removes_every_declared_artifact(tmp_path, monkeypatch):
    from starcompanion.helper_process import HelperOperationError

    keep_helper_workspaces_under(tmp_path, monkeypatch)
    install = make_install(tmp_path)

    with pytest.raises(HelperOperationError, match="klingon"):
        read_contracts(install, language="klingon")

    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_parent_setup_failure_removes_staged_artifacts(tmp_path, monkeypatch):
    import starcompanion.helper_process as helper_process

    keep_helper_workspaces_under(tmp_path, monkeypatch)

    def fail_staging(*_args, **_kwargs):
        raise OSError("synthetic staging failure")

    monkeypatch.setattr(helper_process, "_stage_request", fail_staging)
    with pytest.raises(OSError, match="staging failure"):
        read_contracts(make_install(tmp_path))

    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_parent_decode_failure_removes_child_artifacts(tmp_path, monkeypatch):
    import starcompanion.helper_process as helper_process

    keep_helper_workspaces_under(tmp_path, monkeypatch)

    def fail_decode(*_args, **_kwargs):
        raise ValueError("synthetic decode failure")

    monkeypatch.setattr(helper_process, "_load_result", fail_decode)
    with pytest.raises(ValueError, match="decode failure"):
        read_contracts(make_install(tmp_path))

    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_unexpected_child_exit_removes_every_artifact(tmp_path, monkeypatch):
    import starcompanion.helper_process as helper_process

    keep_helper_workspaces_under(tmp_path, monkeypatch)

    class Endpoint:
        def close(self):
            pass

        def poll(self, *_args):
            return False

    class DeadProcess:
        exitcode = 23

        def start(self):
            pass

        def is_alive(self):
            return False

    class Context:
        def Pipe(self, **_kwargs):
            return Endpoint(), Endpoint()

        def Event(self):
            return Event()

        def Process(self, **_kwargs):
            return DeadProcess()

    monkeypatch.setattr(helper_process.multiprocessing, "get_context", lambda *_: Context())

    with pytest.raises(helper_process.HelperOperationError, match="code 23"):
        read_contracts(make_install(tmp_path))

    assert list(tmp_path.glob("starcompanion-helper-*")) == []


def test_prepared_plan_issues_round_trip_through_typed_artifact(tmp_path):
    prepared = prepare_update(
        make_install(tmp_path),
        {"Foxwell_Test_title": "<script>broken</script>"},
    )
    try:
        assert prepared.plan.errors
        key, issue = prepared.plan.errors[0]
        assert key == "Foxwell_Test_title"
        assert issue.code == "unknown-tag"
        assert issue.offset == 0
    finally:
        prepared.localization.cleanup()


def test_isolated_read_does_not_use_parent_local_implementation(tmp_path, monkeypatch):
    import starcompanion.operations as operations

    install = make_install(tmp_path)
    monkeypatch.setattr(
        operations,
        "_read_contracts_local",
        lambda *args, **kwargs: pytest.fail("ran extraction in parent process"),
    )

    assert len(operations.read_contracts(install).contracts) == 1
