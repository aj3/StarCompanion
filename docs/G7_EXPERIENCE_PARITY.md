# G7 experience-parity architecture

Sprint G7 closes the experience-parity workflow while preserving the verified
C0–G6 write, source-graph, and operation-plan boundaries. All filesystem work
remains preview-first, confirmation-gated, and off the GUI thread.

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

## Focused and accessible experience

- Dedicated Simple mode hides every detailed page and Overview card, leaving
  the existing Update and Undo actions. Hidden navigation and shortcuts are
  disabled, while the persistent Full mode button always provides an exit.
- A replayable non-modal coach tour moves only navigation and keyboard focus.
  Completion is a portable interface preference; the tour never
  starts extraction, changes a profile, or writes a game file.
- Dark, Light, Midnight, and High Contrast palettes share the same semantic
  stylesheet. Every text/surface pair and focus ring passes the existing WCAG
  regression matrix.
- The event viewer is a 500-record in-memory ring. It accepts only fixed event
  keys and levels, redacts usernames, paths, email addresses, long tokens, and
  controls, and caps each detail and the JSON export. Raw exceptions, game
  logs, game strings, ownership, and user-authored values never enter it.
- The offline interface-localization layer validates complete bounded catalogs,
  falls back to bundled English, performs no discovery or download, and stays
  independent from the selected game language. English is complete; the French
  shell preview proves live catalog switching while full feature-page translation
  remains a separately reviewed content task.
- A deterministic, conservative translator-review catalog inventories 1,323
  unique messages at 1,513 source locations, including dialogs, accessibility
  text, helper-rendered labels and status/error text, dynamic format
  templates, and offline help. Stable IDs, placeholders, file/line context, and
  review status are stored in `docs/ui-translation-source.json`; CI rejects a
  stale catalog.
- The bundled `qps-ploc` pseudo-locale accents and expands text without changing
  Python-format, printf, or mission-token placeholders. The shell is rendered at
  100, 150, and 200 percent scale, and every catalog entry includes its pseudo
  rendering for translator/layout review.

## Evidence-bounded presentation closure

- Entity presentation now carries all already-typed G5 vehicle, ship/FPS weapon,
  medical, and commodity values through cache schema 10. Each selectable stat
  has a conservative numeric bound, complete-unit tag budgeting, and per-value
  evidence. Conflicting shared-name facts remain suppressed.
- Component subtype is derived only from the provider's reviewed record-path
  families. Missile seeker notation accepts only `CrossSection`,
  `Electromagnetic`, or `Infrared` and renders `CS`, `EM`, or `IR`; any other
  value remains invisible.
- The legacy presentation pack remains default-off and now contains 19 rules:
  raw-mineral wording, eight illegal-item warnings, five shortened multi-tool
  attachments, the mining-compendium grouping, and refueling quick tips. Every
  rule requires build `1.0.191.55227`, an exact key, and an exact stock value or
  whole-value SHA-256. The two journal transforms reuse the local stock text, so
  no journal corpus is embedded in the repository.
- Independent public-source validation used pinned StarStrings commit
  `b83d58b` for intended outcomes and pinned stock commit `38dd1cd` for before
  values. All 19 rules matched the reviewed 4.10 source with no drift. Runtime
  use remains local and offline.

## Verification boundary

Synthetic tests cover hostile control-file encodings, external replacement,
crash recovery after intended deletion, migration conflicts and link insertion,
clipboard formula injection, channel/language isolation, accessibility metadata,
close cancellation, event redaction/caps, translator-catalog freshness,
placeholder-safe pseudo localization, simple-mode navigation confinement,
four-theme contrast, and 100/150/200-percent English and pseudo-locale
structural screenshots. No local
game strings, logs, ownership data, absolute diagnostics paths, or signing
material are committed.
