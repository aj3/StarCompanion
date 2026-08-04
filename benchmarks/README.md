# Streaming resource benchmark

Run from the repository root after installing StarCompanion:

```powershell
python -m benchmarks.p4k_streaming
```

The default fixture has 100,000 unrelated central-directory records plus one
512 MiB decompressed localization entry. It is generated and consumed in 1 MiB
pieces. The localization-only index must retain exactly one directory record.
The command also fails unless extraction's peak worker RSS remains below both
192 MiB and 35% of the entry size, and a production helper cancellation
completes within 2.5 seconds.

This is intentionally separate from the ordinary test suite: it exercises a
full-size payload and samples operating-system RSS, making it more useful as a
release/performance gate than a fast unit test. It uses invented repetitive
text and never reads or redistributes game data.

The C4 ownership scanner has a separate synthetic adversarial gate:

```powershell
python -m benchmarks.ownership_adversarial --megabytes 128
```

It requires a bounded-memory scan, exactly one acquisition at the end of a
large unrelated log, a zero-byte unchanged rescan, and cancellation within two
seconds. The temporary log is generated locally and removed automatically.
