# StarCompanion core threat model

**Version:** 6 (G7 translation and evidence-bound presentation closure)
**Review date:** 2026-09-14

## Security objectives

StarCompanion must preserve the user's game installation, personal overrides,
ownership evidence, and portable settings while operating without telemetry,
automatic updates, or required network access. Read-only preview must not
create game files. Every game/settings mutation must be explicit, scoped,
validated, atomic where the platform permits it, and recoverable.

The sensitive local assets are `user.ini`, ownership state, settings archives,
backups, and the selected game override. Complete game logs, localization
strings, usernames, and absolute paths must not enter diagnostics or releases.

## Trust boundaries and controls

| Boundary | Assumption | Principal controls |
|---|---|---|
| `Data.p4k` | Large and possibly truncated/corrupt; not an authenticity root | Read-only handle, bounded processing, compression and exact-length validation, ZIP CRC enforcement, narrowly classified CIG method-100 warning |
| Local provider validation | Proprietary graph data and paths must not enter source control or diagnostics | Exact `Data/Game2.dcb` filter, parent-owned temporary extraction, LIVE/HOTFIX allowlist, bounded/cycle-aware graph walks, aggregate-only snapshot schema, and a fail-closed UUID/path/localization redaction gate |
| Local game install discovery | Several channel folders may be stale, inaccessible, empty, or non-regular | Cancellable background discovery, supported-channel validation, non-empty regular-file checks, captured modification-time ranking with deterministic size/path tie-breaks, and visible automatic/manual selection evidence |
| Local INI/JSON/CSV imports | User-selected but potentially hostile | Size/count/depth/schema limits, duplicate rejection, exact scopes, value validation, no remote retrieval |
| Settings ZIP | Fully untrusted portable input | No encrypted/path-traversing input, allowlisted paths/kinds/preferences, count/size/ratio limits, ZIP CRC plus manifest SHA-256, preview, conflict authorization, link/junction revalidation, crash journal and transactional rollback |
| User fallback JSON | Fully untrusted local input | Exact schema, duplicate-key/depth/count/length/size limits, unresolved-key allowlist, build/language binding, explicit authored text only |
| Game override | May change outside StarCompanion | Fingerprinted operation plan, pre-write recheck, backup, journal, atomic replacement, verified result and rollback |
| `USER.cfg` and restore-to-stock | Small game control files may contain unrelated settings, unusual supported encodings, links, or external changes | Exact install-derived targets, 1 MiB cap, ordinary-file check, archive-verified language, last-effective-line update, encoding/newline preservation, reviewed target and hash binding, target-scoped backup/journal, atomic write or intentional removal, and verified final state |
| Application data root | A custom/synchronized destination may be hostile, linked, replaced, concurrent, or only partly copied after a crash | Absolute non-root validation, link/junction checks before every write, allowlisted count/byte-bounded copy, double hash/conflict review, process lock, activation marker/config written last, source retained, restart-only switch, and synchronization warning |
| Per-channel data | Different channels/languages must never mix | Supported-channel allowlist and normalized scopes for caches, overrides, language packs, ownership, backups, and transactions; the only cross-channel exception is an explicit LIVE-HOTFIX ownership scope, whose automatic log discovery is limited to those sibling production folders |
| Local game logs | Logs can be huge, malformed, rapidly rotated, private, linked outside the install, or selected under the wrong channel | Incremental bounded reads, line/file-count limits, cancellation during discovery and scanning, file identities and prefix checks, exact acquisition grammar, scope-matched automatic discovery, link/reparse and non-regular entry rejection, source-channel plus sanitized basename evidence, and path-redacted errors/warnings |
| Archive helper process | May crash, hang, or be cancelled | Parent-owned file artifacts, bounded cancellation/termination, validated result format, parent cleanup |
| Diagnostics | Intended to be shareable | Aggregate counts/status only; excludes paths, usernames, values, logs, ownership, and game strings |
| GUI administration | User-selected paths and long-running local work cross the event loop/worker boundary | Background jobs own discovery, archive reading, ownership scanning, settings portability, and diagnostics I/O; model snapshots cross back to Qt; shutdown requests cancellation and waits boundedly |
| Advanced string editor | Large local string graphs and bulk edits could freeze, accidentally broaden a write, or lose personal text during replacement | Virtualized projection, debounced in-memory validation, model-level undo/redo, explicit multi-select reset, the unchanged serialized C3 operation plan as the only apply boundary, and separate capped collision-resistant `user.ini` snapshots before changed saves |
| Clipboard and window close | Filtered exports could leak hidden evidence or trigger spreadsheet formulas; closing could lose a pending debounced edit | Visible-row-only capped TSV, delimiter removal, formula neutralization, excluded provenance, save/discard/cancel prompt, debounce flush before background save, and open-window retention after save failure |
| Event viewer and interface catalogs | Diagnostic UI could retain private paths/values; translated text could be incomplete, oversized, hostile, or silently fetched | Fixed-level/key 500-record memory ring, pre-storage identifier redaction, per-detail/export caps, explicit raw-log/string/value exclusions, atomic reviewed export, deterministic conservative source inventory, placeholder-safe pseudo rendering, complete bounded bundled catalogs, English fallback, no catalog discovery/network, and game-language independence |
| Tactical and route presentation | Local fields or stock mission variables may be malformed, ambiguous, unlocalized, oversized, or unsupported by one build | Fixed fact and route-family vocabularies, per-fact type/range checks, exact mission-token parsing, description-variant intersection, existing-route suppression, required source evidence, contradictory-value suppression, control/markup bounds, independent provider status, profile-default off migration, complete-tag length budget, and provenance only for rendered facts or variables |
| Legacy presentation pack | Historical public wording may be stale, ambiguous, changed upstream, or inappropriate for another build | Default-off activation, pinned review commits recorded as evidence only, no runtime network access, exact build/key/stock-value or whole-value SHA-256 gates, local-stock journal transforms, collision/validation suppression, and cache-schema invalidation |
| Backup browser | A listed file can be replaced or redirected before restore | Target-scoped ordinary-file filtering, preview fingerprints, pre-write revalidation, preservation of the current target, journaled atomic replacement, and final digest verification |
| Dependencies/release | Third-party code or signing credentials may be compromised | Exact pins and hashes, offline wheelhouse build, vulnerability audit, CycloneDX SBOM, license/notice verification, frozen offline smoke, protected manual signing environment, thumbprint pin, timestamp and signature verification |

StarCompanion does not execute imported content, follow archive paths, invoke a
shell with imported values, contact a translation service, or treat community
data as authoritative game data.

## Attacker models

- A malformed or intentionally hostile P4K, INI, JSON, CSV, ZIP, log, or cache.
- Another local StarCompanion process scanning or writing the same scope.
- A crash or forced termination between staging, backup, and replacement.
- Accidental selection of the wrong channel, language, backup, or settings file.
- A user sharing diagnostics without realizing local data could be sensitive.
- A hostile settings archive, renamed backup, or rapid local file replacement
  selected through the GUI.
- A very large source graph or ownership log used to exhaust the GUI event loop.

## Explicit non-goals

This design does not defend against an administrator or malware already able to
modify StarCompanion's executable, memory, or all user files. SHA-256 inside a
settings archive detects inconsistency, not malicious authorship. No supported
public CIG signature scheme is available for these entries. The offline guard
constrains StarCompanion's Python network surface; it is not a host firewall.

Security reports should use synthetic reproducers whenever possible. Do not
attach real `Game.log`, `Data.p4k`, ownership state, or unredacted strings.
