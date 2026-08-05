# Sprint C6 GUI architecture

## Scope boundary

C6 modernizes how the verified C0--C5 features are presented. It does not
replace their state, extraction, provider, planning, persistence, signing, or
write paths. Existing page widgets remain the owners of feature behavior and
continue to communicate through `AppState`.

The shell may:

- arrange existing pages;
- describe pages and expose navigation;
- show read-only profile, game, and data context;
- apply semantic visual tokens; and
- expose existing profile and theme actions.

The shell must not perform archive work, derive mission data, render contract
values, create operation plans, or write game files.

## Interface audit

The pre-C6 interface had strong safety language, semantic color tokens, and
complete access to the core workflows. The limiting issues were structural:

- six peer-level tabs gave common tasks and advanced diagnostics equal weight;
- long pages expanded without a consistent readable content width;
- page identity and purpose changed from tab to tab;
- the active profile, detected channel, and loaded data set were not visible
  outside the Start page;
- profile and appearance actions were menu-only; and
- generic font resolution was inconsistent in Windows/offscreen rendering.

## Navigation architecture

The shell groups feature pages by user intent; G1/G2 add new projections over
the same verified backends:

```text
Workspace
  Overview             Start here
  Contract content     What to show
  Presentation         Appearance
  Blueprints           Blueprint ownership

Advanced
  Custom wording       Advanced: custom wording
  String editor        Advanced: strings
  Data & provenance    Data & provenance

Tools
  Backup & recovery    Backup and recovery
  Settings & help      Settings and help
```

The left rail is persistent. Each destination has a stable page key, a short
navigation label, a descriptive header, and an `Alt+number` shortcut. The
legacy tab labels remain available through the compatibility stack so current
integration coverage and extensions do not need to change in the first C6
slice.

## Visual system

The existing light and dark semantic palettes remain authoritative. C6 adds
component roles rather than page-specific colors:

- **Canvas:** quiet workspace behind feature content.
- **Surface:** navigation, header, status, and cards.
- **Raised surface:** controls and compact context badges.
- **Accent:** current navigation and the one primary action.
- **Danger:** actions that can write to or restore game files.
- **Muted text:** descriptions, provenance context, and security posture.

Spacing continues to use the 4/8/12/16/24/32 rhythm and radii use the shared
0/3/6/10 scale. Segoe UI Variable and Cascadia Mono are preferred on Windows,
with generic fallbacks for Linux and CI. Feature pages that read top-to-bottom
use a bounded, scrolling viewport; editor and comparison pages retain flexible
full-height layouts.

## Persistent context and trust

The header displays only local, already-known state:

- selected profile;
- detected game channel/version; and
- loaded contract count.

The status strip makes the release posture continuously visible: offline by
design, no telemetry, and confirmation required before writes. These are
descriptions of existing core guarantees, not new GUI-only enforcement.

## Overview dashboard

The second C6 slice replaces the internal three-step presentation with a
readiness dashboard while retaining the same `StartTab` operation methods and
controls. A single hero explains the next safe action. Four semantic status
cards report the game installation, contract index, local enhancement provider,
and quick presentation preset. The cards use existing state and capability
objects; they do not re-evaluate archive or provider data.

`components.py` provides the shared vocabulary:

- `StatusCard` for neutral, informational, ready, degraded, and failed states;
- `EmptyState` for data that has not been loaded or does not exist;
- `NoticeBanner` for recoverable inline information, warnings, and errors; and
- `DashboardHero` for readiness plus the page's guarded primary action.

Every component is styled through semantic properties and the common palette.
No component contains a color or performs business logic.

## UI preference separation

Interface state now uses the existing atomic, portable C5 preference file
through a GUI-only typed adapter. `ui_schema: 1`, `theme`, and `last_page` are
stored independently from render profiles. Loading a render profile therefore
cannot alter the application theme or navigation.

Existing preference files with only `theme` are migrated additively while all
unrelated allowlisted keys are preserved. Duplicate JSON keys, invalid fields,
links, unreadable files, and future/unsupported schemas fall back safely. The
GUI reports the problem inline and refuses to overwrite the source file.
`Profile.appearance` remains readable for backward compatibility and supplies
only the first migration fallback; it no longer drives the running interface.

## High-DPI screenshot regression

The screenshot test starts a fresh Qt process at 100%, 150%, and 200% scaling,
captures an actual PNG, and verifies:

- exact logical and physical canvas dimensions;
- device-pixel ratio;
- major shell, dashboard, and card geometry against a versioned JSON baseline;
- exact Windows rectangle geometry plus reviewed, bounded Ubuntu horizontal
  and vertical font-metric variance, with strict per-rectangle overrides where
  a substituted font deterministically changes page flow;
- process-isolated core, shared-Qt, and screenshot CI domains so Qt platform
  teardown is deterministic on both Windows and Ubuntu;
- monotonic, non-nested GUI job waiters with bounded cancellation;
- the presence of semantic canvas, surface, and accent colors; and
- a non-trivial encoded image.

Native menu metrics are excluded because Qt delegates them to each operating
system. Text raster hashes are also intentionally excluded: Windows' offscreen
Qt plugin exposes no fonts, and platform font rasterization would make a full
pixel hash both brittle and misleading. Interactive Windows renders remain a
separate visual-review gate.

## Settings and provenance pages

The Contract Content, Presentation, and Data & provenance pages now use the
same component vocabulary as Overview:

- Contract Content groups typed output toggles into described `ToggleRow`
  controls and reports enabled-field plus local-reward coverage aggregates.
- Presentation summarizes its active text, title, and list-length choices,
  then presents the existing controls in full-width `SectionCard` regions.
  The single-column structure is deliberate: it remains readable at the
  minimum window size, with keyboard zoom, and with longer translated text.
- Data & provenance reports contract, mission-giver, localization-key, and
  attached-evidence aggregates. Each cached `ProviderCapability` becomes a
  `ProviderHealthCard` showing provider/build identity, status, facts,
  enhanced contracts, evidence, reward-fact coverage, unmatched count, and
  the provider's own diagnostic. Empty capability state is explicit.

All values are direct projections from `AppState`, `Profile`, `ContractSet`,
and `ProviderCapability`. The GUI does not infer provider success, recalculate
coverage, inspect archives, or alter rendering behavior.

## Accessibility contract

- `Alt+1` through `Alt+9` open stable destinations. Sidebar arrows move and
  activate selection; Home/End jump to its bounds.
- Explicit tab order follows visual order within every modernized settings
  page, while native Space/Enter behavior remains intact.
- Buttons, selectors, editors, toggles, status cards, metrics, errors, and page
  roots publish accessible names and descriptions. Tests query Qt's real
  `QAccessibleInterface`, not only widget properties.
- Every interactive control family has a visible semantic focus border.
- Automated WCAG contrast tests require at least 4.5:1 for normal semantic
  text pairs and 3:1 for focus indicators in both themes.
- Structural screenshots cover Overview and every modernized page at 100%,
  150%, and 200%. Custom Wording and Manual Apply also have automated
  1040×680 reflow checks that reject horizontal clipping.

## Wording, operation-plan, and recovery workspaces

Custom Wording retains the existing profile-template mutations and live
renderer. It now presents mission-giver scope, selected title/description
state, active custom-scope count, preview length, safe generated state, and
inline template failure through the same metrics, sections, and semantic
notices used elsewhere. Its editor and preview stay side by side on desktop
and stack inside the vertical viewport at the minimum supported width.

Manual Apply now renders the C3 `InjectionPlan` rather than a second GUI-only
summary. The read-only preparation path:

1. loads the selected effective target and merge/overwrite baseline;
2. calls core `build_operation_plan` for add/change/remove/unchanged/skipped
   classification and validation;
3. attaches generated-profile source winners through the C3 `SourceGraph`;
4. binds the target path/fingerprint, baseline/result hashes, channel,
   language, mode, and source precedence into the serialized plan identity;
5. filters the displayed outcomes without mutating the plan; and
6. optionally exports that exact plan through `InjectionPlan.save`.

A confirmed apply passes that reviewed plan and target fingerprint back to
core `apply`, with its existing backup, atomic replacement, output-hash
verification, and `TransactionJournal`. Input changes invalidate the active
plan. External target changes are rejected before a journal or replacement is
created.

The recovery surface reads `TransactionJournal.inspect` and
`last_operation`; it does not invent a recovery decision. A target matching a
known before/after state can be explicitly finalized. An unknown target state
blocks apply, restore, and automatic recovery. A selected rollback is bound to
the previewed target and backup fingerprints, preserves the current target as
a new recovery backup, journals the replace, and verifies the final hash.
Responsive splitters preserve full-width diff and recovery views by stacking
inside the vertical viewport below 1180 pixels.

## Acceptance criteria for the first shell

- All default and expert pages remain reachable in their existing order.
- Existing page objects, signals, safe confirmations, cancellation, and clean
  shutdown behavior are unchanged.
- Navigation selection, header identity, and persistent context stay in sync.
- Both themes use identical selectors and semantic tokens.
- The shell works at the minimum supported window size without horizontal
  page scrolling.
- Focused GUI/theme tests and the full C0--C5 test suite remain green.

## G1 advanced string editor

The shell now hosts a virtualized string editor backed by the C3 source graph
and operation plan. Its immutable row projection carries stock, generated,
user-winner, plan-outcome, validation, and provider-evidence state; the proxy
performs combined searches and filters without reopening source files. Complete
values and provenance use a tabbed inspector to remain readable at the standard
window height.

Edits are a bounded in-memory command history until the user explicitly saves.
One aggregate background command updates the channel/language `user.ini`, after
checking that its saved baseline has not changed externally. Background jobs
are cancellation-aware, joined during shutdown, and guarded against late timer
events during teardown. The loaded user layer is also the layer consumed by
Overview and Manual Apply, preserving the C0–C5 confirmation, fingerprint,
backup, and rollback boundary.

## G2 operations and local administration

Overview adds background installed-channel discovery and an explicit selector.
Selecting a channel republishes the existing target state, which invalidates
and reloads channel-scoped user wording without mixing caches or ownership.
Discovery never contacts the launcher or a remote service.

The Blueprints page is a virtual read-only projection of C4 `build_catalog`
and `query_blueprints`. Search and filters are in memory. Ownership load,
incremental log scan, revision-checked save, and validated backup recovery use
owned Qt workers; the scan preview must be confirmed before cursors or
acquisition evidence are persisted. Loading is lazy-on-page-open so ordinary
target edits do not start unrelated disk work.

Backup & recovery promotes the existing fingerprint-bound, transaction-
journaled restore surface into default navigation. Settings & help adds:

- built-in and external output-profile actions, still separate from UI state;
- C5 manifest-verified settings archive export, preview, confirmed import, and
  interrupted-restore recovery;
- redacted diagnostics built in the background and displayed verbatim before
  an explicit export; and
- bundled help searched entirely in memory.

Blueprints and Settings & help have structural screenshot baselines at 100%,
150%, and 200% scaling. All active G0–G2 jobs are cancelled/joined on window
close, and completed job wrappers are deleted after their QThreads stop.
