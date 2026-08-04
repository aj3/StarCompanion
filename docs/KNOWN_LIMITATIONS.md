# Core release known limitations

**Review date:** 2026-08-04

- Ordinary CI artifacts are intentionally unsigned. A manually approved
  `release-signing` environment can Authenticode-sign and RFC 3161 timestamp a
  reviewed release candidate once a publisher certificate is configured. The
  current local C5 candidate remains unsigned because no certificate is
  available; manifest hashes alone do not establish publisher identity.
- Ordinary ZIP entries require CRC-32. Current CIG-aligned method-100 entries
  can carry a demonstrably non-ZIP CRC field. StarCompanion reports this and
  requires valid Zstandard decompression plus exact declared length, but cannot
  prove publisher authenticity.
- Only LIVE, PTU, EPTU, HOTFIX, and TECH-PREVIEW are accepted. A future channel
  needs a reviewed update instead of silently becoming a new data scope.
- Language packs are local user-selected INIs. There is no translation download,
  machine translation, telemetry, synchronization, or quality endorsement.
- Settings restore is additive. Missing archive files are not deleted; replacing
  existing files requires confirmation and `--replace-existing`.
- Ownership depends on the documented CIG notification shape. Unmatched or
  ambiguous names remain unresolved instead of being guessed.
- Tests use synthetic P4K, log, settings, and ownership fixtures. Optional
  proprietary real-build checks skip when local fixtures are absent.
- The frozen release target is Windows. Core tests run on Windows and Ubuntu CI,
  but desktop integration is not promised for every Linux distribution.
