"""Full-size P4K streaming memory and cancellation benchmark.

The default creates a 512 MiB synthetic localization entry incrementally, so
neither fixture construction nor extraction needs a same-sized Python bytes
object.  The stream consumer runs in a sampled child process; cancellation is
measured through the production spawned-helper path.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from starcompanion.extract.p4k import P4KArchive, is_localization_entry
from starcompanion.install import GameInstall
from starcompanion.operations import read_contracts
from starcompanion.tasks import CancellationToken, OperationCancelled, OperationStage


ENTRY = "Data/Localization/english/global.ini"
MIB = 1 << 20


@dataclass(frozen=True)
class BenchmarkResult:
    entry_mib: float
    streamed_mib: float
    peak_rss_mib: float
    peak_to_entry_ratio: float
    cancellation_seconds: float
    max_rss_mib: float
    max_peak_ratio: float
    cancellation_limit_seconds: float
    directory_entries: int
    retained_entries: int

    @property
    def passed(self) -> bool:
        return (
            self.peak_rss_mib > 0
            and self.peak_rss_mib <= self.max_rss_mib
            and self.peak_to_entry_ratio <= self.max_peak_ratio
            and self.cancellation_seconds <= self.cancellation_limit_seconds
            and self.retained_entries == 1
        )


def build_archive(path: Path, size_bytes: int, *, filler_entries: int = 100_000) -> Path:
    """Write a highly compressed fixture using a fixed 1 MiB source buffer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = b"Benchmark_Contract_desc=synthetic streaming benchmark line\n"
    chunk = (line * ((MIB // len(line)) + 1))[:MIB]
    remaining = size_bytes
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(filler_entries):
            archive.writestr(f"Data/filler/{index:07}.bin", b"")
        with archive.open(ENTRY, "w", force_zip64=True) as output:
            while remaining:
                part = chunk[: min(len(chunk), remaining)]
                output.write(part)
                remaining -= len(part)
    return path


def measure_stream_rss(archive: Path) -> tuple[int, int, int]:
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_stream_worker, args=(archive, send))
    process.start()
    send.close()
    peak = 0
    while process.is_alive():
        peak = max(peak, _rss_bytes(process.pid))
        process.join(timeout=0.01)
    peak = max(peak, _rss_bytes(process.pid))
    if not receive.poll(5):
        raise RuntimeError(f"stream benchmark exited with code {process.exitcode}")
    kind, payload = receive.recv()
    receive.close()
    process.join()
    if kind == "error":
        raise RuntimeError(payload)
    streamed, retained = payload
    return streamed, retained, peak


def measure_cancellation(install: GameInstall, limit_seconds: float) -> float:
    token = CancellationToken()

    def cancel_during_entry(event) -> None:
        if (
            event.stage is OperationStage.READ_LOCALIZATION
            and event.current is not None
            and event.current > 0
        ):
            token.cancel()

    started = time.monotonic()
    try:
        read_contracts(
            install,
            token=token,
            reporter=cancel_during_entry,
            cancel_grace_seconds=min(1.0, limit_seconds),
        )
    except OperationCancelled:
        return time.monotonic() - started
    raise RuntimeError("full-size extraction completed without observing cancellation")


def run_benchmark(
    archive: Path,
    *,
    entry_size: int,
    max_rss_mib: float = 192.0,
    max_peak_ratio: float = 0.35,
    cancellation_limit_seconds: float = 2.5,
    directory_entries: int = 100_001,
) -> BenchmarkResult:
    streamed, retained, peak = measure_stream_rss(archive)
    cancellation = measure_cancellation(
        GameInstall(root=archive.parent, channel="LIVE"),
        cancellation_limit_seconds,
    )
    entry_mib = entry_size / MIB
    peak_mib = peak / MIB
    return BenchmarkResult(
        entry_mib=entry_mib,
        streamed_mib=streamed / MIB,
        peak_rss_mib=peak_mib,
        peak_to_entry_ratio=peak_mib / entry_mib,
        cancellation_seconds=cancellation,
        max_rss_mib=max_rss_mib,
        max_peak_ratio=max_peak_ratio,
        cancellation_limit_seconds=cancellation_limit_seconds,
        directory_entries=directory_entries,
        retained_entries=retained,
    )


def _stream_worker(archive: Path, connection) -> None:
    try:
        written = 0

        def consume(chunk: bytes) -> None:
            nonlocal written
            written += len(chunk)

        with P4KArchive(archive, entry_filter=is_localization_entry) as source:
            source.stream(ENTRY, consume)
            retained = len(source)
        connection.send(("result", (written, retained)))
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _rss_bytes(pid: int | None) -> int:
    if not pid:
        return 0
    if sys.platform == "win32":
        return _windows_rss(pid)
    status = Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        output = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)], text=True
        ).strip()
        return int(output or 0) * 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _windows_rss(pid: int) -> int:
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    query = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(query, False, pid)
    if not handle:
        return 0
    try:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return 0
        return int(counters.WorkingSetSize)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=512)
    parser.add_argument("--max-rss-mib", type=float, default=192.0)
    parser.add_argument("--max-peak-ratio", type=float, default=0.35)
    parser.add_argument("--cancel-limit-seconds", type=float, default=2.5)
    parser.add_argument("--index-entries", type=int, default=100_000)
    args = parser.parse_args(argv)
    if args.size_mib < 256:
        parser.error("--size-mib must be at least 256 for a full-size benchmark")

    with tempfile.TemporaryDirectory(prefix="starcompanion-benchmark-") as temporary:
        root = Path(temporary) / "LIVE"
        size = args.size_mib * MIB
        archive = build_archive(
            root / "Data.p4k", size, filler_entries=args.index_entries
        )
        result = run_benchmark(
            archive,
            entry_size=size,
            max_rss_mib=args.max_rss_mib,
            max_peak_ratio=args.max_peak_ratio,
            cancellation_limit_seconds=args.cancel_limit_seconds,
            directory_entries=args.index_entries + 1,
        )
    print(json.dumps({**asdict(result), "passed": result.passed}, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
