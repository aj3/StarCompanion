# StarCompanion known limitations

**Review date:** 2026-09-13

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
- Tests use synthetic P4K, log, settings, and ownership fixtures. A reviewed
  aggregate-only LIVE provider snapshot detects build drift, while optional
  proprietary real-build checks skip when local fixtures are absent. HOTFIX has
  not yet been validated against an installed real archive on the review host.
- LIVE `1.0.191.55227` exposes no reviewed direct ace-pilot, turret-count, or
  engagement fields. Those independent capabilities remain unavailable rather
  than being guessed from record names or shared mission descriptions.
- New G6 mission-detail, title-tag, route, and mining-label settings default off
  when profiles migrate to schema 6. Only explicitly enabled evidenced values
  render, and a selected group remains silent when its provider or stock text
  has no evidence for the build.
- Route and mining labels preserve direct stock variables and one reviewed
  level of nested mission-variable indirection. Deeper/cyclic or ambiguous
  indirection remains suppressed. Numeric mining signatures are separately
  sourced legacy data and remain opt-in and exact-build/key/stock bounded.
- Cache schema 9 is intentionally incompatible with older generated caches.
  Re-import the selected local channel to rebuild; personal wording, ownership,
  and settings remain in their separate stores.
- Frozen Windows and Ubuntu artifacts are built independently because native
  PyInstaller executables are not cross-platform. Ubuntu CI verifies its own
  artifact; desktop integration is not promised for every Linux distribution.
- The GUI is English-first. A strict offline catalog, independent locale
  preference, and French shell preview are present, but complete feature-page
  translations require native-language review. Installed Star Citizen
  languages remain strictly isolated local data sources.
- Structured wording labels are intentionally limited to 48 trimmed plain-text
  characters. Markup, escapes, controls, and bidirectional overrides are
  rejected; use the explicitly enabled sandboxed template editor only when the
  validated label/order controls cannot express the desired result.
