# StarCompanion feature-parity ledger

This is the authoritative inventory for the goal of preserving StarCompanion's
design while reaching the useful outcomes of both reference projects. A phase
is not complete because a similarly named screen exists; the outcome, safety
boundary, tests, and recovery behavior must all be present.

## Reviewed baselines

- [Smart Citizen v2.3.1](https://github.com/Osiris-DevWorks/smart-citizen/releases/tag/v2.3.1),
  commit `b103f9ea55a626b0944593b1e089bab12f2e8efa`, reviewed 2026-09-08.
- [MrKraken/StarStrings](https://github.com/MrKraken/StarStrings), commit
  `b83d58baedf71b090903b9ec34186520907271f9`, reviewed 2026-09-08 against its
  4.10 feature list.
- StarCompanion 0.3.0 G3 release candidate, built on the verified public v0.2.0
  baseline.

The reference repositories are used to identify behaviors and game-data
relationships. StarCompanion implements those behaviors in its own models and
does not execute or silently download either project's code or generated data.

## Status meanings

- **Complete** — available through StarCompanion's verified architecture.
- **Partial** — a safe foundation exists, but a visible part of the reference
  outcome is still missing.
- **Planned** — not implemented and assigned to a future phase.
- **Safe equivalent** — the user outcome is covered with a deliberately safer
  design rather than matching the reference mechanism.

## Core and data workflow

| Reference outcome | StarCompanion status | Evidence or remaining work |
|---|---|---|
| Read stock localization and DataForge data from the installed `Data.p4k` | **Complete** | Pure-Python, read-only streaming reader; cancellable helper process; build-scoped cache. |
| LIVE, HOTFIX, PTU, EPTU, and TECH-PREVIEW support | **Complete** | Installed-channel discovery and normalized per-channel state. |
| Installed game-language discovery and selection | **Partial** | C5 discovers local archive languages and isolates their caches/edits; G7 adds a visible GUI selector and explicit activation workflow. |
| Community game-language overlays | **Safe equivalent** | User-selected local INI import only; no background download or trust-on-first-use remote data. |
| Multi-source merge with personal edits last | **Safe equivalent** | Fixed, inspectable `stock → language → ordered imports → generated → user.ini` precedence, conflict report, and provenance. The safety-critical user layer cannot be dragged below another source. |
| Per-channel persistent edits across game patches | **Complete** | Per-channel/per-language `user.ini`, transactional save, import/export, and bounded model history. |
| Preview, validate, apply, backup, rollback, and crash recovery | **Complete** | One fingerprint-bound C3 operation plan drives CLI and GUI; writes are confirmed, atomic, and journaled. |
| Capped localization-backup retention and separate rotating `user.ini` snapshots | **Complete** | Profile/CLI-configurable target backup retention is exposed in the GUI and prunes only recognized unchanged restore points after verified writes. Every changed `user.ini` gets a separate bounded collision-resistant snapshot; no-op saves create none, and cleanup failure is reported without misreporting the committed edit as failed. |
| Clear the loose localization override to return to stock | **Planned** | G7 adds a one-action, backup-first, confirmation-gated vanilla workflow instead of asking users to delete a file. |
| Safely activate the chosen game language in `USER.cfg` | **Planned** | G7 adds preview, backup, confirmation, atomic recovery, unrelated-setting preservation, and encoding preservation. |
| Declarative fixes for known CIG data defects | **Planned** | G5 adds build-bounded, evidence-bearing patch records with synthetic drift tests and an audit view. |
| Freshest valid `Data.p4k` when several installs are present | **Complete** | Discovery ignores inaccessible, empty, and non-regular archives, ranks each channel by captured archive modification time with deterministic size/path tie-breaks, and visibly explains both the automatic winner and any explicit older selection. |
| Shareable localization pack | **Complete** | G4 exports and imports a bounded authored-only ZIP with a validated profile and offline rebuild instructions. The GUI keeps source scope advisory, never activates the bundled profile automatically, and never redistributes stock game strings. |
| Automatic application updater | **Safe equivalent** | Intentionally no startup network request or self-replacing installer. Releases remain user-initiated and hash/signature verified; any future update check must be opt-in and signed before it can be enabled. |
| Telemetry, remote configuration, or cloud sync | **Safe equivalent** | None. Offline behavior is enforced by network-denied packaged smoke tests. |

## Generated enhancements

| Reference outcome | StarCompanion status | Planned phase |
|---|---|---|
| Mission reputation amounts and tracks | **Complete** | C1–C3 local mission provider with field evidence. |
| Blueprint pools, chances, ranks, regional variants, caveats, and title tags | **Complete** | C1–G3 structured mission rendering. |
| Direct mission item rewards and scenario points | **Complete** | C1–G3 structured mission rendering. |
| Mission type and difficulty details | **Complete** | Typed G5 facts now pass through cache schema 7 to independent G6 title/description controls with rendered-only evidence. LIVE aggregate validation is green. |
| Friendly and hostile spawn summaries | **Complete** | Bounded graph joins, independent diagnostics, typed G6 controls, title tags, description details, and LIVE aggregate validation are complete. |
| Ace-pilot `[ACE]` and uncertain `[ACE?]` markers | **Complete** | Direct booleans and probabilities have distinct typed markers and provenance. The current LIVE build exposes neither, so the control remains silent instead of guessing. |
| Turret counts and engagement details | **Complete** | Typed controls and bounded presentation cover direct provider facts. The current LIVE build reports the provider unavailable because it has no reviewed fields. |
| Hauling/delivery/courier routes and title shortening | **Planned** | G6 presentation provider and Tag Builder. |
| Battaglia/asteroid resource-signature tags | **Planned** | G6 mission/mining provider. |
| Ship statistics and component summaries | **Partial** | G5 typed vehicle provider foundation is synthetic-tested; schema-specific fact expansion and live-build validation remain. |
| Ship-component and ship-weapon statistics | **Partial** | G5 component and dedicated ship-weapon provider facts are synthetic-tested; live schema validation remains. |
| FPS-weapon statistics | **Partial** | G5 dedicated FPS-weapon facts and isolation diagnostics are synthetic-tested; live schema validation remains. |
| Medical-consumable effects | **Partial** | G5 medical fact extraction is synthetic-tested with typed conversion-drift handling; live schema validation remains. |
| Commodity and crafting cross-references | **Partial** | Independent G5 commodity and crafting facts are synthetic-tested; reference-graph joins and live validation remain. |
| Journal/discovery enhancements and mining compendium | **Partial** | G5 journal facts are synthetic-tested; cross-references, mining facts, and G6 presentation remain. |
| Provider capability, drift, and per-value evidence | **Complete** | Mission rewards, eight entity providers, and three tactical providers have isolated status/drift reporting, evidence-bearing values, LIVE aggregate checks, and fail-closed exact-build corrections. |

## Editor, presentation, and ownership

| Reference outcome | StarCompanion status | Evidence or remaining work |
|---|---|---|
| Fast full-string search, sorting, and domain/source/state/provider filters | **Complete** | Virtual model/view table and cached search projection; 50,000-row regression. |
| Per-column filters | **Complete** | Seven independent cached column filters combine in one proxy invalidation without creating widgets per row; the 50,000-row regression remains bounded. |
| Inline custom edit with stock/merged/rendered provenance | **Safe equivalent** | Complete values are edited in a debounced inspector instead of a narrow table cell; model undo/redo and safe multi-reset are already present. |
| Filtered-row clipboard export | **Planned** | G7 adds explicit, bounded copy of the visible projection without hidden rows or private provenance fields. |
| Styled loc-token/markup preview | **Complete** | The visual renderer strictly escapes untrusted text, interprets only balanced allowlisted game tags and recognized mission tokens, creates no links/resources, and caps previews at 32 KiB. |
| Configurable structured labels, ordering, numeric format, tags, and expert templates | **Complete** | G3 schema v2 exposes all nine labels; expert templates remain explicit and sandboxed. |
| Category-level enhancement toggles | **Planned** | G7 exposes coarse, understandable enable/disable controls while retaining provider-specific capability reporting. |
| Stat-block placement above or below stock text | **Planned** | G7 adds a typed placement choice; it does not permit arbitrary execution or bypass final validation. |
| General Tag Builder for component, missile, weapon, commodity, and mission-title formats | **Partial** | Mission-title fields, order, placement, separator, and complete-tag length bounds are implemented; entity/item/commodity/missile rules remain. |
| Blueprint catalog, owned/unowned search, reward source, category, and acquisition queries | **Complete** | Stable C4 identities and read-only backend queries. |
| Incremental current/rotated log scan | **Complete** | Bounded, cancellable, rotation/truncation-aware scan with confirmation before cursor/evidence save. |
| Review both LIVE and HOTFIX logs | **Complete** | New GUI preferences review both production siblings by default; the visible control can separate them, the CLI flag selects the shared scope explicitly, and test channels cannot enter linked discovery. |
| Manual owned/unowned shuttle and multi-select | **Complete** | G4 previews one multi-select command and rechecks the ownership revision and per-item before-state in the worker before saving. |
| Blueprint Mission/Type/Class/Size/Grade filters | **Complete** | Catalog metadata and the virtual GUI table expose and combine Mission, Type, Class, Size, Grade, ownership, category, reward-source, and search filters. |
| Full rescan, unresolved resolution, and ownership import/export in GUI | **Complete** | Guarded background GUI workflows provide explicit full rescan, exact unresolved resolution, bounded exact-match import, and revision-checked JSON/CSV export. |
| Repair names altered by another localization editor | **Complete** | Evidence-backed catalog aliases normalize known bracket markers and whitespace, accept only an unambiguous exact normalized identity, and never fuzzy-mark ownership. |
| Owned marker in generated blueprint lists | **Complete** | The selected channel-scoped ownership snapshot is joined into a render-only contract copy, leaving cached contract data immutable. |
| Per-key INI conflict reconciliation | **Complete** | G4 loads bounded imports in a worker, requires reviewable keep/import/append/prepend/custom choices for every conflict, and rechecks the saved baseline before one provenance-aware undoable write. |
| Ship favorites and explicit ASOP order | **Planned** | G6 persists these as irreplaceable user data, separate from generated caches. |
| Window/splitter/column layout persistence and reset | **Complete** | G4 stores strictly bounded geometry, splitter ratios, and table widths in a separate local-only file; off-screen positions are ignored, hidden-page ratios are deferred safely, invalid files are preserved, and explicit reset never changes portable preferences. |

## Operational interface

| Reference outcome | StarCompanion status | Evidence or remaining work |
|---|---|---|
| Guided install/channel onboarding | **Complete** | Overview discovers channels in a worker and explains readiness, trust, and next actions. |
| Two-button simple mode | **Partial** | Overview is the simplified workflow, but G7 still needs a dedicated minimal mode. |
| Backup browser and guarded restore | **Complete** | Target-scoped listing, fingerprint recheck, recovery backup, journal, and final verification. |
| Portable settings backup | **Safe equivalent** | Manifest- and SHA-256-verified ZIP for portable preferences and edits. Ownership has its own explicit JSON/CSV transfer instead of leaking into a general settings bundle. |
| Portable ZIP mode with beside-executable data | **Planned** | G7 adds an explicit mode and keeps installed-mode defaults unchanged. |
| Configurable application-data directory | **Planned** | G7 adds a validated, migration-aware selector instead of requiring an environment variable. |
| OneDrive/synchronization warning and migration | **Planned** | G7 detects risky synchronized roots, explains lock/revision hazards, and offers a recoverable move without deleting the source. |
| Searchable offline FAQ/help and privacy explanation | **Complete** | Bundled help plus redacted diagnostics preview/export. |
| Unsaved-change warning on close | **Planned** | G7 distinguishes model edits from background jobs and offers save/discard/cancel without performing hidden writes. |
| Active-localization indication | **Planned** | G7 adds a persistent channel/language/override indicator or watermark so users can tell what the game will load. |
| Replayable coach-mark tutorial | **Planned** | G7 adds this only after the final workflow and controls stabilize. |
| Redacted real-time application log viewer | **Planned** | Diagnostics exist; G7 adds a bounded in-memory event log with level filter and redacted export. |
| Localized application interface | **Planned** | Game-language handling exists; G7 bundles and reviews UI translations rather than fetching them silently. |
| Four visual themes | **Partial** | Accessible dark and light themes exist; G7 adds two reviewed variants without weakening contrast/focus gates. |
| High-DPI, keyboard, focus, contrast, and screen-reader coverage | **Complete** | Structural screenshots at 100/150/200 percent and accessibility regressions. |
| Windows installer and signed release path | **Partial** | Reproducible Windows/Linux artifacts and SignPath-ready signing workflow exist; certificate approval/signing remains an external release condition. |

## Legacy StarStrings preservation

Every future legacy wording rule is opt-in and previewed, uses exact keys and
build-bounded evidence, records authored provenance, and fails closed on drift.
It must never guess text or act as an automatic fallback for an unresolved CIG
localization key.

| Legacy behavior | StarCompanion status | Planned phase |
|---|---|---|
| Blueprint title markers, pools, component context, ranks, regional variants, and caveats | **Complete** | Existing mission facts/rendering cover the content, and G4 joins exact channel-scoped ownership into the render-only contract copy. |
| Reputation, direct rewards, and scenario progress in contracts | **Complete** | Existing local provider. |
| Hauling title overhaul with origin/destination | **Planned** | G6. |
| Asteroid resource-signature values in scan objectives | **Planned** | G6. |
| Shorter Hephaestanite and mining UI wording; `(Raw)` normalization | **Planned** | G6 local presentation pack. |
| Illegal-item warning prefix | **Planned** | G6 item presentation rules. |
| Component Type/Size/Grade and missile-type prefixes | **Planned** | G5 facts, G6 Tag Builder. |
| Shorter multi-tool attachment names | **Planned** | G6 local presentation pack. |
| Mining guide regrouping and refueling quick tips | **Planned** | G5 journal facts, G6 explicitly maintained wording. |

## Phase order and remaining work

1. **G4 — Ownership and editor completion (complete):** manual ownership changes, full
   tracker import/export/rescan/resolution UI, foreign-name repair, render-plan
   ownership wiring, per-key INI reconciliation, safe shareable-pack export,
   per-column filters, safe markup preview, backup retention and user-edit
   snapshots, layout persistence, and freshest-install evidence.
2. **G5 — Local entity providers (complete):** ships, components, ship/FPS weapons,
   medical, commodities/crafting, journal facts, mission type/difficulty,
   spawn/ace/turret/engagement facts, and declarative build-scoped data fixes.
   Synthetic fixtures come first; every value has evidence and every provider
   has independent capability and drift diagnostics.
3. **G6 — Tag Builder and legacy presentation (active):** independently
   controlled tactical mission presentation and mission Tag Builder are
   complete. Continue with route-aware mission titles, configurable
   item/commodity/missile tags, favorites, mining/resource-signature features,
   and the remaining legacy wording pack under the strict boundary above.
4. **G7 — Experience parity:** dedicated simple mode, clear-localization flow,
   GUI language selection and safe `USER.cfg` activation, portable/data-root
   management with OneDrive guidance, filtered clipboard export, close-dirty
   warning, active-localization indication, category toggles, stat placement,
   localized UI, coach marks, redacted event-log viewer, and two additional
   accessible themes.

G4 began only after G3's complete local and hosted gate was green. G5 must not
start until G4's complete local gate and review are green. Later phases may
change provider and interface layers, but must continue using the C3 operation
plan, C4 isolated ownership store, C5 portability rules, background worker
boundary, and confirmation-gated filesystem writes.

## Completion rule

The combined parity goal is complete only when every row above is **Complete**
or has an explicitly accepted **Safe equivalent**, its synthetic and packaged
offline regressions pass, and no real game strings, player logs, ownership data,
or signing material is tracked or shipped.
