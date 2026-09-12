# G6 typed presentation architecture

Sprint G6 presents locally extracted facts without weakening the C0–C5 write
boundary. The first completed slice covers mission classification, difficulty,
spawn, ace, turret, and engagement facts. Route, mining, entity tags,
favorites, and the opt-in legacy wording pack remain in later G6 slices.

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
4. Cache schema 7 interns tactical evidence with existing provider evidence.
   Loading performs the same type, bounds, enum, and evidence-reference checks.
5. Profile schema 3 stores independent fact toggles, description-detail
   visibility, and the typed title Tag Builder. Migration leaves every new
   control off so an existing profile's rendered output does not change.
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

HOTFIX validation remains externally blocked until a local HOTFIX archive is
installed. It must be run independently and cannot be substituted with LIVE.
