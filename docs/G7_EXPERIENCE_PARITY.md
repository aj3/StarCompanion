# G7 experience-parity architecture

Sprint G7's first slice closes the highest-risk workflow gaps without changing
the verified C0–G6 extraction, provider, source-graph, or operation-plan logic.
All filesystem work remains preview-first, confirmation-gated, and off the GUI
thread.

## Language and stock-localization controls

- Overview shows the selected language, the effective `g_language` value, and
  whether the selected loose override exists. The shell keeps this context
  visible on every page.
- Installed languages are read from the selected local `Data.p4k` index in a
  cancellable worker. An unverified language cannot be activated.
- `USER.cfg` activation changes only the last effective `g_language` assignment.
  It preserves unrelated lines, comments, UTF-8/UTF-8-BOM/UTF-16 BOM encoding,
  and the prevailing line ending.
- The plan binds the reviewed target and before/after hashes. Apply rechecks the
  target, creates a target-scoped backup, journals the atomic replacement, and
  verifies the final state.
- Restore to stock removes only the selected language's loose `global.ini` after
  the same fingerprint, backup, journal, confirmation, and final-state checks.
  A missing override is a no-op.

The CLI exposes the same backend:

```bash
starcompanion languages activate --install <channel-folder> --language french
starcompanion languages activate --install <channel-folder> --language french --confirm
starcompanion languages restore-stock --install <channel-folder> --language french
starcompanion languages restore-stock --install <channel-folder> --language french --confirm
```

## Portable application data

Installed mode keeps platform defaults. Packaged builds may opt into a fixed
marker beside the executable and store data under `StarCompanionData`; any build
may select an absolute custom root through Settings & help. `STARCOMPANION_DATA`
remains the explicit administrator override and takes precedence.

Migration copies only portable preferences, layout, and channel/language INI or
JSON state. It excludes disposable caches, logs, backups, extracted game data,
and unknown files. Count, per-file, and aggregate byte limits apply. Source and
destination hashes are reviewed twice; conflicts, link/junction traversal, and
concurrent migrations fail closed. The activation marker/configuration is
written last, the source is never deleted, and the running process stays on its
original root until restart. Cache files are rebuilt inside nondefault roots.
Synchronized OneDrive, Dropbox, Google Drive, and iCloud paths display an
explicit concurrency warning.

## Editor and presentation controls

- Copy visible rows exports only the filtered in-memory projection, caps rows
  and bytes, strips line/tab delimiters, and neutralizes spreadsheet formulas.
  Stock/rendered evidence details and hidden rows are excluded.
- Closing with dirty string-editor state offers Save, Discard, or Cancel. Save
  flushes pending debounced text before starting the existing C3 background
  persistence operation; a failed save leaves the window open.
- Coarse category toggles update the existing typed profile fields. Individual
  controls and independent provider diagnostics remain authoritative.
- Typed stat-block placement supports only `above` or `below`; rendered values
  still pass the existing validator.

## Verification boundary

Synthetic tests cover hostile control-file encodings, external replacement,
crash recovery after intended deletion, migration conflicts and link insertion,
clipboard formula injection, channel/language isolation, accessibility metadata,
close cancellation, and 100/150/200-percent structural screenshots. No local
game strings, logs, ownership data, absolute diagnostics paths, or signing
material are committed.
