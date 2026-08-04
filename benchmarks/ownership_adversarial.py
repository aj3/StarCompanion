"""Synthetic large-log memory, idempotence, and cancellation benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import tracemalloc
from pathlib import Path

from starcompanion.blueprints import BlueprintCatalog, CatalogEntry, normalize_blueprint_name
from starcompanion.ownership import OwnershipState, ScanCancelled, scan_logs


MIB = 1 << 20
BLUEPRINT_ID = "cig:11111111-1111-1111-1111-111111111111"
EVENT = (
    b'<2026-03-26T17:15:41.684Z> [Notice] '
    b'<SHUDEvent_OnNotification> Added notification '
    b'"Received Blueprint: Coda Pistol: " [23] to queue.\n'
)


def _catalog() -> BlueprintCatalog:
    name = "Coda Pistol"
    return BlueprintCatalog(
        (
            CatalogEntry(
                blueprint_id=BLUEPRINT_ID,
                name=name,
                normalized_name=normalize_blueprint_name(name),
                category="weapons",
            ),
        )
    )


def run(megabytes: int) -> dict[str, int | float]:
    if megabytes < 1:
        raise ValueError("--megabytes must be positive")
    catalog = _catalog()
    with tempfile.TemporaryDirectory(prefix="starcompanion-ownership-bench-") as raw:
        log = Path(raw) / "Game.log"
        prefix = b"synthetic-noise="
        line = prefix + (b"x" * (1023 - len(prefix))) + b"\n"
        block = line * 1024
        with log.open("wb") as stream:
            for _ in range(megabytes):
                stream.write(block)
            stream.write(EVENT)

        initial = OwnershipState("LIVE")
        tracemalloc.start()
        started = time.perf_counter()
        scanned = scan_logs([log], catalog, initial)
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        unchanged = scan_logs([log], catalog, scanned.state)
        cancel_checks = 0

        def cancel() -> bool:
            nonlocal cancel_checks
            cancel_checks += 1
            return cancel_checks >= 8

        cancel_started = time.perf_counter()
        try:
            scan_logs([log], catalog, OwnershipState("LIVE"), cancel=cancel)
        except ScanCancelled:
            cancellation_seconds = time.perf_counter() - cancel_started
        else:
            raise AssertionError("large-log cancellation did not interrupt the scan")

        if scanned.acquisitions_added != 1:
            raise AssertionError("synthetic blueprint acquisition was not found exactly once")
        if unchanged.bytes_read != 0:
            raise AssertionError("unchanged rescan read log bytes")
        if initial.records or initial.cursors:
            raise AssertionError("cancel/preview scanning mutated its input state")
        if peak >= 16 * MIB:
            raise AssertionError(f"peak traced allocation {peak / MIB:.2f} MiB exceeds 16 MiB")
        if cancellation_seconds >= 2.0:
            raise AssertionError("bounded cancellation exceeded two seconds")

        size = log.stat().st_size
        return {
            "log_bytes": size,
            "log_mib": round(size / MIB, 2),
            "scan_seconds": round(elapsed, 3),
            "throughput_mib_per_second": round((size / MIB) / elapsed, 2),
            "peak_traced_mib": round(peak / MIB, 2),
            "acquisitions_added": scanned.acquisitions_added,
            "unchanged_rescan_bytes": unchanged.bytes_read,
            "cancellation_seconds": round(cancellation_seconds, 4),
            "cancellation_checks": cancel_checks,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--megabytes", type=int, default=128)
    args = parser.parse_args()
    print(json.dumps(run(args.megabytes), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
