# StarCompanion v0.2.0 — C6/G1/G2 GUI release

> [!WARNING]
> **The v0.2.0 Windows executables are intentionally unsigned.** Windows cannot
> verify the publisher and Microsoft Defender SmartScreen may display
> **Windows protected your PC**. Download only from the official GitHub release,
> verify the SHA-256 value before running it, and use **More info > Run anyway**
> only when the value matches the release table. Do not disable SmartScreen.

StarCompanion v0.2.0 delivers the professional desktop workflow over the
security-reviewed C0-C5 core. The application remains local-first: it has no
updater, telemetry, analytics, crash upload, remote configuration, or automatic
data download.

## Highlights

- A polished, keyboard-accessible application shell and Overview dashboard.
- Modern Contract Content, Presentation, Data & Provenance, Custom Wording, and
  guarded Manual Apply experiences.
- A virtualized advanced string editor with fast local search/filtering,
  stock/merged/rendered provenance, undo/redo, safe multi-reset, and debounced
  validation.
- Guided installed-channel discovery with strict channel/language isolation.
- A local blueprint ownership tracker backed by the C4 catalog and incremental
  log scanner.
- Guarded backup browsing and restore using fingerprints, a recovery backup,
  journaled atomic replacement, and final digest verification.
- Profile management, validated settings portability, inspect-before-export
  redacted diagnostics, searchable bundled help, and high-DPI screenshot gates.
- Native one-file GUI and CLI artifacts for Windows and Ubuntu.

## Security and privacy assurance

- No C0-C5 business-logic safety gate was removed or bypassed.
- The source graph and serialized C3 operation plan remain the apply authority;
  GUI views do not write generated output directly.
- Archive extraction, channel discovery, ownership scans, settings
  import/export, and diagnostics I/O run in cancellable background operations.
- Runtime source contains no HTTP client, URL-fetching, QtNetwork, or unguarded
  socket import. The two disclosed external URLs are user-clicked/reference-only
  and are not application data sources.
- Shareable diagnostics exclude absolute paths, usernames, logs, ownership,
  game strings, and user-authored values.
- Dependency locks remain exact and hash-verified. The CycloneDX 1.6 runtime
  SBOM is regenerated for version 0.2.0 and release builds install from an
  offline wheelhouse.
- Frozen GUI launch and the complete packaged preview/apply/rollback,
  ownership-recovery, settings-portability, and diagnostics workflow run with
  Python IPv4/IPv6 sockets and DNS denied on both release platforms.

## Artifacts

The release candidate produces these unsigned sets:

- `StarCompanion-windows-unsigned`: `StarCompanion.exe`,
  `starcompanion-cli.exe`, platform manifest, SBOM, LICENSE, and NOTICE.
- `StarCompanion-ubuntu-unsigned`: `StarCompanion`, `starcompanion-cli`,
  platform manifest, SBOM, LICENSE, and NOTICE.

Windows artifacts may later be Authenticode-signed only through the protected,
manually approved signing workflow. Ubuntu artifacts and ordinary CI artifacts
remain unsigned. Hashes in `release-manifest.json` establish integrity, not
publisher identity.

### Verify a downloaded file

The public release notes list the independently verified SHA-256 value for each
asset. On Windows, open PowerShell in the download directory and run:

```powershell
Get-FileHash -Algorithm SHA256 .\StarCompanion.exe
Get-FileHash -Algorithm SHA256 .\starcompanion-cli.exe
```

On Ubuntu, run:

```bash
sha256sum ./StarCompanion ./starcompanion-cli
```

Compare the entire 64-character value with the corresponding value in the
GitHub release table. Do not run the file if any character differs. A matching
hash establishes that the download matches the reviewed release artifact; it
does not provide the publisher identity that Authenticode would provide.

## Upgrade and rollback

v0.2.0 uses the existing C5 portable schemas and channel-scoped stores. Back up
portable settings before upgrading if desired; no automatic migration of game
files occurs. A generated localization change is still reversible through the
target-scoped backup workflow. To roll back the application itself, close it and
replace the executable with v0.1.0; user data is not deleted automatically.

## Publication status

Verified unsigned publication was explicitly approved on 2026-08-05. Tag
`v0.2.0` and the public GitHub release must still be created only from the final
merge commit after required CI and final artifact inspection complete.
