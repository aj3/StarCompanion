# G6 typed presentation architecture

Sprint G6 presents locally extracted facts without weakening the C0–C5 write
boundary. Completed slices cover mission classification, difficulty, spawn,
ace, turret, and engagement facts; nested stock-token hauling routes and
Battaglia resource labels; typed entity tags; ship favorites and ASOP ordering;
and an exact-build legacy numeric mining pack.

## Data path

1. The archive helper extracts only the selected language's `global.ini` and
   `Data/Game2.dcb` into parent-owned temporary storage.
2. One bounded `DataForgeIndex` is shared by reward and tactical providers.
   Provider failures are isolated and reported without exception text.
3. Tactical facts enter the contract model as typed `MissionDetail` values.
   Each value retains its provider, record, path, field, original scalar, and
   confidence. Duplicate equal facts merge all evidence; contradictory values
   are suppressed and degrade only their provider. Non-finite numbers, unsupported names or types, markup,
   controls, hidden formatting characters, untrimmed strings, and oversized
   strings are rejected.
4. Cache schema 10 interns tactical, nested-route, entity-tag, and legacy-pack
   evidence with existing provider evidence.
   Loading performs the same type, bounds, enum, and evidence-reference checks.
5. Profile schema 5 stores independent fact toggles, description-detail
   visibility, the typed title Tag Builder, route choices, and mining-label
   visibility. Migration leaves every new control off so an existing profile's
   rendered output does not change.
6. Rendering exposes only enabled groups. Provenance is attached to a key only
   when that key actually renders the corresponding fact. Generated tags are
   selected from a fixed vocabulary and bounded as complete units; they are
   never partially truncated.

Mission-type handling distinguishes a literal `missionType` enum from a CIG
localization reference using source-field evidence. Literal reviewed values can
be displayed. `LocalisedTypeName` and `@` references must resolve in the
selected local language; unresolved keys are suppressed and may only be filled
through the existing explicit user-authored fallback workflow.

## User controls

Contract Content has seven independent groups: mission type, difficulty,
friendly spawns, hostile spawns, ace pilot, turrets, and engagement. The
Presentation page separately controls whether enabled groups appear in the
description and which enabled groups may become title tags. Tag placement,
separator, and a 16–160 character tag budget are typed profile settings.

Hauling route titles are separately opt-in. Eligibility is limited to contract
title families for hauling, delivery, and courier missions. Endpoint variables
must come from stock descriptions, and only variables shared by every
contributing description variant are used. The user can append the route or
replace an eligible generic title, choose address/name rendering, and select
one of three plain separators. A stock title that already contains an endpoint
is left unchanged. Battaglia scan/mining labels likewise use only exact stock
`Resources` and `MineableType` variables. Both features honor the complete-unit
title budget and attach evidence only for variables that actually render.
Nested route variables are expanded one level only when every candidate stock
entry agrees on the exact mission tokens; ambiguous or empty candidates suppress
the expansion.

Entity and item tags use unambiguous local DataForge-to-localization joins.
Shared names with conflicting kinds are suppressed, and Size/Grade/Class is
shown only when every record sharing a display key agrees. A fail-closed
per-kind key-family allowlist rejects generic UI labels, cross-domain names,
and crafting-output labels before they can become localization mutations.
Crafting relationships remain available to the G5 backend but are not treated
as independently named presentation entities. Ship favorites and
two-digit `NN-` ASOP order prefixes are restricted to evidence-backed vehicle
name rows and execute through the existing channel/language-scoped `user.ini`
command model, including undo/redo and reviewed saving.

G7 extends the same evidence path to bounded vehicle, ship/FPS weapon, medical,
and commodity statistics. Component type comes only from reviewed record-path
families; missile seeker type accepts only the three reviewed DataForge enums.

The legacy numeric mining pack is disabled by default. Its 26 factual values
are attributed to a pinned historical StarStrings commit, accepted only for
build `1.0.191.55227`, and further require exact localization keys and exact
stock English names. It never fills unresolved CIG text and fails closed on a
new build or changed value.

The final default-off legacy presentation pack applies the same checks to 17
small exact item/commodity rewrites and two whole-hash-bound journal transforms.
Journal output is constructed from local stock text; the game corpus is not
stored in source control.

Provider Health remains the source of truth when a build does not expose a
field. Selecting a presentation option never manufactures a value and never
changes provider availability.

## Validation record

Synthetic tests cover graph joins, ambiguity suppression, provider exceptions,
unsafe values, cache corruption, profile migration, bounded tags, rendered-only
provenance, GUI bindings, and archive-to-domain integration.

Read-only LIVE validation on build `1.0.191.55227` processed 1,851 contracts and
5,202 localization keys. After cross-record ambiguity suppression it retained
2,900 typed details across 737 contracts: 35 mission types, 2,747
difficulty-dimension facts, 8 friendly-spawn totals, and 110 hostile-spawn
totals. All accepted values had high confidence. The
build still exposes no reviewed direct ace, turret, or engagement fields, so
the engagement provider remains unavailable rather than inferred. The
validation cache is temporary and is deleted after aggregate inspection; no
game strings or extracted records are committed.

The route/mining slice independently found 103 eligible route fragments: 89
with both sides, eight origin-only, and six destination-only. The default
72-character budget rendered 83 and safely omitted 20 complete fragments; the
longest candidate was 164 characters. Five Battaglia contracts exposed exact
resource-label variables and all five rendered. Rendering all 5,202 keys
produced no warning or skip, and 376 rendered-only token evidence links. These
are aggregate counts; the temporary cache is deleted and no extracted text is
committed.

The final read-only LIVE rerun retained 1,134 safely classified display keys:
132 commodities, 387 components, 349 FPS weapons, 12 medical items, 64
missiles, 177 ship weapons, and 13 vehicles. The 13 nested-route expansions
and all 26 exact-build legacy signatures survived cache round-trip. Rendering
the complete opt-in aggregate produced 6,362 values with zero warnings or
skips; all numeric signatures retained evidence. Generic or cross-domain
localization families were reported as suppressed coverage, not silently
accepted. The 15.1 MB temporary validation cache was deleted after this
aggregate inspection, and no game data is committed. HOTFIX validation remains
externally blocked until a local HOTFIX archive is installed; LIVE cannot stand
in for that independent channel check.
