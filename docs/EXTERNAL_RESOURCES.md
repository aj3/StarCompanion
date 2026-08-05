# External resource inventory

StarCompanion has no updater, telemetry, analytics, crash upload, remote
configuration, or automatic data download. Runtime source is checked in CI for
network-capable Python and Qt imports; the only permitted socket import is the
offline verification guard that blocks IPv4, IPv6, and DNS operations.

The application source contains these external URLs:

- `https://github.com/MrKraken/StarStrings/blob/master/src/For_Tool_Creators/contracts.ini`
  — opened in the default browser only when the user presses the GUI download
  button; StarCompanion does not retrieve it itself.
- `https://scmdb.net/` — identifies the origin/format of an SCMDB file that the
  user exports and then imports locally. It appears in documentation and CLI
  help; StarCompanion does not contact SCMDB.

Links are references, not application data sources. The stock localization and
contract information used by core workflows are extracted locally from the
user-selected Star Citizen installation and its `Data.p4k` archive.
Language packs and portable settings are selected from local files only; no
translation, synchronization, telemetry, or settings service is contacted.

Release infrastructure has one separate, opt-in network dependency: after
Foundation approval, a manually approved GitHub Actions job submits an already
tested artifact to SignPath.io for Authenticode signing and timestamping. This
happens only between GitHub and SignPath after all CI gates pass; neither
installed executable contacts the signing or timestamp services. Ordinary CI
and local builds remain unsigned and do not contact them.

Development reference only: Smart Citizen v2.3.0's Apache-2.0 source was
reviewed to confirm DataForge relationships for mission reputation and crafting
blueprint pools, and later to confirm the authoritative local-log notification
shape and visible LIVE/HOTFIX channel rule for the C4 ownership workflow.
StarCompanion has independent catalog, scanner, watermark, and persistence
implementations and does not import, execute, contact, or download Smart Citizen
at runtime. This use is also recorded in `NOTICE`.

The [dolkensp/unp4k source](https://github.com/dolkensp/unp4k) was consulted to
verify the public DataForge pointer, array, enum-table, and community P4K-reader
contracts after real-build diagnostics exposed decoder mismatches. It is an
MIT-licensed development reference only; StarCompanion does not bundle or run
unp4k or unforge.
