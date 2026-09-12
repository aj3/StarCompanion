# G5 local provider architecture

Sprint G5 reads only the player's local DataForge graph. It adds no network
client, external dataset, game-file write, or GUI-thread extraction path.

## Validated local catalog

| Provider family | Facts | Isolation boundary |
|---|---|---|
| Vehicle | name, mass, cargo, crew | Vehicle records; provider graph joins stop after four pointers |
| Component | name, size, grade, class | Reviewed ship-component category trees |
| Ship weapon | size, damage, fire rate, velocity, range | Ship/vehicle weapon records |
| FPS weapon | damage, fire rate, magazine, range | Personal/FPS weapon records |
| Medical | healing, dose, overdose, toxicity | Medical entity-class records |
| Commodity | local facts and resource composition | Commodity entity records |
| Crafting | recipe time, output, rank, resource/entity/category edges | Blueprint and legacy recipe records |
| Journal | title, body, category, discovery tag | Journal entries |
| Mission classification | type and difficulty | Contract generator fields |
| Mission spawn | friendly/hostile counts and ace facts | Approved spawn references |
| Mission engagement | turrets, distance, style | Approved engagement references |

Each provider returns its own capability report. Missing records, invalid
scalars, conflicting values, and schema drift affect only the provider that
owns the field. Cross-provider record claims are suppressed rather than
guessed.

## Evidence and graph joins

Every fact value carries the exact record ID, normalized record path, field
path, and scalar value. Referenced tactical values also retain every joining
edge from the contract to the target field. Traversal is breadth-first,
cycle-aware, de-duplicates converging paths, and has a reviewed hop limit.

Descriptions shared by more than one mission identity are never used as
tactical match keys. A unique title can still match; a shared-description-only
fact remains visible for diagnostics with low confidence but cannot be applied
to another mission.

## LIVE validation

The complete provider catalog was run read-only against installed LIVE build
`1.0.191.55227` on 2026-09-11. The committed regression is aggregate-only: it
contains counts, provider states, versions, and a schema-shape SHA-256, never
record IDs, paths, localization values, or extracted game data.

The exact-entry central-directory lookup completes in about 0.5 seconds on the
158.7 GB archive. The complete extraction, parse, provider, redaction, and
cleanup validation completed in 85.62 seconds without changing the archive.

- All eight entity providers are available. They cover 1,101 vehicle, 2,448
  component, 646 ship-weapon, 405 FPS-weapon, 17 medical entity, 135 commodity,
  1,641 crafting, and 200 journal candidates.
- The graph proves 134 commodity-to-resource edges and 5,978 crafting input
  and output edges: 4,043 resource requirements, 298 entity requirements, and
  1,637 produced entities. It also proves 1,607 blueprint-category edges.
- Mission classification emits 2,364 mission facts. Spawn derivation emits 228
  facts from paired allied markers and finite limits; one documented `-1`
  unbounded sentinel is excluded from totals.
- The build contains no reviewed direct ace-pilot, turret-count, or engagement
  fields. Ace and engagement capabilities report evidence unavailable instead
  of inferring values from names or descriptions.
- 744 pointer-cycle encounters are expected graph topology and are cut safely.
  No traversal depth or node bound was reached.

No HOTFIX archive was installed on the validation host. HOTFIX parsing,
channel isolation, and redaction remain covered synthetically; a real HOTFIX
aggregate must be added when that channel is locally available.

## Corrections

Corrections require an exact provider, build, record ID, field path, expected
value, replacement, rationale, and source. They preserve the original source
evidence and refuse missing, ambiguous, stale, or type-changing targets.

## Remaining G5 gates

- Repeat the read-only aggregate against HOTFIX when a local HOTFIX archive is
  available; never substitute a LIVE result.
- Wire the validated provider cache into CLI/GUI provenance views; G6, not G5,
  owns user-facing tactical presentation and tag controls.
