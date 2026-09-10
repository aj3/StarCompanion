import copy
import json
import multiprocessing
import os
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from starcompanion.blueprints import BlueprintCatalog, build_catalog
from starcompanion.model import BlueprintPool, Contract, ContractSet, Org, Reward
from starcompanion.ownership import (
    Acquisition,
    OwnershipError,
    OwnershipDecision,
    OwnershipConflictError,
    OwnershipRecoveryAvailable,
    OwnershipRecord,
    OwnershipState,
    OwnershipStore,
    ScanCancelled,
    ScanDiagnostic,
    UnresolvedAcquisition,
    apply_import,
    apply_manual_ownership,
    apply_resolution,
    discover_log_files,
    discover_logs,
    export_csv,
    export_json,
    ownership_path,
    ownership_scope,
    plan_import,
    plan_manual_ownership,
    plan_resolution,
    scan_logs,
    write_export,
)


UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"


def _crash_during_primary_replace(root: str) -> None:
    """Spawn-safe helper: exit after backup durability but before primary replace."""
    import starcompanion.ownership as ownership_module

    store = ownership_module.OwnershipStore("LIVE", root=Path(root))
    state = store.load()
    real_replace = ownership_module.os.replace

    def crash_at_primary(source, destination):
        if Path(destination) == store.path:
            os._exit(91)
        return real_replace(source, destination)

    ownership_module.os.replace = crash_at_primary
    store.save(state)


def catalog():
    org = Org("test", "Test Org")
    contract = Contract(
        "Test_Mission",
        org,
        "Mission",
        reward=Reward(
            blueprint_pools=[
                BlueprintPool(
                    items=["Coda Pistol", "Norfield"],
                    item_ids={"Coda Pistol": UUID_A, "Norfield": UUID_B},
                    item_categories={"Coda Pistol": "weapons", "Norfield": "fuel-nozzles"},
                )
            ]
        ),
    )
    return build_catalog(ContractSet([contract], {org.id: org}))


def ambiguous_catalog():
    org = Org("test", "Test Org")
    contracts = [
        Contract(
            f"Mission_{index}",
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
        for index, blueprint_id in enumerate((UUID_A, UUID_B))
    ]
    return build_catalog(ContractSet(contracts, {org.id: org}))


def event(name="Coda Pistol", timestamp="2026-03-26T17:15:41.684Z"):
    return (
        f'<{timestamp}> [Notice] <SHUDEvent_OnNotification> Added notification '
        f'"Received Blueprint: {name}: " [23] to queue. [Missions][Comms]'
    )


def write(path: Path, text: str):
    path.write_bytes(text.encode("utf-8"))
    return path


def test_channel_scopes_are_isolated_and_live_hotfix_link_is_explicit(tmp_path):
    assert ownership_scope("LIVE") == "LIVE"
    assert ownership_scope("HOTFIX", link_live_hotfix=True) == "LIVE-HOTFIX"
    assert ownership_path("PTU", root=tmp_path) != ownership_path("LIVE", root=tmp_path)
    with pytest.raises(OwnershipError, match="only LIVE and HOTFIX"):
        ownership_scope("PTU", link_live_hotfix=True)
    with pytest.raises(OwnershipError):
        ownership_path("../LIVE", root=tmp_path)


def test_store_round_trip_is_atomic_and_rejects_cross_scope(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    assert store.load() == state
    with pytest.raises(OwnershipError):
        store.save(OwnershipState("PTU"))


def test_manual_multiselect_plan_is_previewed_revision_bound_and_reversible(tmp_path):
    blueprint_catalog = catalog()
    target = OwnershipStore("LIVE", root=tmp_path)
    state = target.load()
    plan = plan_manual_ownership(
        blueprint_catalog,
        state,
        [f"cig:{UUID_A}", f"cig:{UUID_B}", f"cig:{UUID_A}"],
        OwnershipDecision.OWNED,
        now="2026-09-09T12:00:00Z",
    )
    assert len(plan.changes) == 2
    assert not target.path.exists(), "preview must not persist"

    updated = apply_manual_ownership(plan, state)
    target.save(updated)
    assert set(target.load().records) == {f"cig:{UUID_A}", f"cig:{UUID_B}"}
    assert {
        item.source
        for record in updated.records.values()
        for item in record.acquisitions
    } == {"manual"}

    removal = plan_manual_ownership(
        blueprint_catalog,
        updated,
        [f"cig:{UUID_A}"],
        OwnershipDecision.UNOWNED,
        now="2026-09-09T12:01:00Z",
    )
    removed = apply_manual_ownership(removal, updated)
    target.save(removed)
    assert set(target.load().records) == {f"cig:{UUID_B}"}


def test_manual_plan_rejects_unknown_ids_revision_drift_and_before_state_drift():
    blueprint_catalog = catalog()
    state = OwnershipState("LIVE")
    with pytest.raises(OwnershipError, match="not present"):
        plan_manual_ownership(
            blueprint_catalog, state, ["cig:not-known"], OwnershipDecision.OWNED
        )

    plan = plan_manual_ownership(
        blueprint_catalog,
        state,
        [f"cig:{UUID_A}"],
        OwnershipDecision.OWNED,
    )
    newer = copy.deepcopy(state)
    newer.revision += 1
    with pytest.raises(OwnershipConflictError, match="changed after preview"):
        apply_manual_ownership(plan, newer)

    changed = copy.deepcopy(state)
    changed.records[f"cig:{UUID_A}"] = OwnershipRecord(
        f"cig:{UUID_A}", "Coda Pistol"
    )
    with pytest.raises(OwnershipConflictError, match="changed after preview"):
        apply_manual_ownership(plan, changed)


def test_store_inspection_error_redacts_its_absolute_path(monkeypatch, tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path / "private-player-data")
    target = store.path
    real_stat = os.stat

    def denied(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError(13, "permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr("starcompanion.ownership.os.stat", denied)
    with pytest.raises(OwnershipError) as captured:
        store.load()

    assert str(tmp_path) not in str(captured.value)
    assert "private-player-data" not in str(captured.value)


def test_concurrent_stale_writers_are_serialized_and_one_is_rejected(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    initial = OwnershipState("LIVE")
    store.save(initial)
    left = copy.deepcopy(store.load())
    right = copy.deepcopy(store.load())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.save, state) for state in (left, right)]
    outcomes = []
    for future in futures:
        try:
            future.result()
            outcomes.append("saved")
        except OwnershipConflictError:
            outcomes.append("conflict")

    assert sorted(outcomes) == ["conflict", "saved"]
    assert store.load().revision == 2
    assert not store.lock_path.exists()


def test_failed_atomic_replace_preserves_primary_backup_and_cleans_lock(monkeypatch, tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    primary = store.path.read_bytes()
    real_replace = os.replace

    def interrupted(source, destination):
        if Path(destination) == store.path:
            raise OSError("synthetic crash before replace")
        return real_replace(source, destination)

    monkeypatch.setattr("starcompanion.ownership.os.replace", interrupted)
    with pytest.raises(OSError, match="synthetic crash"):
        store.save(state)

    assert store.path.read_bytes() == primary
    assert store.backup_path.read_bytes() == primary
    assert not store.lock_path.exists()
    assert not list(store.path.parent.glob(".ownership.json.*.tmp"))


def test_real_child_crash_before_primary_replace_is_parent_recoverable(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    original = store.path.read_bytes()

    process = multiprocessing.get_context("spawn").Process(
        target=_crash_during_primary_replace, args=(str(tmp_path),)
    )
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 91
    assert store.path.read_bytes() == original
    assert store.backup_path.read_bytes() == original
    assert store.lock_path.is_file()
    current = store.load()
    store.save(current)
    assert current.revision == 2
    assert not store.lock_path.exists()


def test_corrupt_primary_offers_confirmable_last_known_good_recovery(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    store.save(state)
    assert store.backup_path.is_file()
    store.path.write_text("{broken", encoding="utf-8")

    with pytest.raises(OwnershipRecoveryAvailable, match="blueprints recover"):
        store.load()
    recovered = store.recover()

    assert recovered.revision == 3
    assert store.load() == recovered


def test_dead_process_lock_is_recovered_without_losing_state(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    store.lock_path.parent.mkdir(parents=True)
    store.lock_path.write_text('{"pid": 99999999, "token": "dead"}', encoding="ascii")
    state = OwnershipState("LIVE")

    store.save(state)

    assert state.revision == 1
    assert not store.lock_path.exists()


def test_old_lock_owned_by_live_process_is_never_reaped(monkeypatch, tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    store.lock_path.parent.mkdir(parents=True)
    store.lock_path.write_text(
        json.dumps({"pid": os.getpid(), "token": "still-working"}),
        encoding="ascii",
    )
    old = store.lock_path.stat().st_mtime - 3600
    os.utime(store.lock_path, (old, old))
    monkeypatch.setattr("starcompanion.ownership.LOCK_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(OwnershipConflictError, match="busy"):
        store.save(OwnershipState("LIVE"))

    assert store.lock_path.exists()
    assert json.loads(store.lock_path.read_text(encoding="ascii"))["token"] == "still-working"


def test_dead_reaper_artifact_and_dead_lock_are_both_recovered(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    store.lock_path.parent.mkdir(parents=True)
    store.lock_path.write_text('{"pid": 99999999, "token": "dead"}', encoding="ascii")
    reap_path = store.lock_path.with_suffix(store.lock_path.suffix + ".reap")
    reap_path.write_text("99999998", encoding="ascii")

    state = OwnershipState("LIVE")
    store.save(state)

    assert state.revision == 1
    assert not store.lock_path.exists()
    assert not reap_path.exists()


def test_incremental_scan_is_idempotent_and_ignores_ui_echo(tmp_path):
    log = write(
        tmp_path / "Game.log",
        event() + "\n" + '<2026-03-26T17:15:42Z> <UpdateNotificationItem> Notification "Received Blueprint: Coda Pistol: " [23]\n',
    )
    first = scan_logs([log], catalog(), OwnershipState("LIVE"))
    assert first.events_seen == first.acquisitions_added == 1
    second = scan_logs([log], catalog(), first.state)
    assert second.files_read == second.bytes_read == second.acquisitions_added == 0


def test_concurrent_scans_are_deterministic_and_do_not_mutate_shared_state(tmp_path):
    log = write(
        tmp_path / "Game.log",
        event() + "\n" + event("Norfield", "2026-03-27T00:00:00Z") + "\n",
    )
    shared = OwnershipState("LIVE")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _index: scan_logs([log], catalog(), shared), range(32))
        )

    assert not shared.records and not shared.cursors
    assert {result.bytes_read for result in results} == {log.stat().st_size}
    assert {tuple(result.state.records) for result in results} == {
        (f"cig:{UUID_A}", f"cig:{UUID_B}")
    }


def test_growing_file_reads_only_tail_and_partial_final_line_waits(tmp_path):
    log = write(tmp_path / "Game.log", event()[:-10])
    first = scan_logs([log], catalog(), OwnershipState("LIVE"))
    assert first.events_seen == 0
    with log.open("ab") as stream:
        stream.write((event()[-10:] + "\n" + event("Norfield", "2026-03-27T00:00:00Z") + "\n").encode())
    second = scan_logs([log], catalog(), first.state)
    assert second.events_seen == second.acquisitions_added == 2
    assert second.bytes_read == log.stat().st_size


def test_truncation_restarts_and_duplicate_events_are_deduplicated(tmp_path):
    log = write(tmp_path / "Game.log", event() + "\nnoise\n")
    first = scan_logs([log], catalog(), OwnershipState("LIVE"))
    write(log, event() + "\n")
    second = scan_logs([log], catalog(), first.state)
    assert any(item.code == "truncated" for item in second.diagnostics)
    assert second.events_seen == 1
    assert second.acquisitions_added == 0


def test_replacement_same_size_detected_by_prefix(monkeypatch, tmp_path):
    log = write(tmp_path / "Game.log", event() + "\n")
    first = scan_logs([log], catalog(), OwnershipState("LIVE"))
    replacement = (event("Norfield") + "\n").encode()
    replacement = replacement[: log.stat().st_size].ljust(log.stat().st_size, b" ")
    log.write_bytes(replacement)
    second = scan_logs([log], catalog(), first.state)
    assert any(item.code == "replaced" for item in second.diagnostics)


def test_rotation_and_duplicate_lines_do_not_duplicate_acquisition(tmp_path):
    old = write(tmp_path / "old.log", event() + "\n" + event() + "\n")
    current = write(tmp_path / "Game.log", event() + "\n")
    result = scan_logs([old, current], catalog(), OwnershipState("LIVE"))
    record = result.state.records[f"cig:{UUID_A}"]
    assert result.events_seen == 3
    assert len(record.acquisitions) == 1


def test_rapid_rotation_between_discovery_and_open_preserves_both_files(tmp_path):
    current = write(tmp_path / "Game.log", event() + "\n")
    rotated = tmp_path / "logbackups" / "rotated.log"
    rotated.parent.mkdir()
    rotated_once = False

    def rotate(done, total, name):
        nonlocal rotated_once
        if not rotated_once and name == "Game.log":
            current.replace(rotated)
            write(current, event("Norfield", "2026-03-27T00:00:00Z") + "\n")
            rotated_once = True

    first = scan_logs([current], catalog(), OwnershipState("LIVE"), progress=rotate)
    second = scan_logs(discover_log_files(tmp_path), catalog(), first.state)

    assert first.state.records[f"cig:{UUID_B}"].name == "Norfield"
    assert f"cig:{UUID_A}" in second.state.records
    assert second.acquisitions_added == 1


def test_malformed_unmatched_and_pre_epoch_lines_are_explanatory(tmp_path):
    log = write(tmp_path / "Game.log", "garbage\n" + event("Unknown") + "\n" + event("Old", "2026-02-01T00:00:00Z") + "\n")
    result = scan_logs([log], catalog(), OwnershipState("LIVE"))
    assert result.unmatched_names == ("Unknown",)
    assert result.acquisitions_added == 0
    assert result.unresolved_added == 1
    assert result.state.unresolved[0].reason == "no-match"


def test_ambiguous_log_name_is_retained_not_guessed_and_later_reconciles(tmp_path):
    log = write(tmp_path / "Game.log", event() + "\n")
    first = scan_logs([log], ambiguous_catalog(), OwnershipState("LIVE"))
    assert not first.state.records
    assert first.state.unresolved[0].reason == "ambiguous"

    second = scan_logs([log], catalog(), first.state)
    assert second.files_read == 0
    assert second.unresolved_reconciled == 1
    assert f"cig:{UUID_A}" in second.state.records
    assert not second.state.unresolved


def test_ambiguous_acquisition_has_explicit_exact_candidate_resolution(tmp_path):
    log = write(tmp_path / "Game.log", event() + "\n")
    state = scan_logs([log], ambiguous_catalog(), OwnershipState("LIVE")).state
    acquisition = state.unresolved[0].acquisition.acquisition_id
    selected = f"cig:{UUID_B}"

    plan = plan_resolution(state, ambiguous_catalog(), acquisition[:8], selected)
    resolved = apply_resolution(plan, state)

    assert selected in resolved.records
    assert not resolved.unresolved
    with pytest.raises(OwnershipError, match="exact-name candidate"):
        plan_resolution(state, catalog(), acquisition[:8], "cig:" + UUID_A.replace("1", "2"))

    state.revision += 1
    with pytest.raises(OwnershipError, match="changed after preview"):
        apply_resolution(plan, state)


def test_cancellation_never_mutates_input_state(tmp_path):
    log = write(tmp_path / "Game.log", (event() + "\n") * 100)
    state = OwnershipState("LIVE")
    with pytest.raises(ScanCancelled):
        scan_logs([log], catalog(), state, cancel=lambda: True)
    assert not state.records and not state.cursors


def test_cancellation_is_checked_between_bounded_chunks(tmp_path):
    log = write(tmp_path / "Game.log", "x" * (300 * 1024) + "\n")
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(ScanCancelled):
        scan_logs([log], catalog(), OwnershipState("LIVE"), cancel=cancelled)
    assert checks == 3


def test_cancellation_is_checked_inside_a_chunk_with_many_tiny_lines(tmp_path):
    log = tmp_path / "Game.log"
    log.write_bytes(b"x\n" * 200_000)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(ScanCancelled):
        scan_logs([log], catalog(), OwnershipState("LIVE"), cancel=cancelled)
    assert checks == 3


def test_unresolved_reconciliation_is_cancellable_before_full_traversal():
    state = OwnershipState("LIVE")
    for index in range(1_000):
        digest = f"{index:064x}"
        state.add_unresolved(
            UnresolvedAcquisition(
                f"Unknown {index}",
                "no-match",
                Acquisition(digest, "import", None, "seed.json", "f" * 64),
            )
        )
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(ScanCancelled):
        scan_logs((), catalog(), state, cancel=cancel)
    assert len(state.unresolved) == 1_000


def test_scan_snapshots_catalog_indexes_once(monkeypatch, tmp_path):
    blueprint_catalog = catalog()
    alias_getter = BlueprintCatalog.aliases.fget
    by_id_getter = BlueprintCatalog.by_id.fget
    calls = {"aliases": 0, "by_id": 0}

    def aliases(instance):
        calls["aliases"] += 1
        return alias_getter(instance)

    def by_id(instance):
        calls["by_id"] += 1
        return by_id_getter(instance)

    monkeypatch.setattr(BlueprintCatalog, "aliases", property(aliases))
    monkeypatch.setattr(BlueprintCatalog, "by_id", property(by_id))
    log = write(
        tmp_path / "Game.log",
        "\n".join(
            event("Coda Pistol", f"2026-03-26T17:15:41.{index:06}Z")
            for index in range(500)
        )
        + "\n",
    )

    result = scan_logs([log], blueprint_catalog, OwnershipState("LIVE"))

    assert result.acquisitions_added == 500
    assert calls == {"aliases": 1, "by_id": 1}


def test_oversized_line_is_memory_bounded_and_next_line_still_scans(tmp_path):
    log = write(tmp_path / "Game.log", "x" * (1024 * 1024 + 1) + "\n" + event() + "\n")
    result = scan_logs([log], catalog(), OwnershipState("LIVE"))
    assert result.acquisitions_added == 1
    assert any(item.code == "oversized-line" for item in result.diagnostics)


def test_very_large_log_has_bounded_memory_and_zero_byte_rescan(tmp_path):
    log = tmp_path / "Game.log"
    one_megabyte = (b"x" * 1023 + b"\n") * 1024
    with log.open("wb") as stream:
        for _ in range(32):
            stream.write(one_megabyte)
        stream.write((event() + "\n").encode())

    tracemalloc.start()
    first = scan_logs([log], catalog(), OwnershipState("LIVE"))
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    second = scan_logs([log], catalog(), first.state)

    assert first.bytes_read > 32 * 1024 * 1024
    assert first.acquisitions_added == 1
    assert peak < 8 * 1024 * 1024
    assert second.bytes_read == 0


def test_discovery_covers_live_and_rotated_logs(tmp_path):
    (tmp_path / "logbackups").mkdir()
    write(tmp_path / "logbackups" / "old.log", "")
    write(tmp_path / "Game.log", "")
    assert {path.name for path in discover_log_files(tmp_path)} == {"old.log", "Game.log"}


def test_linked_discovery_reviews_live_and_hotfix_without_test_channels(tmp_path):
    game = tmp_path / "StarCitizen"
    live = game / "LIVE"
    hotfix = game / "HOTFIX"
    ptu = game / "PTU"
    for root in (live, hotfix, ptu):
        (root / "logbackups").mkdir(parents=True)
    write(live / "Game.log", "")
    write(live / "logbackups" / "live-old.log", "")
    write(hotfix / "Game.log", "")
    write(hotfix / "logbackups" / "hotfix-old.log", "")
    write(ptu / "Game.log", "")

    discovered = discover_log_files(live, link_live_hotfix=True)

    assert set(discovered) == {
        live / "Game.log",
        live / "logbackups" / "live-old.log",
        hotfix / "Game.log",
        hotfix / "logbackups" / "hotfix-old.log",
    }
    reverse = discover_log_files(hotfix, link_live_hotfix=True)
    assert set(reverse) == set(discovered)
    assert ptu / "Game.log" not in discovered


def test_linked_store_unions_existing_scopes_without_mutating_sources(tmp_path):
    live_store = OwnershipStore("LIVE", root=tmp_path)
    hotfix_store = OwnershipStore("HOTFIX", root=tmp_path)
    live = OwnershipState("LIVE")
    hotfix = OwnershipState("HOTFIX")
    live.add(
        f"cig:{UUID_A}",
        "Coda Pistol",
        Acquisition("a" * 64, "import", None, "live.json", "1" * 64),
    )
    hotfix.add(
        f"cig:{UUID_B}",
        "Norfield",
        Acquisition("b" * 64, "import", None, "hotfix.json", "2" * 64),
    )
    live_store.save(live)
    hotfix_store.save(hotfix)
    live_bytes = live_store.path.read_bytes()
    hotfix_bytes = hotfix_store.path.read_bytes()

    linked = OwnershipStore("LIVE", root=tmp_path, link_live_hotfix=True)
    loaded = linked.load_details()

    assert loaded.continuity_scopes == ("LIVE", "HOTFIX")
    assert set(loaded.state.records) == {f"cig:{UUID_A}", f"cig:{UUID_B}"}
    assert not linked.path.exists()
    linked.save(loaded.state)
    assert live_store.path.read_bytes() == live_bytes
    assert hotfix_store.path.read_bytes() == hotfix_bytes
    assert linked.load_details().continuity_scopes == ()


def test_discovery_reports_directory_errors_without_exposing_paths(
    monkeypatch, tmp_path
):
    backups = tmp_path / "LIVE" / "logbackups"
    backups.mkdir(parents=True)
    real_scandir = os.scandir

    def denied(path):
        if Path(path) == backups:
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr("starcompanion.ownership.os.scandir", denied)
    result = discover_logs(tmp_path / "LIVE")

    assert not result.paths
    assert result.diagnostics[0].source_name == "LIVE - logbackups"
    assert result.diagnostics[0].code == "unreadable-directory"
    assert str(tmp_path) not in result.diagnostics[0].message


def test_discovery_rejects_nonregular_logs_and_is_cancellable(tmp_path):
    backups = tmp_path / "LIVE" / "logbackups"
    backups.mkdir(parents=True)
    (backups / "not-a-file.log").mkdir()
    for index in range(50):
        write(backups / f"{index:03}.log", "")
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        return checks >= 12

    with pytest.raises(ScanCancelled, match="discovery cancelled"):
        discover_logs(tmp_path / "LIVE", cancel=cancel)
    completed = discover_logs(tmp_path / "LIVE")
    assert len(completed.paths) == 50
    assert any(item.code == "unsafe-entry" for item in completed.diagnostics)


def test_discovery_file_limit_fails_closed(monkeypatch, tmp_path):
    backups = tmp_path / "LIVE" / "logbackups"
    backups.mkdir(parents=True)
    for index in range(4):
        write(backups / f"{index}.log", "")
    monkeypatch.setattr("starcompanion.ownership.MAX_DISCOVERED_LOG_FILES", 3)

    with pytest.raises(OwnershipError, match="3-file safety limit"):
        discover_logs(tmp_path / "LIVE")


def test_discovery_diagnostics_flow_into_scan_result(tmp_path):
    warning = ScanDiagnostic("LIVE - logbackups", "unreadable-directory", "denied")
    result = scan_logs(
        (),
        catalog(),
        OwnershipState("LIVE"),
        initial_diagnostics=(warning,),
    )
    assert result.diagnostics == (warning,)


def test_linked_rescan_upgrades_old_basename_only_provenance(tmp_path):
    live = tmp_path / "StarCitizen" / "LIVE"
    live.mkdir(parents=True)
    log = write(live / "Game.log", event() + "\n")
    first = scan_logs([log], catalog(), OwnershipState("LIVE-HOTFIX"))
    record = first.state.records[f"cig:{UUID_A}"]
    record.acquisitions[0] = replace(record.acquisitions[0], source_name="Game.log")
    cursor = next(iter(first.state.cursors.values()))
    first.state.cursors[cursor.identity] = replace(cursor, source_name="Game.log")

    second = scan_logs(
        discover_log_files(live, link_live_hotfix=True),
        catalog(),
        first.state,
    )

    assert second.bytes_read == 0
    assert record.acquisitions[0].source_name == "Game.log"
    migrated = second.state.records[f"cig:{UUID_A}"].acquisitions[0]
    assert migrated.source_name == "LIVE - Game.log"
    assert next(iter(second.state.cursors.values())).source_name == "LIVE - Game.log"


@pytest.mark.parametrize("channel", ("PTU", "EPTU", "TECH-PREVIEW"))
def test_linked_discovery_rejects_nonproduction_channels(tmp_path, channel):
    root = tmp_path / channel
    root.mkdir()
    with pytest.raises(OwnershipError, match="only LIVE and HOTFIX"):
        discover_log_files(root, link_live_hotfix=True)


def test_unreadable_log_diagnostic_redacts_the_absolute_path(tmp_path):
    missing = tmp_path / "private-user-folder" / "Game.log"

    result = scan_logs([missing], catalog(), OwnershipState("LIVE"))

    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.source_name == "Game.log"
    assert str(tmp_path) not in diagnostic.message
    assert "private-user-folder" not in diagnostic.message


def test_import_preview_scmdb_json_apply_and_round_trip_exports(tmp_path):
    source = tmp_path / "scmdb.json"
    source.write_text(json.dumps({"blueprints": [
        {"name": "Coda Pistol", "completed": True},
        {"name": "Norfield", "completed": False},
        {"name": "Unknown", "completed": True},
    ]}), encoding="utf-8")
    plan = plan_import(source, catalog(), OwnershipState("LIVE"))
    assert plan.additions == 1
    assert plan.unmatched_names == ("Unknown",)
    state = apply_import(plan, OwnershipState("LIVE"))
    assert len(state.records) == 1
    assert json.loads(export_json(state, catalog()))["blueprints"][0]["completed"] is True
    assert b"blueprint_id,name,category,acquired_at,source" in export_csv(state, catalog())


def test_import_preview_is_scope_and_revision_bound(tmp_path):
    source = tmp_path / "scmdb.json"
    source.write_text(
        json.dumps({"blueprints": [{"name": "Coda Pistol", "completed": True}]}),
        encoding="utf-8",
    )
    original = OwnershipState("LIVE", revision=4)
    plan = plan_import(source, catalog(), original)

    with pytest.raises(OwnershipError, match="changed after preview"):
        apply_import(plan, OwnershipState("LIVE", revision=5))
    with pytest.raises(OwnershipError, match="changed after preview"):
        apply_import(plan, OwnershipState("PTU", revision=4))


def test_import_limits_shapes_and_export_overwrite_guard(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not_blueprints": []}', encoding="utf-8")
    with pytest.raises(OwnershipError):
        plan_import(bad, catalog(), OwnershipState("LIVE"))
    store = OwnershipStore("LIVE", root=tmp_path)
    with pytest.raises(OwnershipError):
        write_export(store.path, b"{}", store_path=store.path)


@pytest.mark.parametrize(
    "name,content,match",
    [
        ("duplicate.json", '{"blueprints": [], "blueprints": []}', "duplicate JSON key"),
        ("duplicate.csv", "name,name\nCoda Pistol,Coda Pistol\n", "duplicate or excessive"),
        ("nul.json", '{"blueprints":[{"name":"Coda\\u0000Pistol"}]}', ""),
    ],
)
def test_hostile_imports_are_rejected_or_quarantined(tmp_path, name, content, match):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    if match:
        with pytest.raises(OwnershipError, match=match):
            plan_import(path, catalog(), OwnershipState("LIVE"))
    else:
        plan = plan_import(path, catalog(), OwnershipState("LIVE"))
        assert not plan.candidates


def test_deep_json_is_reported_as_invalid_not_an_uncaught_recursion_error(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text('{"padding":' + "[" * 2000 + "0" + "]" * 2000 + ',"blueprints":[]}', encoding="utf-8")
    with pytest.raises(OwnershipError, match="invalid ownership import"):
        plan_import(path, catalog(), OwnershipState("LIVE"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(revision=-1),
        lambda data: data["cursors"].append(
            {
                "identity": "x" * 64,
                "source_name": "Game.log",
                "offset": 2,
                "size": 1,
                "prefix_length": 1,
                "prefix_sha256": "0" * 64,
            }
        ),
        lambda data: data["records"].append(
            {"blueprint_id": "bad\nkey", "name": "Bad", "acquisitions": []}
        ),
        lambda data: data.update(unexpected_payload="not part of schema 1"),
    ],
)
def test_corrupted_store_fields_are_rejected(tmp_path, mutation):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    data = json.loads(store.path.read_text(encoding="utf-8"))
    mutation(data)
    store.path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(OwnershipError):
        store.load()


def test_duplicate_keys_in_ownership_store_are_rejected(tmp_path):
    store = OwnershipStore("LIVE", root=tmp_path)
    state = OwnershipState("LIVE")
    store.save(state)
    text = store.path.read_text(encoding="utf-8")
    store.path.write_text(text.replace('"revision": 1,', '"revision": 1, "revision": 1,'), encoding="utf-8")

    with pytest.raises(OwnershipError, match="duplicate JSON key"):
        store.load()
