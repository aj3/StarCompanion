# Troubleshooting

## Contracts are blank, or text is missing in game

**This is the patch-break symptom, and it is expected after a Star Citizen
update.**

Each build adds, renames and removes string keys. An override generated against
an older build no longer matches, so the game finds nothing for those keys and
renders empty titles and descriptions.

**Fix:** restore your backup, then regenerate against the new build.

```bash
starcompanion restore --backup <backup file> --target <path to global.ini>
```

Backups sit next to the target in `backups/` unless you passed `--backup-dir`,
and the GUI lists them on the Apply tab with one-click restore.

Then re-run `stock` → `import` → `render` → `apply` against the updated install.

> Re-run after **every** patch. There is no version check that can save you here:
> the file is valid, it simply refers to keys the new build no longer has.

## "refusing to write ... is inside what looks like a Star Citizen install"

Deliberate. Writing into a real install needs both `--confirm` and
`--allow-game-folder`:

```bash
starcompanion apply --rendered rendered.json --target <global.ini> \
  --confirm --allow-game-folder
```

Detection looks for `Data.p4k`, `Bin64` or `USER.cfg` in any parent directory.

## "refusing to write without --confirm"

`apply` never writes unless you say so. Use `plan` first to see what would
change — it writes nothing.

## "DataCore version N is not supported"

The game's binary data format changed. StarCompanion refuses rather than
emitting records that look plausible and are wrong. It needs updating for that
patch; nothing you can configure will work around it.

## "no end-of-central-directory record found"

The `--archive` path is not a `.p4k`, or the file is truncated. Check you are
pointing at `<install>/Data.p4k`.

## "profile schema_version N is not supported"

The profile was written by a different build. If it is newer, update
StarCompanion; if older, re-save it from a current profile.

## Changes applied but nothing looks different in game

1. Confirm `USER.cfg` contains `g_language = english`.
2. Confirm you edited the file the game actually loads:
   `<install>/data/Localization/english/global.ini`.
3. Fully restart the client — localization is read at startup.

## A template shows "Template error" in the preview

Normal while typing. The preview reports the problem and recovers as soon as the
template is valid again; nothing is written from a broken template. "Use
built-in" reverts that mission giver.

## Rendering skipped some entries

The validator rejected values that would break in game — most often an unbalanced
tag or a real newline. The skipped keys are listed, and the original text is left
untouched. If your own template caused it, fix the template; nothing invalid is
ever written.

## Getting a clean baseline

You never need to verify or reinstall game files. The pristine `global.ini` can
be read straight out of the archive:

```bash
starcompanion stock --archive "<install>/Data.p4k" --out stock-global.ini
```

The archive is opened read-only and is never modified.
