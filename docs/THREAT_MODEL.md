# StarCompanion core threat model

**Version:** 1 (Sprint C5)
**Review date:** 2026-08-04

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
| Local INI/JSON/CSV imports | User-selected but potentially hostile | Size/count/depth/schema limits, duplicate rejection, exact scopes, value validation, no remote retrieval |
| Settings ZIP | Fully untrusted portable input | No encrypted/path-traversing input, allowlisted paths/kinds/preferences, count/size/ratio limits, ZIP CRC plus manifest SHA-256, preview, conflict authorization, crash journal and transactional rollback |
| Game override | May change outside StarCompanion | Fingerprinted operation plan, pre-write recheck, backup, journal, atomic replacement, verified result and rollback |
| Per-channel data | Different channels/languages must never mix | Supported-channel allowlist and normalized scopes for caches, overrides, language packs, ownership, backups, and transactions |
| Archive helper process | May crash, hang, or be cancelled | Parent-owned file artifacts, bounded cancellation/termination, validated result format, parent cleanup |
| Diagnostics | Intended to be shareable | Aggregate counts/status only; excludes paths, usernames, values, logs, ownership, and game strings |
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

## Explicit non-goals

This design does not defend against an administrator or malware already able to
modify StarCompanion's executable, memory, or all user files. SHA-256 inside a
settings archive detects inconsistency, not malicious authorship. No supported
public CIG signature scheme is available for these entries. The offline guard
constrains StarCompanion's Python network surface; it is not a host firewall.

Security reports should use synthetic reproducers whenever possible. Do not
attach real `Game.log`, `Data.p4k`, ownership state, or unredacted strings.
