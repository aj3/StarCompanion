# G5 local provider architecture

Sprint G5 reads only the player's local DataForge graph. It adds no network
client, external dataset, game-file write, or GUI-thread extraction path.

## Current synthetic-first catalog

| Provider family | Facts | Isolation boundary |
|---|---|---|
| Vehicle | name, mass, cargo, crew | Vehicle records only |
| Component | name, size, grade, class | Excludes specialized item trees |
| Ship weapon | size, damage, fire rate, velocity, range | Ship/vehicle weapon records |
| FPS weapon | damage, fire rate, magazine, range | Personal/FPS weapon records |
| Medical | healing, duration, overdose, toxicity | Medical consumables |
| Commodity | local price facts | Tradable commodity records |
| Crafting | recipe time, output, rank | Blueprint/recipe records |
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

## Corrections

Corrections require an exact provider, build, record ID, field path, expected
value, replacement, rationale, and source. They preserve the original source
evidence and refuse missing, ambiguous, stale, or type-changing targets.

## Remaining G5 gates

- Validate field aliases and record classifications against supported local
  LIVE and HOTFIX builds without committing extracted game data.
- Add graph-backed commodity/crafting relationships where the real schema
  proves them.
- Record bounded aggregate snapshots for build drift.
- Wire the validated provider cache into CLI/GUI provenance views; G6, not G5,
  owns user-facing tactical presentation and tag controls.
