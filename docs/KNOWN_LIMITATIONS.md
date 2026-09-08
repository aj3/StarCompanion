# StarCompanion known limitations

**Review date:** 2026-08-05

- The v0.2.0 Windows artifacts are intentionally unsigned by explicit
  publication approval. Windows may display Microsoft Defender SmartScreen and
  unknown-publisher warnings. The release provides independently verified
  SHA-256 values for integrity, but hashes do not establish publisher identity.
  Do not disable SmartScreen; proceed only after downloading from the official
  release and matching the complete published hash.
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
- Frozen Windows and Ubuntu artifacts are built independently because native
  PyInstaller executables are not cross-platform. Ubuntu CI verifies its own
  artifact; desktop integration is not promised for every Linux distribution.
- The GUI is localized in English. Installed Star Citizen localization
  languages remain strictly isolated and supported as local data sources, but
  application-interface translation is deferred.
- Structured wording labels are intentionally limited to 48 trimmed plain-text
  characters. Markup, escapes, controls, and bidirectional overrides are
  rejected; use the explicitly enabled sandboxed template editor only when the
  validated label/order controls cannot express the desired result.
