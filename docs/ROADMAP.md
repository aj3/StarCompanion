# StarCompanion — Sprint Roadmap

Multi-session build plan. Each phase is independently shippable, has testable
acceptance criteria, and ends with a working tree. Start a session by pasting
that phase's **Resume prompt**.


## Standing rules (apply to every phase)

- **Never write to the game install without asking first, every time.** Test against scratch copies.
- **Never commit game data.** `tests/samples/*.ini` is gitignored; keep it that way.
- **Game files are read-only.** Never write into `Data.p4k`.
- **Text output only.** No memory patching, no UI injection — that leaves what CIG sanctioned.
- Every phase ends green: `PYTHONPATH=src python -m pytest tests/ -q`
- Reference tools (scdatatools, StarBreaker) inform *format understanding* only. No copied code.

## Status

| Sprint | Phase | Status |
|---|---|---|
| 0 · Foundation | 0. Write path | ✅ Done — 36 tests |
| 1 · Headless pipeline | 1. Domain model & importer | ✅ Done — 85 tests |
| | 2. Renderer & templates | ✅ Done — 116 tests |
| | 3. Config profiles | ✅ Done — 145 tests |
| | 4. CLI | ✅ Done — 170 tests |
| 2 · Interface | 5. GUI | ✅ Done — 204 tests |
| 3 · Datamining | 6. Format research & audit | ✅ Done — docs/format-notes.md |
| | 7. P4k reader | ✅ Done — 244 tests |
| | 8. DataCore parser | ✅ Done — 279 tests |
| | 9. Contract extraction | ⚠️ Partial — 292 tests |
| | 9b. Mission XML layer | ✅ Done — reader shipped, premise disproved |
| 4 · Ship | 10. Packaging & release | ✅ Done — 292 tests |

---

# Sprint 0 · Foundation ✅

**Phase 0 — Write path.** The parts that touch the user's game file, built first
so everything downstream sits on proven ground.

Delivered: `src/starcompanion/{ini,validate,inject}.py`, 36 tests.

Key results: byte-identical round-trip of a real 10MB `global.ini`;
suffix-tolerant key matching recovers **104 keys** (1,348 → 1,452) that
StarStrings currently drops (its issue #19); writes are confirmation-gated and
backed up; zero validator false positives on 1,452 real entries.

---

# Sprint 1 · Headless pipeline

Goal: a complete, usable, scriptable tool with no GUI. Proves the whole data
path before any UI work.

## Phase 1 — Domain model & contracts.ini importer

Turn text into structured objects. The importer is scaffolding — Phase 9
replaces it with real extraction, and the model is the seam that makes that
swap invisible to everything downstream.

**Deliverables** — `src/starcompanion/model.py`, `src/starcompanion/sources/contracts_ini.py`, tests

| Type | Holds |
|---|---|
| `Org` | name, ordered rank ladder, display metadata |
| `Contract` | title/desc keys, org, family, difficulty tier, rank index |
| `Reward` | rep, rep range, scenario points (+split flag), scrip |
| `BlueprintPool` | items, rank gate, region variant, caveat |

Normalize here, once: `VE/E/M/H/VH/S` → rank 1–6 plus colour grouping
(VE+E=Yellow, M+H=Orange, VH+S=Red); per-org rank ladders (19 distinct labels
observed); rep ranges (`300 / 16,000`) and split points (`120,000 (Split)`) as
structured values, not strings.

**Delivered** — `src/starcompanion/model.py`, `src/starcompanion/sources/contracts_ini.py`, 49 tests

770 contracts from all 1,449 unique keys across 39 orgs; only 3 unparsed (Covalex
hauling, which genuinely states no fixed reward).

Four things the real data forced, none of them in the original design:

1. **Kind tokens sit mid-key** (`..._Title_VE_001`), so titles never paired with
   their descriptions until the splitter stopped assuming a suffix.
2. **Orgs are spelled inconsistently** — `headhunters`/`Headhunters`/`HeadHunters`
   is one org across 272 entries. Ids are casefolded, display names taken from
   modal casing plus an override table.
3. **Pools stack gates.** `(BitZeros Only)` *and* `Awarded from Neutral level
   variants` is one pool with two conditions; a single `gate` field was silently
   dropping one. `BlueprintPool.gates` is now a list, and all four gate kinds
   (rank/faction/region/repeat) appear in real data.
4. **Contracts have alternate phrasings.** CIG ships `_001`/`_002`/`_003` variants
   the game picks between; 12 contracts have them. They group as one contract but
   every key is retained, since a dropped key is a contract with no override.

Also found: rep can be negative (`-190,500 / 400 / 2,400`), and `contracts.ini`
defines 3 keys twice with differing values — resolved first-wins, matching the
parser.

## Phase 2 — Renderer & default templates

The customization engine. Jinja2 templates take domain objects to `.ini` values.

**Deliverables** — `src/starcompanion/render/`, `templates/default.j2`, per-org overrides, tests

- Template context exposes the domain objects (`contract.rank`, `reward.rep`, `pool.items`)
- Per-org template with default fallback
- Every rendered value runs through `validate_value()` before leaving the renderer
- Template errors are reported with the contract key attached, never swallowed

**Delivered** — `src/starcompanion/render/`, `src/starcompanion/templates/`, 31 tests

Renders all 1,449 keys with zero validation errors. **74% reproduce StarStrings
byte-for-byte** on default settings; the residue is deliberate and classified:
blank-line spacing (we normalise, they inherit), an added MG Scrip line, reward
data propagated to *every* alternate phrasing rather than only the first, and
`[BP]*` derived from gate data rather than their cross-contract shared-string
analysis.

Design notes:

- **Templates use real newlines; the renderer converts them to literal `\n` last.**
  A template author cannot accidentally emit a line break that blanks a contract.
- **Runs of blank lines are left alone** — they occur inside CIG's prose, and
  collapsing them would edit text we were only meant to append to.
- **Base text is recovered by stripping the annotation** from the imported
  output (1,446 of 1,449 keys). Once contracts come from the game files this
  goes away and base is simply the stock string.
- Titles use compact rep (`150/2000`), bodies use spaced (`150 / 2,000`) —
  matching the source's own convention.
- `render_all` skips any value failing validation rather than emitting it, even
  when the user's own template produced it.

## Phase 3 — Config profiles

User preferences as a versioned, shareable, validated document.

**Deliverables** — `src/starcompanion/config.py` (pydantic), `profiles/default.json`, tests

Covers the four customization axes: field toggles (rep, pools, scenario points,
scrip, rank gates, regional variants, caveats); formatting (emphasis tag per
info type, bracket style, title prefix scheme); per-org template bindings;
merge-vs-overwrite behaviour.

**Delivered** — `src/starcompanion/config.py`, `src/starcompanion/profiles/`, 29 tests

Three shipped presets — `default` (StarStrings-alike), `minimal` (title tags only,
CIG prose untouched), `rank-first` (org+rank leads every title, rep emphasised
above gate notes). All three render the real corpus with zero unusable values.

- **`extra="forbid"`** so a typo like `reputaiton` is an error, not a silently
  ignored setting that leaves the user wondering why nothing changed.
- **Version is checked before model validation**, so a future profile reports
  "upgrade StarCompanion" rather than a confusing `Literal` mismatch.
- **Structural vs contextual validation are separate.** Pydantic cannot know
  which orgs exist, so `validate_against(contract_set)` handles that and returns
  readable problems.
- Phase 2 gained **per-field emphasis** (`by_field`) to satisfy "emphasis tag per
  info type"; `<None>` is excluded as it cannot wrap text.

## Phase 4 — CLI

Full pipeline, headless. Ships value before the GUI and makes CI possible.

**Deliverables** — `src/starcompanion/__main__.py`, tests

```
starcompanion import   --contracts <path> --out cache.json
starcompanion render   --cache cache.json --profile p.json --out rendered.json
starcompanion plan     --rendered rendered.json --target <global.ini>
starcompanion apply    --rendered rendered.json --target <global.ini> --confirm
starcompanion restore  --backup <file> --target <global.ini>
```

**Delivered** — `src/starcompanion/__main__.py`, `src/starcompanion/cache.py`, 25 tests

Sprint 1 is complete: a working tool. Full chain verified on a scratch copy of
the real corpus — 1,449 keys rendered, 1,096 updated, 0 skipped, key count held
at 90,121, restore byte-identical.

Two commands beyond the plan: `profiles` (list built-ins) and `inspect` (read a
cache header).

- **A second safety gate.** `--confirm` alone is not enough to write inside a
  detected Star Citizen install — that also needs `--allow-game-folder`. Detection
  looks for `Data.p4k`/`Bin64` in any parent directory, so it catches the real
  folder regardless of drive letter or install path.
- **Distinct exit codes** (`3` refused, `4` invalid) so scripts can tell "you
  didn't confirm" from "the data is bad".
- **The cache is a user-facing file format**, so it is written explicitly rather
  than reflectively, version-tagged, and stamped with which source produced it —
  a cache from the interim importer and one from the game files are not
  interchangeable.

---

# Sprint 2 · Interface

## Phase 5 — GUI

**Deliverables** — `src/starcompanion/gui/`, five tabs over the Sprint 1 pipeline

| Tab | Does |
|---|---|
| Source | pick install path, run/refresh extraction, show build version + counts |
| Fields | per-category toggles |
| Formatting | emphasis tags, bracket style, title prefixes |
| Templates | per-org editor, live preview against a real cached contract |
| Apply | merge/overwrite, diff summary, backup list + restore, confirm-gated write |

**Delivered** — `src/starcompanion/gui/`, 34 tests (headless via `QT_QPA_PLATFORM=offscreen`)

`AppState` owns the contract set, profile and paths, and emits signals; tabs bind
widgets to it and nothing more. No pipeline logic was reimplemented — the
game-install check moved to `inject.looks_like_game_install` so the CLI and GUI
share one implementation rather than two that can drift.

- **A broken template is a normal editing state**, not an error. `TemplateRenderError`
  is caught and shown in the preview pane; typing on recovers immediately.
- **Writing takes three deliberate steps**: refresh the plan, press Apply, confirm
  a dialog restating the target and counts. A target inside a game install adds a
  visible warning on the tab *and* an extra line in that dialog.
- The tag dropdown offers only the seven renderable tags; `<None>` is excluded
  since it cannot wrap text.
- Render results are cached on `AppState` and invalidated on profile change, so
  the live preview stays responsive over the full 1,449-key corpus.

Note: headless screenshots show correct layout but tofu glyphs — the offscreen
platform has no fonts. Text content is asserted directly from the widgets.

---

# Sprint 3 · Datamining

Highest risk, deliberately last — everything downstream is proven by now.

## Phase 6 — Format research & dependency audit

**Deliverables** — `docs/format-notes.md`

Clone scdatatools (MIT) and StarBreaker; read their p4k and DataCore modules.
Audit for malicious behaviour: outbound network calls, obfuscated code,
unexpected writes, install-time scripts. Record findings.

Then document, in our own words: p4k archive layout (modified ZIP, ZStandard,
encrypted entries), `.dcb` structure (header, string table, typed records,
pointer resolution), and where contract/reward records live.

**Delivered** — `docs/format-notes.md`

Both tools audited clean; neither is malicious. Formats documented in our own
words — p4k signatures (including CIG's non-standard `0x14034B50` local header),
ZStandard method `100`, AES-128-CBC with the encryption flag at extra-field
offset 168, and the 120-byte DataCore header with its v6/v8 record-size change.

**Provenance finding that changes the plan:** the canonical scdatatools upstream
at `gitlab.com/scmodding/frameworks/scdatatools` no longer resolves — gone or
private. The GitHub mirror was archived in 2020; the only current copy is an
unstarred personal fork. That vindicates writing our own extractor rather than
depending on it. StarBreaker (Rust, active, ~121 stars) is the better reference.

Noted but not inherited: scdatatools uses `shell=True` with interpolated paths in
six places. Our extractor spawns no processes, so the footgun does not carry over.

## Phase 7 — P4k reader

**Deliverables** — `src/starcompanion/extract/p4k.py`, tests

Read-only streaming reader: enumerate entries, decompress ZStandard, handle
encrypted entries, extract a single named file without unpacking the archive.

**Unlocks the pristine stock baseline** — pulling `global.ini` straight from the
archive means `OVERWRITE` mode never needs the user to verify/reinstall files.

**Delivered** — `src/starcompanion/extract/p4k.py`, `tests/p4kbuilder.py`, 40 tests,
plus a `starcompanion stock` command

All three CIG deviations handled and tested in every combination: the
`0x14034B50` local header, ZStandard method 100, and AES-128-CBC entries flagged
at extra-field offset 168. ZIP64 covered for both per-entry sentinels and a
ZIP64 EOCD.

**Overwrite mode no longer needs a hand-supplied file** — `starcompanion stock
--archive Data.p4k` produces the pristine baseline straight from the install,
and a test drives that output through an overwrite apply.

- **Tests build their own archives** (`tests/p4kbuilder.py`) from the spec rather
  than reusing the reader's assumptions, so the reader has to actually cope with
  the format. Deterministic, and needs no game install present.
- Bug worth remembering: resolving ZIP64 fields with `take() or current` silently
  discarded a **header offset of 0** — legitimate for the first entry in an
  archive. Falsy-zero, caught by a test asserting the offset was resolved.
- Read-only is asserted, not assumed: a test reads every entry and checks the
  archive's bytes, size and mtime are unchanged, and another reads from a
  chmod-444 file.
- Memory is asserted too: reading one small entry out of a >16 MB archive must
  touch under 1 MB total.

**Validated against the real install** (read-only, with permission) — build
`sc-alpha-4.9.0`, a 151 GB `Data.p4k` holding 1,364,115 entries. Opened in 32 s
using 537 MB, extracted the stock 10.4 MB `global.ini`, and the archive's size
and mtime were unchanged afterwards.

Three corrections only the real archive could surface:

1. **The shipped `global.ini` is CRLF**, not LF — every community pack
   redistributes it as LF, which is what Phase 0 was validated against. Splitting
   on `\n` alone left a stray `\r` on all 90,121 values, which would have leaked
   into rendered output. `LocalizationFile` now detects and preserves the
   convention either way.
2. **The DataCore is `Data/Game2.dcb`**, not `Data/Game.dcb` — the path the
   reference tools use no longer exists. Phase 8 must discover by extension.
3. **Language folders carry region suffixes** (`french_(france)`, not `french`).

With the CRLF fix, stock vs StarStrings differs in exactly 2,110 of 90,121 keys
— the annotations — rather than all of them.

## Phase 8 — DataCore parser

**Deliverables** — `src/starcompanion/extract/datacore.py`, tests

Parse `.dcb`: header → string table → typed records → resolve pointers to plain
dicts. Version-tag every parse.

**Delivered** — `src/starcompanion/extract/datacore.py`, `tests/dcbbuilder.py`,
31 tests, plus a `starcompanion datacore` inspect command

Parses the real 330 MB `Game2.dcb` in ~2 s: **version 8, 6,685 structs, 23,722
properties, 772 enums, 116,512 records.** Both record layouts supported (v6 = 32
bytes, v8 = 36 with the added tag field).

Confirmed the data Phase 9 needs is there:

- **`MissionBrokerEntry`** — 2,584 records, with `title`, `description` and
  `missionGiver` typed `LOCALE`. Those hold localization keys: the link to
  `global.ini`.
- **`BlueprintReward`** — carries a `weight` alongside `blueprintRecord`, so drop
  probability is available. StarStrings does not surface that.
- **`ContractPrerequisite_Reputation`** — with `minStanding`/`maxStanding`.
  **Prerequisites do exist here**, unlike in `contracts.ini`, which answers an
  earlier open question.

Bug worth remembering: `HEADER_FORMAT` was 128 bytes rather than 120 and worked
only because the two surplus fields were never read — it would have crashed on a
file exactly one header long. Now asserted at import, and tested.

Real-file tests are opt-in via `STARCOMPANION_DCB` (or `tests/samples/Game2.dcb`)
and skip cleanly; the 330 MB file is never committed.

## Phase 9 — Contract extraction

**Deliverables** — `src/starcompanion/extract/contracts.py`, data-driven record rules, tests

Walk decoded records for mission definitions: title/desc string IDs, rep,
scenario points, scrip, blueprint pool refs, rank-tier variants, regional
variants. Emit the Phase 1 domain model, replacing the `contracts.ini` importer.

Record-walking rules live in editable config so a patch break is a config edit,
not a rewrite.

**Delivered (partial)** — `src/starcompanion/sources/datacore_source.py`, 13 tests

Also extended Phase 8 with **instance-data reading**: data mappings, the
instance region after the string tables, and typed field decoding. Validated
hard — **computed field widths match the declared `struct_size` for all 6,685
structs, zero mismatches.**

Extraction from `MissionBrokerEntry` yields **2,492 contracts, 1,007 keys, 915
resolving against stock global.ini — of which 912 are contracts StarStrings does
not cover.** Independent extraction works.

### Why this phase is only partial

Rewards are **not in the DataCore**. Established, not assumed:

- All 67 LOCALE-bearing structs were scanned across all 116,512 records.
  `MissionBrokerEntry` is the only one holding contract strings, and it reaches
  just **95 of StarStrings' 1,449 keys**.
- `BlueprintReward` and `MissionReward` exist in the schema with **zero
  instances**. Reading a broker entry at pointer depth 0, 1 and 2 never reaches
  a `blueprintRecord` or `minStanding`.

Reward values therefore live in the Subsumption mission definitions — 465 files
under `Data/Libs/Subsumption/Missions/` — which needs a CryXMLB reader and
mission-graph traversal. That is genuinely new scope, so it is **Phase 9b**
rather than something to quietly fold in here.

Speculative reward-walking code was written, found to be unreachable, and
**deleted rather than shipped**. Contracts from this source carry an empty
`Reward`, and a test asserts they stay that way so the gap cannot regress into
fabricated data. `contracts_ini` remains the reward source in the meantime.

## Phase 9b — Mission XML layer

**Goal** — reach the ~1,354 contract strings and all reward values that live in
Subsumption mission definitions rather than the DataCore.

**Deliverables** — a CryXMLB reader (`extract/cryxml.py`), a mission-definition
walker, and reward extraction feeding the existing domain model.

**Delivered** — `src/starcompanion/extract/cryxml.py`, `tests/cryxmlbuilder.py`,
15 tests

The CryXmlB reader works and parses real mission files (641 nodes on the first
one tried). It is genuinely useful for any future work on Subsumption content.

**The reward half of this phase was disproved rather than built.** Reward values
are not in the shipped client data at all:

| Check | Result |
|---|---|
| `BlueprintReward` / `MissionReward` records | **0 instances** — schema only |
| StarStrings' 1,449 keys in the 465 mission files | **0** |
| `Data/Libs/Subsumption/Missions/PU/` — the path every `missionModule` names | **0 entries** |
| "Foxwell" anywhere in the 1.36 M-entry archive | 20 hits, all art assets |

`global.ini` ships all 1,449 strings so the client can *display* them, but the
mission logic that selects and rewards them is server-authoritative. That also
explains the zero-instance reward structs.

**Consequence:** no client-side tool can extract reputation amounts or blueprint
pools. StarStrings' values are derived from observation and community reporting,
not from reading these files — which is consistent with MrKraken relying on bug
reports to correct pools (their issues #3, #6, #14, #40).

So `contracts_ini` stays the reward source, not as a shortcut but because it is
the only source that exists. Full details in docs/format-notes.md §4.

**Resume prompt**
> Read docs/ROADMAP.md Phase 9b and docs/format-notes.md. Implement the CryXMLB
> reader and mission-definition walker to extract reward data. Cross-check against
> StarStrings' contracts.ini and flag uncertain cases rather than asserting them.

---

# Sprint 4 · Ship

## Phase 10 — Packaging & release

**Deliverables** — PyInstaller build, `README.md`, install/troubleshooting docs, CI

**Delivered** — `README.md`, `docs/troubleshooting.md`, `packaging/`,
`.github/workflows/ci.yml`

- **One-file build verified by building and running it**, not by assuming the
  spec was right. First attempt crashed — PyInstaller runs its target as
  `__main__`, breaking the package's relative imports — so the entry point is
  now `packaging/entry.py`. The 59 MB `StarCompanion.exe` starts clean, and all
  six template/profile files are confirmed present inside the archive.
- **Fresh-clone condition verified** by hiding `tests/samples/` and re-running:
  262 pass, 30 skip, nothing errors. CI additionally fails the build if any
  `.p4k`, `.dcb` or sample `.ini` is ever committed.
- **The documented install path was tested**, which caught that pip puts the
  launchers in a per-user `Scripts` directory often missing from PATH. README
  now gives `python -m starcompanion` as the always-works form.
- **Patch-break drill** is the first entry in troubleshooting: blank contracts
  after an update, why no version check can catch it, and the restore command.
- End-to-end run against the real pristine `global.ini` extracted from a live
  install: 1,449 keys updated, count held at 90,121, CRLF preserved, restore
  byte-identical.

README states plainly that the project is unofficial, text-only, and
redistributes no game data — and is honest that reward values still come from
StarStrings pending Phase 9b.

---

# Resume prompts

Paste at the start of a session.

**Phase 1**
> Read docs/ROADMAP.md. Implement Phase 1 (domain model + contracts.ini importer). Follow the standing rules — never touch the game install, don't commit game data. End with all tests green.

**Phase 2**
> Read docs/ROADMAP.md. Phase 1 is done. Implement Phase 2 (renderer + default templates), with parity against real StarStrings output for Foxwell, Covalex and TheCollector.

**Phase 3**
> Read docs/ROADMAP.md. Phases 1–2 done. Implement Phase 3 (pydantic config profiles) covering all four customization axes.

**Phase 4**
> Read docs/ROADMAP.md. Phases 1–3 done. Implement Phase 4 (CLI). Verify the full chain against a scratch copy of tests/samples/global.ini — never the real install.

**Phase 5**
> Read docs/ROADMAP.md. Sprint 1 complete. Implement Phase 5 (PySide6 GUI). Keep all business logic in the Sprint 1 modules; the GUI only drives them.

**Phase 6**
> Read docs/ROADMAP.md. Implement Phase 6: audit scdatatools and StarBreaker for malicious behaviour, then write docs/format-notes.md describing the p4k and DataCore formats in our own words. Do not copy their code.

**Phase 7**
> Read docs/ROADMAP.md and docs/format-notes.md. Implement Phase 7 (read-only p4k reader). Extract stock global.ini from Data.p4k. Read-only on game files — confirm the archive is unmodified afterward.

**Phase 8**
> Read docs/ROADMAP.md and docs/format-notes.md. Implement Phase 8 (DataCore .dcb parser). Fail loudly on unrecognized structure versions rather than emitting partial data.

**Phase 9**
> Read docs/ROADMAP.md. Implement Phase 9 (contract extraction) emitting the Phase 1 domain model. Cross-check against StarStrings' contracts.ini and flag uncertain cases rather than asserting them.

**Phase 10**
> Read docs/ROADMAP.md. Implement Phase 10 (packaging, README, CI). Build a one-file Windows executable and document the per-patch update requirement.

---

# Open decisions

Not blocking, worth settling before the phase that needs them:

1. **Pull Phase 7 forward?** The p4k reader unlocks a pristine stock baseline. Doing it right after Sprint 1 would remove the last dependency on hand-supplied sample files — at the cost of hitting the risky work sooner. *(needed by: Phase 5)*
2. **Non-English locales?** Everything currently assumes `english`. Supporting others is mostly a path change, but templates would need translating. *(needed by: Phase 10)*
3. **Publish the repo?** Affects README framing, licence choice, and how loudly the no-game-data rule needs stating. *(needed by: Phase 10)*
4. **Share profiles with StarStrings users?** Profiles are portable by design; a small preset gallery is nearly free if wanted. *(needed by: Phase 3)*
