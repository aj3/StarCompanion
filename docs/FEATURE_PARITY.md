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
| Installed game-language discovery and selection | **Complete** | Overview discovers archive languages in a cancellable worker, visibly selects one, and scopes caches, edits, plans, and overrides by channel/language. |
| Community game-language overlays | **Safe equivalent** | User-selected local INI import only; no background download or trust-on-first-use remote data. |
| Multi-source merge with personal edits last | **Safe equivalent** | Fixed, inspectable `stock → language → ordered imports → generated → user.ini` precedence, conflict report, and provenance. The safety-critical user layer cannot be dragged below another source. |
| Per-channel persistent edits across game patches | **Complete** | Per-channel/per-language `user.ini`, transactional save, import/export, and bounded model history. |
| Preview, validate, apply, backup, rollback, and crash recovery | **Complete** | One fingerprint-bound C3 operation plan drives CLI and GUI; writes are confirmed, atomic, and journaled. |
| Capped localization-backup retention and separate rotating `user.ini` snapshots | **Complete** | Profile/CLI-configurable target backup retention is exposed in the GUI and prunes only recognized unchanged restore points after verified writes. Every changed `user.ini` gets a separate bounded collision-resistant snapshot; no-op saves create none, and cleanup failure is reported without misreporting the committed edit as failed. |
| Clear the loose localization override to return to stock | **Complete** | G7 provides one-action preview and confirmation, target fingerprinting, backup, journaled removal, crash recovery, and final absence verification. |
| Safely activate the chosen game language in `USER.cfg` | **Complete** | G7 verifies the language from the archive, preserves unrelated settings/encoding/line endings, and uses a fingerprint-bound backup-first atomic plan in GUI and CLI. |
| Declarative fixes for known CIG data defects | **Partial** | G5 provides exact-build/provider/record/field/value correction records with provenance and fail-closed drift tests. No real-build correction is shipped until a specific defect and replacement are independently evidenced. |
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
| Mission type and difficulty details | **Complete** | Typed G5 facts pass through cache schema 10 to independent G6 title/description controls with rendered-only evidence. LIVE aggregate validation is green. |
| Friendly and hostile spawn summaries | **Complete** | Bounded graph joins, independent diagnostics, typed G6 controls, title tags, description details, and LIVE aggregate validation are complete. |
| Ace-pilot `[ACE]` and uncertain `[ACE?]` markers | **Complete** | Direct booleans and probabilities have distinct typed markers and provenance. The current LIVE build exposes neither, so the control remains silent instead of guessing. |
| Turret counts and engagement details | **Complete** | Typed controls and bounded presentation cover direct provider facts. The current LIVE build reports the provider unavailable because it has no reviewed fields. |
| Hauling/delivery/courier routes and title shortening | **Complete** | Exact stock endpoint tokens, description-variant intersection, append/replace modes, typed display choices, bounded whole-unit rendering, and rendered-only provenance are synthetic- and LIVE-validated. |
| Battaglia/asteroid resource-signature tags | **Complete** | Exact Battaglia resource tokens use local evidence; the separate 26-value numeric pack is default-off, source-attributed, exact-build/key/stock bounded, and fails closed on drift. |
| Ship statistics and component summaries | **Complete** | Default-off, bounded vehicle mass/cargo/crew and component type/size/grade/class tags use only unambiguous evidenced G5 values. |
| Ship-component and ship-weapon statistics | **Complete** | Typed size, damage, rate, projectile speed, range, and strict CS/EM/IR missile notation retain per-value evidence and whole-tag limits. |
| FPS-weapon statistics | **Complete** | Damage, rate, magazine capacity, and effective range are typed, bounded, independently selectable, and provenance-bearing. |
| Medical-consumable effects | **Complete** | The reviewed health, repair, dose, overdose, and toxicity fields are typed, bounded, independently selectable, and fail closed when absent. |
| Commodity and crafting cross-references | **Complete** | Independent providers, real-record resource/output joins, drift diagnostics, and LIVE aggregate regressions are complete. Crafting output labels are deliberately excluded from name mutation. |
| Journal/discovery enhancements and mining compendium | **Partial** | G7 completes exact-stock mining-compendium regrouping and refueling tips. Broader evidence-backed journal/discovery record cross-references and presentation remain. |
| Provider capability, drift, and per-value evidence | **Complete** | Mission rewards, eight entity providers, and three tactical providers have isolated status/drift reporting, evidence-bearing values, LIVE aggregate checks, and fail-closed exact-build corrections. |

## Editor, presentation, and ownership

| Reference outcome | StarCompanion status | Evidence or remaining work |
|---|---|---|
| Fast full-string search, sorting, and domain/source/state/provider filters | **Complete** | Virtual model/view table and cached search projection; 50,000-row regression. |
| Per-column filters | **Complete** | Seven independent cached column filters combine in one proxy invalidation without creating widgets per row; the 50,000-row regression remains bounded. |
| Inline custom edit with stock/merged/rendered provenance | **Safe equivalent** | Complete values are edited in a debounced inspector instead of a narrow table cell; model undo/redo and safe multi-reset are already present. |
| Filtered-row clipboard export | **Complete** | G7 copies only the bounded visible projection, excludes private provenance/hidden rows, removes row delimiters, and neutralizes spreadsheet formulas. |
| Styled loc-token/markup preview | **Complete** | The visual renderer strictly escapes untrusted text, interprets only balanced allowlisted game tags and recognized mission tokens, creates no links/resources, and caps previews at 32 KiB. |
| Configurable structured labels, ordering, numeric format, tags, and expert templates | **Complete** | G3 schema v2 exposes all nine labels; expert templates remain explicit and sandboxed. |
| Category-level enhancement toggles | **Complete** | G7 coarse controls update the existing typed reward, tactical, route/resource, entity, and legacy fields while individual controls and diagnostics remain available. |
| Stat-block placement above or below stock text | **Complete** | Profile schema 6 exposes a strict above/below choice; rendered output still passes final validation. |
| General Tag Builder for component, missile, weapon, commodity, and mission-title formats | **Complete** | Mission and entity/item tags are typed, independently selectable, complete-unit bounded, and backed by strict local name/attribute evidence. |
| Blueprint catalog, owned/unowned search, reward source, category, and acquisition queries | **Complete** | Stable C4 identities and read-only backend queries. |
| Incremental current/rotated log scan | **Complete** | Bounded, cancellable, rotation/truncation-aware scan with confirmation before cursor/evidence save. |
| Review both LIVE and HOTFIX logs | **Complete** | New GUI preferences review both production siblings by default; the visible control can separate them, the CLI flag selects the shared scope explicitly, and test channels cannot enter linked discovery. |
| Manual owned/unowned shuttle and multi-select | **Complete** | G4 previews one multi-select command and rechecks the ownership revision and per-item before-state in the worker before saving. |
| Blueprint Mission/Type/Class/Size/Grade filters | **Complete** | Catalog metadata and the virtual GUI table expose and combine Mission, Type, Class, Size, Grade, ownership, category, reward-source, and search filters. |
| Full rescan, unresolved resolution, and ownership import/export in GUI | **Complete** | Guarded background GUI workflows provide explicit full rescan, exact unresolved resolution, bounded exact-match import, and revision-checked JSON/CSV export. |
| Repair names altered by another localization editor | **Complete** | Evidence-backed catalog aliases normalize known bracket markers and whitespace, accept only an unambiguous exact normalized identity, and never fuzzy-mark ownership. |
| Owned marker in generated blueprint lists | **Complete** | The selected channel-scoped ownership snapshot is joined into a render-only contract copy, leaving cached contract data immutable. |
| Per-key INI conflict reconciliation | **Complete** | G4 loads bounded imports in a worker, requires reviewable keep/import/append/prepend/custom choices for every conflict, and rechecks the saved baseline before one provenance-aware undoable write. |
| Ship favorites and explicit ASOP order | **Complete** | Evidence-backed vehicle names receive safe `*` and `NN-` prefixes through the existing channel/language-scoped user layer with model undo/redo and reviewed persistence. |
| Window/splitter/column layout persistence and reset | **Complete** | G4 stores strictly bounded geometry, splitter ratios, and table widths in a separate local-only file; off-screen positions are ignored, hidden-page ratios are deferred safely, invalid files are preserved, and explicit reset never changes portable preferences. |

## Operational interface

| Reference outcome | StarCompanion status | Evidence or remaining work |
|---|---|---|
| Guided install/channel onboarding | **Complete** | Overview discovers channels in a worker and explains readiness, trust, and next actions. |
| Two-button simple mode | **Complete** | G7 hides all detailed cards/pages and disables their shortcuts, leaving the existing Update and Undo actions plus a persistent exit to Full mode. |
| Backup browser and guarded restore | **Complete** | Target-scoped listing, fingerprint recheck, recovery backup, journal, and final verification. |
| Portable settings backup | **Safe equivalent** | Manifest- and SHA-256-verified ZIP for portable preferences and edits. Ownership has its own explicit JSON/CSV transfer instead of leaking into a general settings bundle. |
| Portable ZIP mode with beside-executable data | **Complete** | Packaged builds can preview and activate a fixed beside-executable marker/data root; installed defaults remain unchanged and cache is rebuilt locally. |
| Configurable application-data directory | **Complete** | G7 validates an absolute non-root destination and performs a bounded, allowlisted, copy-only, conflict-safe migration that activates after restart. |
| OneDrive/synchronization warning and migration | **Complete** | Synchronized roots are detected and explained; migration rejects links/concurrency, writes activation last, and never deletes the original. |
| Searchable offline FAQ/help and privacy explanation | **Complete** | Bundled help plus redacted diagnostics preview/export. |
| Unsaved-change warning on close | **Complete** | G7 offers save/discard/cancel, flushes pending debounced text before background save, and leaves the window open on save failure. |
| Active-localization indication | **Complete** | The persistent shell context and Overview warning show selected language, effective `g_language`, and stock/custom override state. |
| Replayable coach-mark tutorial | **Complete** | A non-modal focus/navigation tour is replayable from Help and Settings; completion is portable and no tour step reads or writes data. |
| Redacted real-time application log viewer | **Complete** | A 500-record local ring accepts fixed events, filters levels, redacts identifiers, caps details/exports, and explicitly excludes raw logs, strings, ownership, and exception text. |
| Localized application interface | **Safe equivalent** | A conservative inventory covers 1,323 statically identifiable GUI-facing messages/1,513 locations with placeholder-safe pseudo rendering; native feature-page translations remain external reviewed content rather than machine-generated claims. |
| Four visual themes | **Complete** | Dark, Light, Midnight, and High Contrast share one semantic stylesheet and all pass text/focus contrast gates. |
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
| Hauling title overhaul with origin/destination | **Complete** | G6 uses only exact stock route variables shared by contributing description variants. |
| Asteroid resource-signature values in scan objectives | **Complete** | Stock variables are local; numeric RS values are available only through the separately attributed, default-off, exact-build/key/stock legacy pack. |
| Shorter Hephaestanite and mining UI wording; `(Raw)` normalization | **Complete** | Four exact-key/stock rules are default-off, source-attributed, and build-bounded. |
| Illegal-item warning prefix | **Complete** | Eight exact-key/stock rules add the reviewed warning only on the reviewed build. |
| Component Type/Size/Grade and missile-type prefixes | **Complete** | Strict component path families plus typed Size/Grade/Class and allowlisted CS/EM/IR seeker facts render with local evidence. |
| Shorter multi-tool attachment names | **Complete** | Five exact-key/stock rules reproduce the reviewed short attachment outcomes and fail closed on drift. |
| Mining guide regrouping and refueling quick tips | **Complete** | Whole-value SHA-256 gates transform only local stock text; no journal corpus is committed. |

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
3. **G6 — Tag Builder and legacy presentation (complete):** tactical and entity
   tags, route-aware titles, resource labels, favorites/ASOP ordering, nested
   mission-token support, and the exact-build legacy mining pack are complete.
4. **G7 — Experience parity (complete):** language selection/activation,
   restore-to-stock, portable/custom data roots, synchronization warnings,
   filtered clipboard export, close protection, active-localization context,
   category toggles, stat placement, dedicated simple mode, coach marks, a
   redacted event viewer, four themes, complete translator inventory,
   pseudo-locale gates, bounded entity stats, and exact legacy presentation
   rules are complete. Full native-reviewed feature-page translations remain
   external contributor content.

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
