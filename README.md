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
starcompanion plan   --rendered rendered.json --target <path to global.ini>
starcompanion apply  --rendered rendered.json --target <path to global.ini> --confirm
```

`plan` writes nothing. `apply` refuses without `--confirm`, and refuses *again*
inside a detected game install unless you also pass `--allow-game-folder`. A
backup is taken before every write, and `restore` puts it back byte-for-byte.

Pull the pristine baseline straight from the game archive:

```bash
starcompanion stock --archive "<install>/Data.p4k" --out stock-global.ini
```

### SCMDB data

[SCMDB](https://scmdb.net/) is the community database holding the reward data
that is not in the client. StarCompanion reads **exports you download yourself**
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
                        profile ──► Jinja templates ──► renderer ──► validator
                                                                               │
                                            backup ──► injector ◄──────────────┘
```

Contracts are discovered from your own game files by the naming convention CIG
uses for contract strings, filtered by content so item names and UI labels are
never rewritten. Reward values are the only thing that has to come from
elsewhere. The first read of the archive takes about half a minute and is then
cached per game build, so a patch re-reads automatically.

Every rendered value passes the validator before it can be written; anything
that would break in game is skipped rather than emitted, even if your own
template produced it.

## Community reward data (switched off)

Reputation amounts and blueprint lists are **not on your computer** — Star
Citizen's servers decide them, proven by scanning every text-like file in the
archive (see [docs/format-notes.md](docs/format-notes.md) §4). Showing them
therefore means trusting a community-maintained list.

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

- **Reward values are the only thing not read from your game.** Contract
  discovery is fully independent — around 1,750 contracts across 160+ mission
  givers, read from your own install, covering ~84% of StarStrings' curated set
  plus thousands they do not list. But reputation amounts and blueprint pools
  are **not present in the client at all**. The reward structs exist in the DataCore with zero instances, none
  of the 465 mission files mention a single contract key, and the mission
  modules they reference are absent from the archive: that logic is
  server-authoritative. Evidence in
  [docs/format-notes.md](docs/format-notes.md) §4.
- English only. Other languages are a path change plus translated templates.

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
[docs/format-notes.md](docs/format-notes.md), written from our own reading. No
code was copied from any reference tool.

## Provenance

Everything under `src/` was written for this project. To state the specifics,
since "informed by other tools" is easy to say and hard to check:

- **No third-party code is vendored.** The repository contains no copy of, and
  no reference to, any other tool.
- **Dependencies are five mainstream PyPI packages** — PySide6, Jinja2,
  pydantic, zstandard, cryptography. No VCS installs, no URL installs, nothing
  pulled from a personal fork.
- **The application makes no network calls.** There are no HTTP, socket or
  URL-fetching imports anywhere in `src/`.
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
  original idea, and currently the source of reward data
- [SCMDB](https://scmdb.net/) by Krovax — community contract, crafting and
  mining database; the source of reward data that is not in the client
- Format understanding informed by (but not copied from)
  [StarBreaker](https://github.com/diogotr7/StarBreaker) and `scdatatools`

## Legal

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
