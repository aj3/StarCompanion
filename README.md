# StarCompanion

Reads Star Citizen's own game files and builds a **customisable** `global.ini`
localization override — so contract titles and descriptions show the reward
information the game leaves out, formatted the way *you* want it.

Everything it shows is read from your own installation. Nothing is downloaded.

> [!IMPORTANT]
> Unofficial fan project. Not affiliated with, endorsed by, or approved by
> Cloud Imperium Games. Use at your own discretion.

## What it does

Star Citizen loads its UI text from a key→string table at
`Data/Localization/english/global.ini`. CIG permits players to override that
file for community translation. StarCompanion rewrites the contract entries in
it to append information the game does not surface:

- **Reputation awarded**, including values that vary by difficulty
- **Blueprint pools** — what a contract can drop, and at which reputation rank
- **Scenario progress points** for event contracts
- **MG Scrip**, regional pool variants, and data caveats

It only ever changes text. No memory patching, no UI injection — that is the
line CIG's community-localization allowance draws, and staying on the right side
of it is deliberate.

## What it cannot do

Worth stating plainly, because these are the things people ask for first:

| Wish | Possible? |
|---|---|
| Sort the contract list by rank | **No.** Sort order is client code, not text. A rank *prefix* in the title is the closest available. |
| Colour-code contracts | **Partly.** The game renders only `<EM>`–`<EM4>`, `<b>`, `<i>` — fixed emphasis levels, no custom colour. |
| Subfolders per mission giver | **No.** Folders are UI structure, not text. A giver prefix makes a flat list scannable instead. |

## Installing

Requires Python 3.12+.

```bash
pip install -e .
```

If `starcompanion` is not found afterwards, pip installed the launchers into a
per-user `Scripts` directory that is not on your PATH. Either add it, or use the
module form, which always works:

```bash
python -m starcompanion --help
```

## Using it

### The interface

```bash
starcompanion-gui          # or: python -m starcompanion.gui.app
```

It opens on **Start here**, which is the whole job in three steps:

1. **Your game** — found automatically. You are never asked to type a path.
   One button reads the contracts out of it.
2. **How to label each contract** — put the mission giver, the difficulty, or
   both at the front of every title.

Then one button: **Update my game**. It says how many contracts will change,
takes a backup, and there is an **Undo my last change** button beside it.

It also warns if your `USER.cfg` is missing `g_language`, since without that
setting the game ignores the override entirely and nothing appears to happen.

*Appearance* changes how the added text looks. *Advanced: custom wording* lets
you write the wording yourself and is not needed for normal use.

A hand-driven apply screen exists for three rarer cases — a second game copy
such as PTU, restoring an older backup rather than the newest, or rebuilding
from a clean file when another pack is installed. It is hidden by default:

```bash
STARCOMPANION_EXPERT=1 starcompanion-gui
```

### The command line

```bash
# reads your game; finds the install by itself
starcompanion import --out cache.json
starcompanion render --cache cache.json --profile rank-first --out rendered.json
starcompanion channel preview --install <LIVE folder> --rendered rendered.json
starcompanion channel apply   --install <LIVE folder> --rendered rendered.json --confirm
starcompanion channel rollback --install <LIVE folder> --confirm
```

`channel preview` prepares from the existing override, or streams the pristine
baseline from `Data.p4k` on a clean install, and writes nothing. `channel apply`
shows the same plan and refuses without `--confirm`. It takes a scoped backup
before the atomic write. `channel rollback` previews and restores the newest
backup by default, also requiring `--confirm`; use `--list` or `--backup` to
select an older scoped backup.

The lower-level `plan`, `apply`, and `restore` commands remain available for
explicit paths. Low-level `apply` additionally requires `--allow-game-folder`
inside a detected install.

Pull the pristine baseline straight from the game archive:

```bash
starcompanion stock --archive "<install>/Data.p4k" --out stock-global.ini
```

### SCMDB data

[SCMDB](https://scmdb.net/) is an optional community database used here for
blueprint ownership and source comparison. StarCompanion reads **exports you download yourself**
— it never contacts the site, because SCMDB's `robots.txt` excludes automated
access to its data endpoints.

Export from SCMDB, then:

```bash
# mark blueprints you already own, so pools show what is left to collect
starcompanion scmdb --export scmdb-tracking.json --cache cache.json

# or compare without changing anything, to see where sources disagree
starcompanion scmdb --export scmdb-pools.json --cache cache.json --compare
```

Owned items are marked in the rendered text:

```
- Antium Helmet Jet [Owned]
- Antium Core Maroon [Owned]
- Antium Legs Maroon
```

`--compare` writes nothing — it reports which contracts agree, which differ, and
in which direction, so you can judge a community correction rather than adopt it
blindly.

> [!NOTE]
> Only SCMDB's blueprint-tracking export shape is known and handled exactly.
> Contract-pool and resource exports are detected structurally; an export
> StarCompanion does not recognise is **reported rather than guessed at**, since
> mis-reading one would put wrong reward data into your game text.

### Local blueprint ownership

Sprint C4 keeps authoritative ownership outside contract/localization caches,
scoped to the selected game channel. It accepts only the game's authoritative
`Received Blueprint` notification, scans logs incrementally by file identity
and byte watermark, and stores hashes plus minimal acquisition evidence rather
than log contents or absolute paths. It does not contact CIG, SCMDB, or any
other service.

```bash
# Preview first; no ownership or watermark is persisted.
starcompanion blueprints scan --cache cache.json --channel LIVE \
  --install "Z:\\RSI\\StarCitizen\\LIVE"

# Save the reviewed acquisitions and scan watermarks.
starcompanion blueprints scan --cache cache.json --channel LIVE \
  --install "Z:\\RSI\\StarCitizen\\LIVE" --confirm

# Search/filter the catalog and joined player state.
starcompanion blueprints list --cache cache.json --channel LIVE \
  --ownership unowned --category weapons --reward-source Foxwell

# Portable user-owned data. Imports preview by default; exports also require
# confirmation. JSON is SCMDB-shaped but is produced and consumed offline.
starcompanion blueprints import --cache cache.json --channel LIVE \
  --file scmdb-tracking.json
starcompanion blueprints import --cache cache.json --channel LIVE \
  --file scmdb-tracking.json --confirm
starcompanion blueprints export --cache cache.json --channel LIVE \
  --out owned-items.json --confirm
```

`--full` forces a complete re-scan. LIVE and HOTFIX remain separate unless
`--link-live-hotfix` explicitly selects their shared ownership scope;
PTU/EPTU/TECH-PREVIEW are never folded into that scope. CSV and JSON imports
are size/count/schema limited, exact-name matched, and report unmatched names
instead of guessing. Ambiguous/no-match log acquisitions remain as minimal
unresolved evidence and are reconciled automatically when a later catalog has
one exact match. `blueprints unresolved` shows the explicit queue and exact
candidates; `blueprints resolve --acquisition <hash-prefix> --blueprint-id
<cig-id>` previews a user-selected resolution and still requires `--confirm`.
`blueprints diagnostics` emits aggregate counts only—no owned item names, log
paths, or acquisition evidence.

Ownership writes use a cross-process lock and revision check, so concurrent
scans cannot silently overwrite one another. Every successful replacement
retains a validated last-known-good `ownership.json.bak`. If the primary file
is corrupt, ordinary commands stop and direct the user to preview
`blueprints recover`; the backup is restored only after repeating that command
with `--confirm`. Duplicate JSON keys, unknown state fields, excessive nesting,
invalid hashes/timestamps/offsets, and hostile CSV/JSON shapes are rejected.

### Channels, languages, and portable settings

Sprint C5 discovers only supported launcher channels and reads installed
languages directly from each channel's `Data.p4k`. Channel, language, cache,
override, ownership, backup, and transaction scopes are normalized before any
path is created.

```bash
starcompanion channels list
starcompanion languages list --install "Z:\RSI\StarCitizen\LIVE"

# Local-only language pack: preview, then persist in LIVE/french scope.
starcompanion languages import --install "Z:\RSI\StarCitizen\LIVE" \
  --language french --file my-french.ini
starcompanion languages import --install "Z:\RSI\StarCitizen\LIVE" \
  --language french --file my-french.ini --confirm

# Portable ZIP excludes caches, history, ownership, logs, backups, and strings.
starcompanion settings export --out settings.zip
starcompanion settings export --out settings.zip --confirm
starcompanion settings import --file settings.zip
starcompanion settings import --file settings.zip --confirm
# Only after a reported interrupted restore:
starcompanion settings recover --confirm

# Shareable support report with private content redacted.
starcompanion diagnostics preview --install "Z:\RSI\StarCitizen\LIVE"
starcompanion diagnostics export --install "Z:\RSI\StarCitizen\LIVE" \
  --out diagnostics.json --confirm
```

Settings imports enforce ZIP CRC, manifest SHA-256, allowlisted paths and
preferences, size/count/compression-ratio limits, duplicate rejection, and
transactional rollback. Existing files require `--replace-existing` in addition
to confirmation. A process crash retains a scoped recovery journal and backups;
`settings recover` previews before rolling the incomplete operation back. See
[the core threat model](docs/THREAT_MODEL.md) and
[known limitations](docs/KNOWN_LIMITATIONS.md). Windows publisher signing is a
protected manual release operation documented in
[release signing](docs/RELEASE_SIGNING.md); normal CI artifacts stay unsigned.

### Built-in profiles

| Profile | What it gives you |
|---|---|
| `default` | Everything, formatted close to StarStrings |
| `minimal` | Rep and a blueprint flag in titles only; CIG's prose untouched |
| `rank-first` | Giver and rank lead every title; rep emphasised above gate notes |

Profiles are versioned JSON — save, share, and reload them.

## Installing the result

1. Back up your existing `global.ini` (StarCompanion does this for you).
2. Point `--target` at `<install>/data/Localization/english/global.ini`.
3. Ensure your `USER.cfg` contains `g_language = english`.

```
StarCitizen/
└── LIVE/
    ├── USER.cfg
    └── data/Localization/english/global.ini
```

See [docs/troubleshooting.md](docs/troubleshooting.md) when something looks
wrong — blank contracts after a patch are the most common case.

> [!WARNING]
> **Re-run after every Star Citizen patch.** Each build adds and changes string
> keys. An override from an older build leaves blank contract titles and missing
> description text in game. If contracts start appearing empty, that is the
> cause — restore your backup or regenerate against the new build.

## How it works

```
Data.p4k ──► p4k reader ──► stock global.ini ──► contract discovery ──┐
                                                                      ├─► domain model
optional: contracts.ini / SCMDB export ──► reward values ─────────────┘        │
                                                                               ▼
                        profile ──► Jinja templates ──► renderer
                                                                               │
 stock → language overlay → imports → generated → user.ini ──► validator
                                                                │
                                     backup ──► injector ◄───────┘
```

Contracts are discovered from your own game files by the naming convention CIG
uses for contract strings. Typed mission-key relationships from
`Data/Game2.dcb` admit short and structurally unusual missions while explicit
namespace denials keep item and UI strings out. The same local mission provider
joins reputation, blueprint pools, and direct item rewards. The first read and
DataForge pass takes about a minute on the tested LIVE build, then it is cached
per game build so a patch re-reads automatically.

Every rendered value passes the validator before it can be written; anything
that would break in game is skipped rather than emitted, even if your own
template produced it.

## Community reward data (switched off)

The default workflow renders local DataForge mission reputation, blueprint
pools, item rewards, and field-level provenance without community data. The
existing rendered-cache workflow can still take reward annotations from an
explicitly supplied community contract list.

That is deliberately **hidden**, because the point of this project is to read
the game rather than re-publish someone else's file. The code is intact and
tested — `sources/contracts_ini.py`, `sources/scmdb.py`, `sources/merge.py` —
and can be switched back on:

```bash
STARCOMPANION_COMMUNITY_REWARDS=1 starcompanion-gui
```

The command line always keeps `--contracts` and the `scmdb` command: those are
explicit opt-ins nobody meets by accident.

## Current limitations

- **Mission enhancements are the first local provider.** Ship, component,
  weapon, commodity, and journal providers remain future C2 work. Unsupported
  DataForge builds degrade independently and report diagnostics instead of
  blocking stock contract rendering.
- English only. Other languages are a path change plus translated templates.

## Persistent personal overrides

Sprint C3 keeps personal text separate from disposable caches and generated
output. Only keys explicitly changed by the user are written to a canonical
UTF-8/BOM `user.ini`, isolated by channel and language under the platform data
directory (or `STARCOMPANION_DATA`). Every mutation is previewed and requires
`--confirm`:

```bash
starcompanion user set --channel LIVE --key Mission_Title --value "My title" --confirm
starcompanion user list --channel LIVE
starcompanion user remove --channel LIVE --key Mission_Title --confirm
starcompanion user undo --channel LIVE --confirm
starcompanion user redo --channel LIVE --confirm
```

Imports are previewed with explicit conflict handling and remain one undoable
model command. `keep` preserves existing personal values; `incoming` replaces
them. The default `error` choice writes nothing while conflicts remain:

```bash
starcompanion user import --channel LIVE --file portable-user.ini
starcompanion user import --channel LIVE --file portable-user.ini --on-conflict keep --confirm
starcompanion user export --channel LIVE --out my-user.ini --confirm
```

Rendering uses one stable precedence order:

```text
stock → language overlay → configured imports → generated enhancements → user.ini
```

For a game-origin cache, `render` infers the channel and includes its
language-scoped `user.ini`. Use `--channel` for other caches. Optional reports
show the winning source, every shadowed contribution, provider evidence, and
all value conflicts:

```bash
starcompanion render --cache cache.json --channel LIVE \
  --language-overlay local-language.ini \
  --import-source first.ini --import-source second.ini \
  --sources-out sources.json --conflicts-out conflicts.json \
  --out rendered.json
```

`channel preview` and confirmed `channel apply` re-overlay the current
`user.ini`, so edits survive regeneration and a stale rendered JSON cannot win.
The INI is revalidated and transactionally normalized before the game override
is committed. `--no-user-edits` is available for deliberate diagnostics.
Undo history is a bounded, checksummed sidecar; if it is stale or damaged it is
discarded without changing `user.ini`.

### Reviewed operation plans and recovery

The source graph and filesystem diff join in one versioned operation plan. It
contains no localization values, but records each add/change/remove/unchanged
key, validation issues, source winner and conflict state, the fixed precedence
order, target/baseline/result SHA-256 identities, and a deterministic plan ID.

```bash
starcompanion render --cache cache.json --channel LIVE \
  --sources-out sources.json --out rendered.json

starcompanion channel preview --install "Z:\RSI\StarCitizen\LIVE" \
  --rendered rendered.json --sources sources.json \
  --plan-out operation-plan.json

starcompanion channel apply --install "Z:\RSI\StarCitizen\LIVE" \
  --rendered rendered.json --sources sources.json \
  --expect-plan operation-plan.json --confirm
```

`--expect-plan` requires a freshly prepared plan to have the same identity as
the reviewed file. A changed override, render input, source winner, mode,
channel, language, or desired result refuses the write. The target is
fingerprinted before and after baseline copying, after backup, immediately
before replacement, and after installation.

Overwrite plans compare the populated override with the complete prepared
stock result, so discarded pack-only keys appear explicitly as removals.
`--remove-key` supports an intentional merge-mode removal only when the same
key is independently authorized as a user-added entry by `--fallbacks`; it is
shown in the same plan and never happens merely because rendered input omitted
a key.

Apply and rollback use an atomic recovery journal beside the scoped backups.
After an interrupted process, StarCompanion recognizes whether replacement did
not happen, completed successfully, or the target entered an unknown external
state. Only the first two are finalized automatically during a confirmed
operation; an unknown state is preserved and blocks further writes.

```bash
starcompanion channel diagnostics --install "Z:\RSI\StarCitizen\LIVE"
```

Diagnostics show the current target identity, pending recovery status, last
apply/rollback plan ID, recovery backup, and verified final fingerprint without
including localization text.

## User-authored missing-key fallbacks

CIG occasionally ships a reward-bearing DataForge mission whose referenced
localization keys are absent. StarCompanion never guesses those strings or
borrows text from a similarly named mission. Instead, the C3 fallback workflow
exports an empty, build-pinned authoring file from the structured provider gaps:

```bash
starcompanion fallbacks template --cache cache.json --out fallbacks.json
starcompanion fallbacks validate --file fallbacks.json --install "Z:\RSI\StarCitizen\LIVE"
```

Fill only the empty `values` yourself, then explicitly carry the same file
through import and application:

```bash
starcompanion import --install "Z:\RSI\StarCitizen\LIVE" --fallbacks fallbacks.json --out cache.json
starcompanion render --cache cache.json --out rendered.json
starcompanion channel preview --install "Z:\RSI\StarCitizen\LIVE" --rendered rendered.json --fallbacks fallbacks.json
starcompanion channel apply --install "Z:\RSI\StarCitizen\LIVE" --rendered rendered.json --fallbacks fallbacks.json --confirm
```

Only exact, complete DataForge key groups that remain absent in the selected
build can be imported. Existing stock strings, unrelated keys, partial mission
pairs, cross-build files, and shared sentinels such as `LOC_UNINITIALIZED` are
rejected. Preview labels the new entries as authorized additions; all other
unknown rendered keys remain skipped. The normal atomic backup and rollback
workflow covers added keys as well as updates.

### A standalone build

No Python needed on the target machine:

```bash
pip install pyinstaller
pyinstaller packaging/starcompanion.spec
```

Produces two executables in `dist/`, with templates and profiles bundled inside:

| File | What it is |
|---|---|
| `StarCompanion.exe` (~56 MB) | The window. Double-click it. |
| `starcompanion-cli.exe` (~18 MB) | The command line. Smaller because it needs no Qt. |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite runs without a game install: tests build their own `.p4k` and `.dcb`
fixtures from the format spec. Tests that want real data skip cleanly unless you
provide it:

```bash
STARCOMPANION_DCB=/path/to/Game2.dcb pytest
```

Game data is never committed — `tests/samples/*.ini` is gitignored, and the
330 MB DataCore lives outside the repository.

Format notes for the p4k and DataCore layouts are in
[docs/format-notes.md](docs/format-notes.md). No implementation code was copied
from a reference tool; consulted format sources are disclosed in
[NOTICE](NOTICE) and [docs/EXTERNAL_RESOURCES.md](docs/EXTERNAL_RESOURCES.md).

## Provenance

Everything under `src/` was written for this project. To state the specifics,
since "informed by other tools" is easy to say and hard to check:

- **No third-party implementation code is vendored.** Development references
  are linked and attributed rather than copied or executed.
- **Dependencies are six mainstream PyPI packages** — PySide6, Jinja2,
  pydantic, zstandard, cryptography, and tzdata. No VCS installs, no URL
  installs, nothing pulled from a personal fork.
- **The application makes no network calls.** CI rejects network-capable HTTP,
  socket, URL-fetching, and QtNetwork imports. The sole socket import is the
  offline verification guard that blocks network access.
- **It executes nothing.** No `eval`, no `exec`, no `subprocess`, no `pickle`
  loading. The only `exec` in the codebase is Qt's own `app.exec()` event loop.
- Reference tools were read to understand CIG's file formats, in clones kept
  outside the repository and since deleted. They were never installed, never
  imported, and never on the Python path. What was taken is format facts —
  signature values, header layouts, record sizes — which any implementation
  must encode.

These are checkable: `grep -rn "import requests\|subprocess\|eval(" src/`
returns nothing.

## Credits

- [MrKraken's StarStrings](https://github.com/MrKraken/StarStrings) — the
  original idea and an optional community contract-list source
- [SCMDB](https://scmdb.net/) by Krovax — community contract, crafting and
  mining database and optional local-export comparison source
- Format understanding informed by (but not copied from)
  [StarBreaker](https://github.com/diogotr7/StarBreaker) and `scdatatools`

## Legal

StarCompanion is licensed under the
[Apache License 2.0](LICENSE). Required attribution and project notices are in
[NOTICE](NOTICE).

Unofficial Star Citizen fan project, not affiliated with the Cloud Imperium
group of companies. All content not authored here is property of its respective
owner.

Customising localization via `global.ini` is
[intended and authorised by CIG](https://robertsspaceindustries.com/spectrum/community/SC/forum/1/thread/star-citizen-community-localization-update)
to support community translations until it is officially integrated. Because
this adds information to an existing official language, CIG can ask for it to
stop at any time. Considered a third-party contribution; use at your own
discretion.

**This project redistributes no game data.** It reads the files already on your
machine and writes only where you tell it to.
