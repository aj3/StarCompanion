# StarCompanion — Smart Citizen Catch-up Program

**Planning baseline:** 2026-08-03
**Cadence assumption:** two-week sprints, one small team
**Delivery order:** core functionality first; professional GUI second

This program closes the useful feature gap with Smart Citizen v2.3.0 without
turning StarCompanion into a clone. Smart Citizen is a behavior and game-data
reference. StarCompanion retains its own models, tests, offline-first design,
pure-Python P4K/DataForge readers, and explicit confirmation before modifying a
game file.

The work does **not** fit safely into a single sprint. It is organized as six
core sprints followed by three GUI sprints. Every sprint is independently
shippable and has an exit gate. Sprint C0 is the next executable sprint.

## Product target

At the end of the core phase, a user can select any installed Star Citizen
channel, extract the stock strings and relevant DataForge records directly from
that installation, generate useful enhancements, persist personal edits, track
blueprint ownership, preview a deterministic merge, and apply or roll back the
result without network access.

At the end of the GUI phase, those capabilities are presented through a fast,
accessible, consistent desktop interface with clear provenance, background
progress, safe recovery, and an advanced editor.

## Non-negotiable design rules

1. No automatic network access, telemetry, remote configuration, or updater.
2. No administrator requirement, memory injection, or modification of
   `Data.p4k`.
3. Every write to the game remains user initiated, atomic, validated, backed
   up, and recoverable.
4. A clean installation must work from `Data.p4k`; a loose stock `global.ini`
   must never be assumed.
5. Generated data records its source channel, build fingerprint, extraction
   time, and provider provenance.
6. Real game data, player logs, owned-item exports, and generated caches must
   not be committed or included in release artifacts.
7. Templates run in a sandbox with an allowlisted, primitive-only context.
8. Long P4K/DataForge/log operations are cancellable and never run on the GUI
   thread.
9. Smart Citizen code may only be copied with explicit Apache-2.0 attribution
   and NOTICE handling. Prefer behavior-level reimplementation until
   StarCompanion has an explicit project license.

## Feature parity matrix

| User outcome | Current StarCompanion state | Target | Delivery |
|---|---|---|---|
| Apply on a clean game install | Blocked when no loose stock `global.ini` exists | Extract stock language file from `Data.p4k`, build override, validate, confirm, apply | C0 |
| Safe rendering and writes | Backups exist; template execution and writes need hardening | Sandboxed render, strict tag validation, atomic replace, collision-proof backups | C0 |
| Correct cache refresh | Cache can be keyed as `unknown` | Cache keyed by channel and build/P4K fingerprint | C0 |
| Mission reputation and blueprint rewards | Existing research concluded values were unavailable | Traverse nested DataForge records and UUID links; expose evidence and confidence | C1 |
| Generated ship/item/mission enhancements | Partial contract strings only | Pluggable local enhancement providers with typed output | C2 |
| Persistent personal edits | Profiles/templates exist | Per-channel `user.ini`, deterministic source precedence, import/export, undo history | C3 |
| Blueprint ownership tracker | SCMDB source support only | Local log scan, ownership model, filters, SCMDB-compatible import/export | C4 |
| Multiple game channels | Primarily one selected install | LIVE/PTU/EPTU/HOTFIX/TECH-PREVIEW with isolated state | C5 |
| Multiple game languages | Limited baseline | Extract installed languages and keep edits scoped by language | C5 |
| Settings backup/restore | Not complete | Validated, portable archive of settings and per-channel overrides | C5 |
| Advanced professional UI | Functional tabbed GUI; theme work in progress | Design system, dashboard, advanced editor, responsive background operations | G0–G2 |
| Automatic updater/remote translations | Intentionally absent | Remains absent until signing, provenance, and opt-in policy are designed | Deferred |

## Dependency order

```text
C0 Safety + clean-install pipeline
  └─ C1 DataForge graph and reward extraction
       └─ C2 Enhancement providers
            └─ C3 Merge, overrides, preview, and apply transaction
                 ├─ C4 Blueprint ownership and log scanning
                 └─ C5 Channels, languages, backup, release gate
                       └─ G0 Design system and navigation
                            └─ G1 Advanced editor
                                 └─ G2 Operational polish
```

---

## Sprint C0 — Trustworthy clean-install pipeline

**Goal:** make the existing core safe and ensure the primary workflow works on
a normal Star Citizen installation before adding features.

**Budget:** 30–38 engineering points.

### Implementation checkpoint — 2026-08-03

Completed in the first C0 slice:

- clean installs now prepare the stock localization directly from `Data.p4k`;
- the guided GUI previews and creates the missing override only after consent;
- first apply stores a pristine baseline backup, so Undo works without a prior
  loose override;
- apply and restore use flushed sibling temporary files and atomic replacement;
- backups have collision-proof names and no-op applies write nothing;
- user templates run in Jinja's immutable sandbox, with Python-internal access
  covered by regression tests;
- malformed allowed-tag syntax is apply-blocking and reports its character
  offset, while known CIG legacy balance defects remain visible warnings;
- contract caches are scoped by install, channel, language, version, and P4K
  metadata instead of sharing an `unknown` bucket;
- generated localization, cache, SCMDB, and ownership artifacts are ignored and
  rejected by CI; and
- unchanged theme selections no longer rebuild the global Qt stylesheet on
  every profile edit;
- P4K indexing, localization reads, contract parsing, and update preparation run
  in owned worker threads with staged progress and cooperative cancellation;
- closing the main window cancels and joins every active worker before Qt tears
  down its objects; and
- cancellation is checked during the million-entry central-directory scan and
  can never enter the confirmed atomic replace section;
- individual entry reads, AES decryption, Deflate, and Zstandard decompression
  now checkpoint and report progress in 1 MiB chunks;
- P4K consumers can now receive decompressed chunks directly, so the archive
  reader no longer assembles a complete compressed or decompressed entry;
- localization consumers scan the complete central directory for honest
  progress and language discovery but retain only matching `global.ini`
  records, avoiding the hundreds of megabytes needed for a million-entry
  generic archive index;
- localization parsing consumes the streamed baseline line by line, while
  update preparation keeps its baseline in a parent-owned temporary file;
- archive extraction and parsing run in a spawned helper process; cancellation
  first requests a cooperative stop, then terminates the helper after a bounded
  one-second grace period if it is stuck;
- helper inputs, contract results, and preview results use incremental typed
  JSON Lines artifacts; only small progress/status/error records cross the
  multiprocessing pipe, and the child exits before the parent reconstructs a
  semantic result;
- the parent creates an allowlisted artifact workspace containing only the
  staged input, result, and optional baseline paths; setup failure, child error,
  decode failure, unexpected exit, cooperative cancellation, and forced
  termination all remove every declared artifact without recursive deletion;
- successful previews explicitly transfer the remaining baseline lease to the
  returned context-managed `PreparedUpdate`, while transient input/result
  artifacts are deleted immediately;
- a delayed window close displays an explicit safe-checkpoint message while it
  joins workers instead of appearing frozen; and
- application-global theme idempotence plus deterministic window teardown cut
  the full GUI test file from more than two minutes to roughly eight seconds;
- runtime, development, build, and supply-chain tooling dependencies are exact,
  universal CPython 3.12+ locks with SHA-256 hashes, and even the lock compiler
  is captured in the audited tools lock;
- CI actions use immutable commit SHAs, all dependency surfaces are audited,
  and release wheels must install from a hash-verified wheelhouse with the
  package index disabled;
- the reproducible CycloneDX 1.6 runtime SBOM has a verified root dependency
  graph and is checked for drift in CI; and
- packaged core workflows run with Python socket/DNS access denied and produce
  a parent-owned release manifest containing executable and SBOM hashes.

Verification at this checkpoint: 496 passed, 11 skipped in one invocation from
the hash-locked development environment. All
72 GUI tests pass together in a single Python 3.14/Qt 6 process. Both one-file
executables build successfully, and the frozen CLI completes a clean-install
`import --install` smoke test through the spawned helper using a synthetic P4K.
The cross-platform release benchmark scans 100,001 directory records while
retaining one localization record, streams a 512 MiB decompressed entry at
28.0 MiB peak worker RSS (5.5% of entry size), and observes cancellation in
0.61 seconds against a 2.5-second limit. CI now runs the same bounded-resource
gate on Windows and Linux. All four dependency locks currently report no known
vulnerabilities. The runtime SBOM regenerates byte-for-byte identically, the
release build installs without an index, and the frozen import/extraction smoke
test passes with socket and DNS access denied.

The C0 stop/go gate is complete. Apache-2.0 is declared consistently in the
repository, package metadata, NOTICE, release manifest, and SBOM. The CLI now
uses the same prepared-channel operation as the GUI for a clean-install or
existing-override preview, confirmation-gated atomic apply, scoped backup
selection, and confirmation-gated rollback.
The entry and transport bytes are now bounded. Parsing still builds one
complete semantic key/value model because preview, validation, and rendering
need random key access, but the helper exits before the parent reconstructs its
copy. Future very-large DataForge providers should consume archive chunks into
an indexed on-disk snapshot rather than building an equivalent all-record
object graph.

### Stories

#### C0.1 Extract the stock localization baseline (8 points)

- Introduce a headless `PrepareChannel` service used by both CLI and GUI.
- Discover the selected channel and locate `Data.p4k`.
- Extract the selected language's stock `global.ini` through the existing P4K
  reader when a loose file does not exist.
- Create the override directory/file only after preview and confirmation.
- Return structured stages and errors rather than showing dialogs in core code.

Acceptance:

- A scratch installation containing only `Data.p4k` can reach preview and
  apply.
- Existing loose-file behavior remains supported.
- Integration tests use synthetic P4K fixtures and write only to temporary
  directories.
- CLI and GUI call the same service.

#### C0.2 Sandbox profiles and templates (5 points)

- Replace unrestricted Jinja execution with `SandboxedEnvironment`.
- Pass immutable primitives and allowlisted formatting helpers only.
- Reject attribute traversal, imports, callable globals, and unsafe filters.
- Add malicious-profile regression tests.

Acceptance:

- Templates cannot access Python modules, process state, environment variables,
  or the filesystem.
- All shipped profiles render byte-for-byte equivalent output where safe.

#### C0.3 Transactional apply and rollback (5 points)

- Write to a sibling temporary file, flush, validate, then atomically replace.
- Generate collision-proof backup names and retain a configurable count.
- Verify the installed result and expose one-step rollback.
- Never delete or overwrite a backup to resolve a name collision.

Acceptance:

- Simulated interruption before replace leaves the original intact.
- Failed post-render validation never changes the game override.
- Repeated applies in the same second produce distinct backups.

#### C0.4 Strict localization validation (5 points)

- Detect unfinished, unbalanced, crossed, and unknown markup tags.
- Split warnings from apply-blocking errors.
- Include key, offset, and a repair hint in each issue.
- Add a conservative compatibility allowlist for known game markup.

Acceptance:

- Inputs such as `<EM4 text`, mismatched closing tags, and crossed tags fail.
- Known-good stock strings have no new blocking false positives.

#### C0.5 Cache and provenance fingerprint (3 points)

- Key caches by channel, language, executable/build marker, P4K size, and P4K
  modification timestamp; optionally add a cheap central-directory digest.
- Store schema and extractor versions in every cache manifest.
- Treat missing identity as non-cacheable, not as a shared `unknown` build.

Acceptance:

- Switching channels or replacing `Data.p4k` cannot reuse stale data.
- Old cache schemas are invalidated with a readable reason.

#### C0.6 Repository and release hygiene (3 points)

- Ignore generated caches, rendered output, extracted stock INIs, SCMDB exports,
  logs, and ownership data.
- Make CI reject those artifacts anywhere in a commit.
- Choose and add a project license before reusing any implementation code.
- Lock release dependencies and emit an SBOM; do not add an updater.

Acceptance:

- A seeded sensitive-artifact test makes CI fail.
- Build inputs are pinned and release contents can be enumerated.

#### C0.7 Background job boundary (3 points)

- Define progress, cancellation, result, and failure events for extraction.
- Run existing extraction through this boundary in the GUI.
- Guarantee that cancellation cannot enter the apply/replace critical section.

Acceptance:

- The GUI remains responsive during a representative extraction.
- Closing the window cancels or cleanly detaches work without corrupting state.

### C0 stop/go gate

Do not begin reward extraction until:

- clean-install extract → preview → apply → rollback passes end to end;
- malicious template tests pass;
- malformed localization tags block apply;
- caches invalidate on channel/build changes;
- all tests are green and no game/player artifacts are tracked.

### Suggested two-week execution

| Day | Focus | Demonstrable result |
|---|---|---|
| 1 | Confirm service contract and fixtures | Pipeline state model and synthetic clean install |
| 2–3 | C0.1 | Stock strings extracted from fixture P4K |
| 3–4 | C0.2 and C0.4 | Sandbox and strict validation regression suite |
| 5–6 | C0.3 | Atomic apply and rollback fault tests |
| 7 | C0.5 and C0.6 | Provenance manifest and artifact CI guard |
| 8 | C0.7 | GUI extraction runs in cancellable worker |
| 9 | Integrated CLI/GUI acceptance | Clean-install workflow passes from both surfaces |
| 10 | Stabilization and release candidate | Gate report, documentation, packaged smoke test |

---

## Sprint C1 — DataForge graph and mission rewards

**Goal:** replace the current top-level-record assumption with a reusable graph
index that finds nested structs and follows record references.

**Implemented and checked against synthetic fixtures plus LIVE 4.9.188.23497
(2026-08-03):**

- `extract/dataforge.py` supplies a bounded lazy record index by UUID, path,
  filename, and struct, plus typed scalar conversion and cycle-safe graph walks.
- Nested Career/List contract variants emit separate `MissionFacts`; primary
  reputation scope, blueprint pools, crafting blueprints, and direct item
  rewards retain field-level `Evidence`.
- Duplicate UUIDs are ambiguous rather than first-wins. Missing targets,
  malformed values, cycles, optional-provider absence, and structural build
  drift have explicit synthetic regressions.
- `starcompanion datacore --mission-facts --dcb Game2.dcb` prints the isolated
  provider capability report. The local build produced 2,509 mission facts
  (2,457 titled), 124 with reputation, 768 with blueprint pools (4,356 expanded
  item occurrences), 330 with direct item rewards, and 54,043 evidence links.
  Its status is `degraded`, not failed, because optional entity targets and some
  template titles are absent in the shipped data. No extracted game data is
  committed.

### Scope

- Add `DataForgeIndex` over the existing DataCore parser:
  - index records by filename, struct type, GUID/UUID, and normalized path;
  - expose safe nested-field traversal and typed scalar conversion;
  - resolve references with cycle protection and diagnostic breadcrumbs.
- Build a reputation-amount lookup from reputation records.
- Scan `records/contracts/contractgenerator` records.
- Traverse
  `.//missionResultReputationRewards/SReputationAmountListParams` and resolve
  reputation amounts rather than looking only for top-level reward records.
- Build blueprint pool lookups from `records/crafting/blueprintrewards`, then
  resolve pool membership and crafting blueprints under
  `records/crafting/blueprints/crafting`.
- Emit `MissionFacts` containing localization keys, reputation, blueprint/item
  rewards, source-record IDs, and confidence/diagnostics.
- Add build-drift reports: missing optional fields warn; structural contract
  changes fail a provider, not the entire extraction.

Acceptance:

- Synthetic nested fixtures prove UUID resolution, pool expansion, cycle
  handling, missing targets, and duplicate records.
- A locally installed supported build produces non-zero mission/reward evidence
  when those records exist.
- Every displayed reward can be traced to its record path and fields.
- No SCMDB or other web data is required for extraction.

Smart Citizen reference points:

- `scripts/generate_enhancements_ini.py::_extract_mission_xp`
- `scripts/generate_enhancements_ini.py::build_blueprint_pool_lookup`
- `scripts/generate_enhancements_ini.py::scan_contract_generators`

Use these to understand record relationships and expected behavior; implement
the traversal through StarCompanion's reader and typed models.

---

## Sprint C2 — Local enhancement provider framework

**Goal:** turn extracted game facts into useful enhancements without coupling
the extraction logic to templates or GUI widgets.

### Scope

- Define `EnhancementProvider`, `EnhancementSet`, `Evidence`, and provider
  capability/version contracts.
- Implement providers in this order:
  1. missions: reputation, blueprint rewards, route/location details;
  2. ships: selected stats and component summaries;
  3. ship components and ship weapons;
  4. FPS weapons and medical consumables;
  5. commodities/crafting cross-references;
  6. journal/discovery entries.
- Keep raw facts, presentation formatting, and final localization merge as
  separate layers.
- Let unavailable providers fail independently and show a capability report.
- Snapshot only aggregate counts and hashes for real-build regression checks;
  never commit extracted CIG text.

Acceptance:

- Each provider has synthetic fixtures and golden rendered examples.
- Re-running with identical inputs is deterministic.
- Disabling a provider removes only that provider's output.
- Every generated string exposes its provider and evidence provenance.

Delivered for the mission provider:

- `EnhancementProvider`, `EnhancementSet`, typed evidence, versioned capability
  reports, and deterministic copy-on-merge semantics live outside extraction,
  templates, and Qt.
- The isolated archive helper reads `Data/Game2.dcb`, applies C1 facts, and
  returns cache-v2 contract data with provider health and per-contract evidence.
- CLI import/inspect report provider coverage; render can write a per-key
  provenance sidecar. GUI Start and Data & provenance views show the same data.
- LIVE 4.9.188.23497 produced local reputation, blueprint, and direct item
  rewards with every item localization key resolved and no community input.
- C2 hardening classifies optional reference/data gaps separately from genuine
  schema drift, reports matched and unmatched reward-fact coverage with reasons,
  and keeps the supported LIVE provider available despite optional CIG gaps.
- One-pass scope traversal reduced the measured uncached LIVE import from about
  95 seconds to 56 seconds. Cache v4 interns the final 19,437 evidence links
  into 9,572 unique rows and stores base-text deltas; the expanded reviewed
  LIVE cache is about 6.4 MB, down from the earlier 10.1 MB representation
  without dropping provenance.
- Evidence-backed contract classification resolved all 165 previously
  localized-but-filtered reward facts. It recovered 164 matches: 74
  short-content cases, 58 CIG `,P` aliases, 19 two-segment keys, 10
  shared/atypical title-description layouts, one description-only mission, and
  two contradictory-role records. The last fact is now correctly classified
  as a `LOC_UNINITIALIZED` placeholder rather than a filter miss. Explicit
  typed key membership can relax content and layout heuristics, but never the
  UI/item namespace denylist or requirement for a real description.
- The reviewed LIVE aggregate now covers 1,057 of 1,060 reward facts across
  1,841 contracts. The remaining two facts reference localization keys absent
  from this build and one uses `LOC_UNINITIALIZED`; these are reported as
  source-data gaps rather than classifier failures.
- `tests/real_build_snapshots.json` records counts and deterministic identity/
  reward-shape hashes for LIVE 4.9.188.23497. The opt-in regression test accepts
  a real cache or install and never commits extracted localization text.
- The post-update archive audit found 347 reward labels that resolved CIG
  sentinel keys into visible placeholder text. Sentinel display names now use
  evidence-backed record-filename fallbacks, with a second provider guard that
  prevents raw or resolved placeholders from reaching templates. The reviewed
  aggregate explicitly requires zero placeholder reward labels and now exposes
  4,001 expanded blueprint-pool occurrences and 266 direct items after C4's
  item-localization audit removed nested interaction-label false matches.

Smart Citizen reference: the domain-specific builders in
`scripts/generate_enhancements_ini.py`. Consult them for field discovery and
edge cases, not as a module to embed or execute.

---

## Sprint C3 — Source graph, persistent overrides, and safe apply

**Goal:** give users a deterministic, inspectable editing model whose changes
survive extraction and game updates.

Delivered first C3 source slice:

- Provider capabilities now cache every unresolved localization fact as typed
  source ID, reason, and exact key group; template generation never scrapes or
  truncates human diagnostic strings.
- `fallbacks template` exports blank values only. `fallbacks validate` checks
  schema plus optional build/language context, while `import --fallbacks`
  performs the authoritative check against the selected stock localization and
  DataForge graph.
- Authored values are accepted only for complete reward-bearing key groups that
  remain absent. Existing keys, unrelated additions, partial groups, shared
  placeholders, unsafe values, and cross-build/language reuse fail closed.
- The source is explicit through import, render, channel preview, and confirmed
  apply. Added keys have user-fallback provenance, appear separately in the
  injection plan, receive the same atomic backup/rollback treatment, and cannot
  authorize any other missing rendered key.
- Cache v5 invalidates pre-workflow caches; local mission provider v3 emits the
  structured unresolved records. The reviewed LIVE build exports exactly two
  authorable missions/four blank keys and excludes its `LOC_UNINITIALIZED`
  record without generating replacement prose.

Delivered persistent-edit/source-graph slice:

- The shared model now declares and tests the exact precedence graph
  `stock → language overlay → configured imports → generated enhancements → user.ini`.
  Every pairwise conflict is covered independently of layer construction order;
  later configured imports win in their explicit list order.
- Resolved entries retain the winner, all shadowed contributions, source kind,
  provider evidence, and conflict state. `render --sources-out` emits the full
  report and `--conflicts-out` emits the disagreement subset.
- A per-channel/per-language UTF-8/BOM `user.ini` stores only personal values.
  Scope components and paths are validated, saves and exports use atomic
  replacement, imports are size/count limited, duplicate keys fail closed, and
  an unexpectedly empty model cannot erase populated edits.
- Import preview exposes add/change/unchanged/conflict counts and requires an
  explicit `keep` or `incoming` choice. Set, remove, and import are shared model
  commands with bounded persistent undo/redo; a stale, incoherent, or damaged
  history journal is ignored and can never replay over `user.ini`.
- Render and installed-channel preview/apply automatically select the scoped
  user layer. Confirmed apply revalidates and transactionally normalizes
  `user.ini` before committing the replaceable game override; malformed winning
  values are reported and omitted rather than emitted.

Delivered unified-plan/recovery slice:

- `InjectionPlan` is now the single versioned operation artifact shared by the
  source graph, helper-process preview, confirmed apply, and diagnostics. Its
  deterministic identity covers channel, language, mode, baseline source,
  target fingerprint, baseline/result hashes, add/change/remove/unchanged/
  skipped outcomes, validation, precedence, and sanitized source winners.
- `render --sources-out` evidence can be embedded with `channel --sources`;
  operation plans retain winner kind and conflict state without copying game or
  user localization values. `--plan-out` saves the reviewed artifact and
  `--expect-plan` refuses unless a fresh plan has the exact same identity.
- Merge preparation fingerprints an existing override before and after the
  streamed copy, then binds the copied baseline to that state. Apply checks it
  again after backup and immediately before atomic replace, closing stale-plan
  and mixed-snapshot races. Rollback similarly binds both target and backup.
- Overwrite compares the current effective override to the complete prepared
  stock result, exposing formerly implicit pack-key removals. Explicit
  merge-mode removals require both `--remove-key` and independent user-added
  authorization from `--fallbacks`; omission from rendered input alone never
  deletes a key or stock localization.
- Parent-owned apply/rollback journals live beside scoped backups. Recovery
  finalizes only recognizable pre-replace or post-replace states, never writes
  during diagnosis, and blocks on external divergence. `channel diagnostics`
  reports pending state, last operation, recovery backup, and verified hashes
  without including localization text.
- A fresh read-only LIVE 4.9.188.23497 regression retained the reviewed 1,841
  contracts/5,188 keys and 1,057/1,060 reward-fact coverage. Against the
  existing override, the source-bound plan classified 1,775 changes and 3,413
  unchanged keys with no additions, removals, skips, or errors in 1.88 seconds.
  Its 5,188 sanitized source entries produced a 1.03 MB plan; two independent
  previews were byte-identical (SHA-256
  `3123bb5e3e9d8b7ec975d2368fb79d0862ab2168decfa95023d7638db6884c4c`) and
  contained no localization values. No LIVE file was written.

### Scope

- Establish an explicit precedence graph:
  `stock → language overlay → configured imports → generated enhancements → user.ini`.
- Add per-entry provenance and conflict status.
- Persist only user-modified keys in per-channel/per-language `user.ini`.
- Add import/export with preview and explicit conflict choices.
- Add undo/redo commands at the model layer so CLI and GUI share semantics.
- Produce a diff plan before apply: add/change/remove/unchanged counts and
  validation issues.
- Save `user.ini` transactionally before the game override; never replace a
  populated override with an unexpectedly empty model.
- Preserve the existing profile/template feature through the sandboxed render
  layer.

Acceptance:

- User edits survive re-extraction and regenerated enhancements.
- Merge order is deterministic and covered for every pairwise conflict.
- Import cannot write outside the chosen data root.
- Empty-model and partial-failure tests cannot erase prior edits.

Smart Citizen reference points:

- `src/merger/ini_merger.py`
- `src/utils/user_ini_manager.py`
- `src/parser/ini_parser.py`
- `src/gui/import_dialog.py` for conflict outcomes only

---

## Sprint C4 — Blueprint ownership and local log scanning

**Goal:** match the blueprint-tracker user outcome using local game logs and
portable user-owned data.

**Status: core backend complete.** The C1/C2 bridge now carries each crafting
blueprint record UUID and conservative entity-path category into cache version
6. `blueprints.py` deterministically joins those identities to reward-source
and provider evidence; older/community name-only pools are visibly marked with
a SHA-256 name fallback rather than being mislabeled as CIG identities.

`ownership.py` persists a versioned store under an isolated channel scope.
LIVE/HOTFIX sharing is opt-in; the test channels cannot enter that scope.
Selected `Game.log` and `logbackups/*.log` files are streamed in bounded chunks
using per-file identities, prefix fingerprints, and committed byte offsets.
Unchanged scans read zero bytes, partial final lines remain uncommitted, and
replacement/truncation restarts safely. Duplicate lines and rotated copies
deduplicate on event evidence. Stored evidence contains only timestamp,
basename, byte offset, and hashes—never a complete log line or absolute path.
No-newline input is bounded by a 1 MiB line limit. Exact names that resolve to
zero or several catalog records are retained as unresolved evidence, not
guessed or lost when the byte cursor advances, and are retried against future
catalogs. A separate preview/confirm `unresolved`/`resolve` workflow lets the
user select one of the catalog's exact-name candidates; it cannot target a
fuzzy or unrelated item.

The CLI provides preview-first `blueprints scan` and `blueprints import`,
confirmed CSV/SCMDB-shaped JSON export, aggregate-only diagnostics, and backend
filters for exact search, owned/unowned, reward source, category, and
acquisition source. Import parsing is UTF-8, schema, size, count, and field
limited; ambiguous/unmatched names are reported and never guessed.

### Scope

- Create a stable blueprint catalog from C1/C2 extraction.
- Add an ownership store separate from generated localization caches.
- Scan selected local game logs incrementally with per-file identity and
  watermarks; support forced full rescan.
- Treat LIVE and HOTFIX as optionally linked for ownership scanning while
  keeping PTU/EPTU/TECH-PREVIEW isolated.
- Deduplicate acquisitions and retain minimal evidence needed for explanation.
- Import/export CSV and SCMDB-shaped JSON without contacting SCMDB.
- Implement backend queries for search, owned/unowned, reward source, category,
  and acquisition status.

Acceptance:

- Re-scanning unchanged logs is idempotent and fast.
- Rotation, truncation, duplicate lines, malformed lines, and partial final
  lines are covered.
- Import is schema/size limited, rejects path tricks, and previews changes.
- Ownership data is excluded from git, diagnostics, and release bundles.

Verified synthetically for unchanged-file idempotence, incremental append,
forced rescan, inode/path rotation, same-file truncation and replacement,
duplicate/echo suppression, malformed and pre-epoch records, partial final
lines, oversized-line memory bounds, bounded cancellation, channel/path escape
rejection, SCMDB/CSV transfer, ambiguous acquisition reconciliation, preview
gates, and diagnostics privacy. An additional adversarial pass covers 128 MiB
logs, rapid discovery/open rotation, 32 simultaneous scan previews, stale
writers, dead and live lock owners, abandoned lock reapers, malformed and
duplicate-key state, excessive JSON nesting, hostile CSV/JSON fields, and
injected failure immediately before atomic replacement. Store updates are
serialized across processes and revision-checked; a valid last-known-good
backup is retained and recovery is separately previewed/confirmed. The 128 MiB
gate measured 0.88 MiB peak traced allocation, a 1.340 second scan (95.54
MiB/s), a zero-byte unchanged rescan, and 8.3 ms bounded cancellation on the
review machine. The final suite passes 657 tests with 14 fixture/environment
skips. A frozen offline CLI smoke verifies preview, confirmed scan, query,
aggregate-only diagnostics, unchanged rescans, corruption refusal, and explicit
backup recovery. Real player logs and ownership were not used or added to the
repository.

The read-only LIVE 4.9.188.23497 audit exposed and fixed a prior C2 issue: a
first-match traversal could select deeply nested `Carry`/`Drop` interaction
labels as an item's display name. The evidence-ranked single-pass resolver now
prefers `AttachDef.Localization.Name` and item/vehicle localization references.
The reviewed aggregate is 4,001 expanded pool occurrences, 266 direct items,
and only two genuinely duplicate display aliases; those two remain unresolved
for log ownership rather than being conflated. The final in-process archive and
catalog audit completed in about 73 seconds on the review machine; unchanged
log scans read zero bytes.

Smart Citizen reference points:

- `src/utils/blueprint_log_scanner.py`
- `src/utils/owned_items.py`
- `src/utils/blueprint_export.py`
- `src/gui/blueprint_tracker_tab.py` for user-visible filters and channel rules

---

## Sprint C5 — Channels, languages, portability, and core release

**Goal:** make the completed core reliable across installed game variants and
portable between machines.

**Status: core backend complete.** Install discovery accepts the five reviewed
launcher channels, enumerates strictly shaped localization entries, and
normalizes every channel/language filesystem scope. Custom backup roots are
partitioned by both dimensions. Persisted language packs are local-only source
layers and cannot trigger network retrieval.

The settings archive is preview-first and contains only allowlisted preferences,
per-channel/language `user.ini`, and local language packs. ZIP paths, duplicate
members/JSON keys, compression, CRC, manifest hashes, sizes, counts, ratios,
scopes, and values are validated before restore. Existing files require an
additional replacement authorization; multi-file failure rolls back completed
writes, while a killed process leaves a validated recovery journal and scoped
backups for explicit `settings recover`. Caches, histories, ownership, logs,
backups, and game strings are never
included. Diagnostics export aggregate status/counts without paths, usernames,
values, ownership, logs, or localization strings.

Ordinary archive entries require CRC-32 and exact declared output length. Only
an aligned CIG method-100 entry whose computed CRC demonstrably differs from its
header uses the documented advisory case; that warning propagates through
isolated preparation and CLI extraction while decompression and exact length
remain mandatory. The release threat model and limitations are documented in
`THREAT_MODEL.md` and `KNOWN_LIMITATIONS.md`.

Verification at this checkpoint: 694 tests pass with 14 optional
fixture/environment skips; source compilation, network-surface inspection, and
diff validation pass. The Apache-2.0 wheel contains `LICENSE` and `NOTICE`, and
the rebuilt Windows GUI/CLI executables pass the disconnected packaged smoke,
artifact-manifest, offline-guard, and CycloneDX SBOM checks.

External release assurance is prepared but intentionally gated: normal CI
publishes an explicitly unsigned artifact, while a manual dispatch can enter a
protected `release-signing` environment only after the complete Windows/Ubuntu
matrix. The signing job requires a pinned publisher-certificate thumbprint and
RFC 3161 timestamp, verifies both executables, reruns their disconnected smoke,
and publishes a separately named signed artifact. See `RELEASE_SIGNING.md`.

### Scope

- Discover and validate LIVE, PTU, EPTU, HOTFIX, and TECH-PREVIEW installs.
- Isolate cache, user overrides, backups, logs, and ownership by channel.
- Discover installed localization languages inside each P4K and scope user edits
  by channel/language.
- Add local language-pack import only; remote translation retrieval remains out
  of scope.
- Export/import a small settings archive containing a versioned manifest,
  allowlisted preferences, and per-channel overrides. Apply ZIP size/count/path
  limits and preview before restore.
- Add diagnostics export that redacts usernames, absolute paths, log content,
  ownership, and game strings by default.
- Verify ordinary ZIP CRC fields. For CIG-aligned method-100 entries whose
  header value is demonstrably not ZIP CRC-32, surface an integrity warning and
  require valid decompression plus the exact declared byte length.
- Produce locked, SBOM-bearing builds and document reproducibility/signing
  status.

Acceptance:

- Switching channel or language never leaks cached output or edits.
- Settings archive round-trips and cannot escape the data root.
- Supported packaged builds pass clean-install smoke tests without network.
- Core parity release has a documented threat model and known-limitations list.

---

## GUI phase

The GUI phase begins only after C5. Backend contracts may expose progress and
provenance earlier, but new presentation work must not bypass the core models.

### Sprint G0 — Design system and information architecture

- Separate UI preferences from output profiles.
- Convert the theme work into documented tokens for color, typography, spacing,
  radius, focus, states, and elevation.
- Apply semantic roles consistently; avoid rebuilding the entire application
  stylesheet on each field edit.
- Replace the wizard-like tab flow with clear navigation and a home dashboard:
  active channel/language, extraction freshness, enabled providers, validation
  health, unapplied edits, and last backup.
- Add standard empty, loading, degraded, error, and success states.
- Meet keyboard navigation, visible focus, high-DPI, and contrast requirements.
- Add screenshot baselines at 100%, 150%, and 200% scaling.

### Sprint G1 — Advanced string editor and preview

- Use a model/view table capable of thousands of rows without widget-per-cell
  rendering.
- Add fast search and filters for modified, conflict, missing, provider, source,
  category, and validation state.
- Show stock, merged, and rendered values with provenance and inline validation.
- Add undo/redo, multi-select operations, favorites, and safe reset-to-source.
- Add a live diff/apply preview driven entirely by the C3 plan model.
- Debounce editing and never perform extraction or filesystem I/O on the UI
  thread.

### Sprint G2 — Operations, onboarding, and polish

- Guided first run and install/channel discovery.
- Cancellable extraction/apply progress with honest stages and recovery advice.
- Blueprint tracker UI using C4 queries, not direct log parsing.
- Backup browser and guarded restore flow.
- Profile/settings manager, diagnostics viewer/export, searchable help, and a
  concise privacy screen confirming zero telemetry/network behavior.
- Package smoke tests, accessibility pass, usability test, and release notes.

## Recommended improvements beyond parity

Smart Citizen is useful evidence about what players value, but matching all of
its implementation choices would preserve complexity that StarCompanion does
not need. The following changes should be treated as product improvements, not
optional polish.

### 1. Make the normal workflow one action

The current StarCompanion interface exposes the construction process through
separate Start, Fields, Formatting, Templates, Source, and Apply tabs. That is
useful while developing the pipeline but asks a normal user to understand too
much of the internals.

The default workflow should be:

```text
Select channel → choose enhancements → review changes → apply
```

Templates, source ordering, and field-level rules belong in an Advanced area.
The application should reopen to a dashboard and make “Review changes” the
primary action whenever inputs or the game build change.

### 2. Replace template-first customization with structured formatting rules

Jinja remains valuable as an expert escape hatch, but it is too powerful and
too hard to validate as the primary customization model. Introduce typed rules
for common needs—prefixes, labels, ordering, conditional sections, numeric
formatting, colors/tags, and omission of empty fields. Compile those rules to
the render model. Keep custom templates disabled by default, sandboxed, and
clearly marked as advanced.

This improves safety, enables live validation, makes profiles portable across
schema changes, and gives the future GUI controls that can be explained without
requiring template syntax.

### 3. Make provenance a first-class feature

Every merged or generated value should answer:

- Where did the stock value come from?
- Which provider changed it?
- Which DataForge record/field supports the generated facts?
- Did a local import or the user's override win a conflict?
- Which build and extractor version produced it?

This is more than debugging. A compact “Why this value?” panel would make
StarCompanion substantially more trustworthy than tools that only emit an INI.

### 4. Design for partial success across game updates

Star Citizen data layouts change. Extraction should publish a capability
report rather than return one global success flag. If a build breaks the
commodity provider, stock strings, missions, ships, user edits, preview, and
apply should continue to work. The UI should say exactly which provider was
disabled and retain its diagnostic evidence without showing a generic failure.

Each provider should declare:

- required record types and fields;
- optional fields and fallback behavior;
- supported schema/extractor version;
- output count and anomaly thresholds; and
- whether stale prior output is safe to retain (default: no).

### 5. Prefer an indexed local data snapshot over repeated P4K scans

After extraction, write a versioned, channel-scoped local snapshot containing
only normalized facts required by providers. Providers should query that
snapshot instead of repeatedly opening `Data.p4k` or reparsing all DataForge
records. Build it in one streaming pass where practical, commit atomically, and
invalidate it with the C0 fingerprint.

This should reduce startup time, keep memory predictable, simplify provider
tests, and allow a user to inspect aggregate extraction health without exposing
raw copyrighted game data.

### 6. Separate four kinds of state

Do not store unrelated preferences in the existing output profile model. Use
distinct stores and lifecycles for:

1. application preferences and UI theme;
2. extraction cache and generated facts;
3. render/enhancement profiles; and
4. irreplaceable user data (`user.ini`, favorites, ownership).

Only the fourth category requires protective snapshots on ordinary saves.
Caches must always be disposable. Export/restore should state which categories
are included.

### 7. Keep the CLI as the reference workflow

The GUI should orchestrate stable use cases, not contain business logic. A
headless command that can prepare, extract, generate, diff, validate, apply,
and rollback gives the project:

- deterministic end-to-end tests;
- easier diagnosis and automation;
- a smaller GUI failure surface; and
- proof that the core does not depend on Qt state.

The GUI may combine steps, but its result should be reproducible by a printed
CLI command or a serializable operation plan.

### 8. Add a compatibility report instead of silently guessing

At startup and before apply, show a local report containing channel, build
fingerprint, language, extractor schema, provider status, cache freshness, and
last successful apply/backup. Unknown builds are allowed, but the app should
distinguish “tested,” “appears compatible,” and “provider degraded.” It should
never imply a build is supported merely because extraction did not crash.

### 9. Optimize for recovery, not just prevention

Keep the confirmation requirement, but reduce fear by making the exact output
and undo path obvious. Preview should include the target path, backup path,
number of changed keys, blocking validation issues, and retained user edits.
After apply, surface a persistent “Undo last apply” action until another apply
or external file change makes it unsafe.

### 10. Establish a privacy-verifiable offline mode

“No network” should be testable rather than only stated. Core tests should fail
unexpected socket creation, packaged smoke tests should complete with networking
disabled, and diagnostics should list all externally opened URLs separately
from data transfer. Links may open only after an explicit user click. This gives
StarCompanion a clear identity: local game-data enhancement with no hidden
services.

## Recommended scope cuts

To reach a strong release sooner, avoid spending early sprints on:

- duplicating every Smart Citizen screen or cosmetic option;
- an automatic updater before signed releases exist;
- remote/community source aggregation;
- broad plugin execution or arbitrary Python hooks;
- localization of the application UI before extraction supports multiple game
  languages correctly; and
- elaborate animations, custom controls, or branding before the editor remains
  responsive on full data.

The first excellent release should be narrower but dependable: extract locally,
explain what was found, preserve edits, show an accurate diff, and apply or undo
safely.

## Quality gates for every sprint

- `PYTHONPATH=src python -m pytest tests/ -q` is green.
- New parsing behavior has synthetic fixtures for success, absence, corruption,
  duplicates, and build drift.
- No test depends on the developer's installed game or home-directory layout.
- Filesystem tests use scratch roots and assert paths stay within them.
- No real CIG strings, logs, SCMDB exports, or ownership data enter git.
- Public core APIs have type hints and structured errors.
- Operations that can take more than 100 ms expose progress or run outside the
  GUI thread.
- Security-sensitive changes receive explicit negative tests.
- The sprint ends with a packaged smoke test, migration note, and rollback
  instructions where state formats changed.

## Program completion criteria

Core parity is complete when all C0–C5 gates pass and a disconnected clean
machine can:

1. find an installed channel and language;
2. extract stock strings and supported game facts from `Data.p4k`;
3. generate mission/reward and other enabled enhancements;
4. retain personal edits across regeneration;
5. preview, validate, apply, and roll back atomically;
6. track/import/export blueprint ownership locally; and
7. move settings safely between machines.

GUI parity is complete when the same workflow is discoverable without CLI use,
remains responsive under full-size data, meets the accessibility and scaling
gates, and provides provenance and recovery at every destructive boundary.

## Explicitly deferred

- Automatic updater or installer self-update.
- Automatic remote translation downloads.
- Telemetry, analytics, crash upload, or remote feature flags.
- Community/cloud account synchronization.
- Features dependent on undocumented remote services.

These are not required for Smart Citizen-equivalent user outcomes. Any future
proposal must be opt-in, separately threat-modeled, cryptographically verified,
and unable to affect the offline workflow.
