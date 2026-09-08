# G3 structured presentation rules

G3 begins the 0.3.0 development line and makes validated profile fields the
normal way to shape generated contract text. It does not change extraction,
source precedence, operation plans, game-folder writes, backups, or recovery.

## Profile schema v2

The `wording` object contains:

- `mode`: `structured` by default, or explicit `advanced` template execution;
- `section_order`: every reward section exactly once;
- `labels`: nine bounded plain-text labels;
- `reputation_separator`: spaced slash, compact slash, or bullet; and
- `thousands_separator`: whether generated reward numbers use grouping.

Version 1 profiles migrate when loaded. Profiles without templates enter
structured mode. Profiles with templates retain their exact source and enter
advanced mode, preserving historical output. Saving writes schema v2. Newer or
pre-v1 schemas remain rejected rather than guessed.

## Safety boundary

Structured labels must be 1–48 trimmed characters and cannot contain markup,
backslashes, line/control characters, surrogate code points, or Unicode
direction controls. The renderer repeats these checks for direct API callers.
Reordering cannot omit or duplicate a reward section. Final rendered values
still pass the existing global.ini validator.

Advanced templates are stored while disabled, so switching back to structured
mode is nondestructive. Enabling them is explicit and profile-scoped. They run
inside the existing immutable Jinja sandbox and cannot bypass final output
validation.

## GUI and threading

Presentation changes only the in-memory profile and invalidates the existing
render cache. Custom Wording disables its editor until advanced mode is
enabled and distinguishes active custom wording from stored inactive source.
No archive, filesystem, helper-process, or network operation was added to the
GUI thread.

The completed integration pass exposes all nine validated wording labels in
the Presentation page. It also closes a C4/G2 operational gap: a visible,
portable option defaults to reviewing current and rotated logs from both sibling
LIVE and HOTFIX folders in one background scan. Users can separate those stores;
the CLI continues to require its explicit shared-scope flag. Enabling the shared
view unions older separate ownership read-only and consolidates a copy only in a
confirmed write, without changing either source store. Test channels remain
isolated, late results from a prior scope are discarded, cursor-only progress
still requires confirmation, and stored evidence identifies the source channel
without retaining absolute paths. Discovery is cancellable, rejects links and
non-regular log entries, stops at a fixed file limit, and shows safe read
warnings instead of reporting a false clean success.

## Rollback

To return to v0.2 behavior for an existing custom profile, enable advanced
templates. To return to generated defaults without deleting template source,
disable advanced templates. Restoring generated wording removes only the
selected mission-giver/title-or-description override.
