# Star Citizen file formats — implementation notes

Written for Phases 7–9. Describes the formats in our own words, derived from
reading two open-source community tools. **No code was copied from either**;
these notes exist so the extractor can be written without their source open.

Audit and provenance are at the end.

---

## 1. `Data.p4k` — the game archive

A **ZIP variant**, not a bespoke container. Standard ZIP tooling gets most of
the way; three CIG-specific deviations break naive readers.

### Structure

Ordinary ZIP layout: local file headers + data, then a central directory, then
an End of Central Directory (EOCD) record. Parse back-to-front — scan for the
EOCD magic from the tail, follow it to the central directory, then walk entries.

Signatures (all little-endian `u32`):

| Record | Signature |
|---|---|
| EOCD | `0x06054B50` |
| ZIP64 EOCD locator | `0x07064B50` |
| ZIP64 EOCD record | `0x06064B50` |
| Central directory header | `0x02014B50` |
| Local file header | `0x04034B50` |
| **Local file header (CIG)** | **`0x14034B50`** |

The EOCD record is 22 bytes: signature, disk number, start disk, entries on
disk, total entries, central directory size, central directory offset, comment
length.

### Deviation 1 — CIG's own local header signature

Local file headers may carry `0x14034B50` instead of the standard
`0x04034B50`. **Accept both.** A reader that only recognises the standard value
rejects the archive outright.

### Deviation 2 — ZStandard compression

Compression method **`100`** means ZStandard, which is outside the ZIP spec
(the standard registers 0=store, 8=deflate). Python's `zipfile` will refuse it,
so decompression must be handled directly rather than delegated. Store and
deflate still appear for some entries; dispatch on the method field.

### Deviation 3 — AES-encrypted entries

Some entries are encrypted with **AES-128-CBC, zero IV**, using a key that is
a published constant in the community tools rather than a secret:

```
5E 7A 20 02 30 2E EB 1A 3B B6 17 C3 0F DE 1E 47
```

Encryption is flagged in the entry's **extra field**, not the standard ZIP
general-purpose flag: the byte at offset `168` of the extra field is non-zero
when the entry is encrypted (so the extra field must be at least 169 bytes for
the entry to be encrypted at all).

Because the standard flag is not used, the stock ZIP decoder marks these
entries invalid. Decode central directory entries manually rather than reusing
a strict library validator.

### ZIP64

The archive is multi-gigabyte, so ZIP64 is in play. Any of
`uncompressed_size`, `compressed_size`, or `local_header_offset` equal to
`0xFFFFFFFF` (or `0xFFFFFFFFFFFFFFFF` for 64-bit size) means the real value
lives in the ZIP64 extra field, read in field order.

### Paths we need

Verified against a real install — **build `sc-alpha-4.9.0`, changelist
12344265, `Data.p4k` 151 GB, 1,364,115 entries**:

- `Data/Localization/<language>/global.ini` — the stock string table. Globbing
  `Data/Localization/*/global.ini` enumerates languages. **Real names carry a
  region suffix**: `english`, `french_(france)`, `german_(germany)`,
  `chinese_(simplified)`, … (11 in this build). Do not assume bare `french`.
- **`Data/Game2.dcb`** — the DataCore database, 330 MB, zstd, unencrypted.
  Note the **`2`**: older tooling looks for `Data/Game.dcb`, which does not
  exist in current builds. Discover by extension rather than hardcoding.

Both are individually extractable; there is no need to unpack the archive.
Neither was encrypted in this build, though the reader handles encryption.

Current CIG-aligned method-100 entries pad the local header so payload data
starts on a 4 KiB boundary. In LIVE 4.9.188.23497, the central/local field named
CRC does not equal ZIP CRC-32 of the decompressed bytes for either `Game2.dcb`
or any inspected localization file. StarCompanion treats that field as
advisory only for this unambiguous aligned layout, records an integrity warning,
and still requires successful Zstandard decoding and the exact declared output
length. Standard/fixture ZIP layouts continue to fail on a CRC mismatch.

### Observed on the real archive

- Opening (central directory only) takes ~32 s and ~537 MB for 1.36 M entries.
  The cost is the entry table, not the file — reading a single entry afterwards
  is bounded by that entry's size.
- `global.ini` (english) is 10,439,724 bytes, 90,121 keys, 13,353 of them
  carrying the `,P` suffix.
- **The shipped `global.ini` is CRLF**, while StarStrings and other community
  packs redistribute it as LF. A parser that splits on `\n` alone leaves a
  stray `\r` on the end of every value, which then leaks into rendered output.
  Detect the convention; do not assume either.

### Implications for Phase 7

- Read the central directory only, then seek to the entries we want. Never load
  the archive into memory.
- Open read-only and never write — extraction targets a separate cache path.
- Treat a missing/short EOCD, an unknown compression method, or a truncated
  entry as a clear error naming the archive, not a traceback.

---

## 2. `Data/Game.dcb` — the DataCore database

A binary record database holding the game's structured data: items, missions,
reward tables, loadouts. Not a filesystem — a set of typed records with a
reflection-style schema describing their own layout.

### Header

**120 bytes**, all fields little-endian:

```
magic                     u32
version                   u32
reserved                  u32 x2
struct_definition_count   i32
property_definition_count i32
enum_definition_count     i32
data_mapping_count        i32
record_definition_count   i32
boolean_value_count       i32
int8/16/32/64_value_count i32 x4
uint8/16/32/64_value_count i32 x4
single_value_count        i32     (f32)
double_value_count        i32     (f64)
guid_value_count          i32
string_id_value_count     i32
locale_value_count        i32
enum_value_count          i32
strong_value_count        i32
weak_value_count          i32
reference_value_count     i32
enum_option_count         i32
text_length               u32
text_length2              u32
```

**Known versions are 6 and 8.** Anything else must be rejected outright — see
"version churn" below.

### Layout

Everything after the header is a sequence of fixed-size arrays, each sized by
its count from the header, in a fixed order:

1. struct definitions
2. property definitions
3. enum definitions
4. data mappings
5. records
6. typed value arrays — one array per scalar type, in header field order
7. string table 1 (`text_length` bytes)
8. string table 2 (`text_length2` bytes)

Because sizes are all known up front, offsets are computed by accumulation and
the whole file can be reinterpreted in place — no per-record allocation needed.

### Key types

- **`StringId` / `StringId2`** — an `i32` offset into string table 1 or 2
  respectively. Strings are NUL-terminated at that offset. **Two separate
  tables**: getting them the wrong way round yields plausible-looking wrong
  strings, which is worse than a crash.
- **`StructDefinition`** — name offset, parent/struct size, and the property
  range describing its fields.
- **`PropertyDefinition`** — name, owning struct index, data type, and a
  `conversion_type` distinguishing a single value from an array.
- **`DataMapping`** — struct index plus how many instances follow, used to walk
  the typed value arrays.
- **`Record`** — the addressable unit: name, file name, struct index, size, and
  an id. **Grew from 32 bytes in v6 to 36 in v8** (a new `tag_offset` field), so
  the record array must be parsed per-version.
- **`Pointer`** — struct index + index, resolved against the typed arrays.
- **`Reference`** / strong / weak values — cross-record links, resolved via the
  record id map.

### Reading a record

1. Look up its `StructDefinition` via `struct_index`.
2. Walk that struct's properties (including inherited ones, via the parent
   chain).
3. For each property, take the next value from the array matching its data
   type; a pointer or reference is resolved to another record.
4. Resolve every `StringId` through the correct string table.

### Useful derived indexes

Worth building once at load:

- record id → record index
- struct name → struct index
- struct index → the records using it (this is how "find all mission records"
  is answered)
- "main" records — the last record per unique file name, since a file name can
  appear more than once

### Implications for Phases 8–9

- **Version churn is the standing risk.** Field counts and record sizes change
  between patches. Validate `version` immediately and fail loudly with the value
  found; never parse a partial file and emit data that looks right.
- Record-walking rules (which struct names hold missions, which properties hold
  rewards) belong in editable config, not code — a patch that renames a struct
  should be a config edit.
- Localization keys inside records are plain strings that must match keys in
  `global.ini`. The `,P` suffix problem from Phase 0 applies here too.

---

## 3. CryXmlB — CryEngine binary XML

Mission definitions and much other config ship in this format rather than text
XML. Files begin with the magic `CryXmlB\0`.

### Layout

After the 8-byte magic, a header of nine `u32` fields, all offsets relative to
the start of the file:

```
xml_size
node_table_position       node_count
attribute_table_position  attribute_count
child_table_position      child_count
string_data_position      string_data_size
```

Then four sections:

- **Nodes** — 28 bytes each: tag string offset, item type, attribute count
  (`u16`), child count (`u16`), parent index, first attribute index, first
  child index, reserved.
- **Attributes** — 8 bytes each: key string offset, value string offset.
- **Child indices** — `u32` each; a node's children are the `child_count`
  entries starting at its `first_child_index`.
- **String data** — NUL-terminated UTF-8, addressed by byte offset.

Node 0 is the root. **Wire children after every node exists** — a child can
appear before its parent in the table.

---

## 4. Historical top-level reward search (superseded by C1)

Recorded because it explains why the original broker-only extractor stopped
early. These observations were correct for that narrow traversal, but the
conclusion was not: Sprint C1 follows anonymous struct instances, typed arrays,
record UUIDs, and generator/broker localization joins.

The earlier top-level checks against build `sc-alpha-4.9.0` found:

| Check | Result |
|---|---|
| `BlueprintReward` / `MissionReward` records in DataCore | **0 instances** (schema only) |
| Broker entry read at pointer depth 0, 1, 2 | never reaches `blueprintRecord` or `minStanding` |
| StarStrings' 1,449 keys found in the 465 mission files | **0** |
| `Data/Libs/Subsumption/Missions/PU/` — the path every `missionModule` points at | **0 entries** |
| "Foxwell" anywhere in the archive | 20 hits, all art: animations, a face model, a logo |

`global.ini` *does* contain all 1,449 strings. The earlier investigation treated
the empty top-level reward structs and missing Subsumption modules as evidence
that reward selection was wholly server-authoritative. C1 disproved that
inference by reaching different local DataForge record trees.

**Corrected consequence:** top-level record scans cannot find the reward graph.
The client does ship usable local evidence under contract-generator, mission
broker, reputation-reward, and crafting blueprint record trees. C1 extracts it
without SCMDB or a network source and reports missing optional targets as build
diagnostics.

Contract *discovery* is still possible: `MissionBrokerEntry` yields 2,492
contracts and ~1,000 localization keys, and the per-variant keys follow a rigid
`{Org}_{family}_{difficulty}_{kind}_{seq}` convention that can be matched
against `global.ini` directly.

---

## Reference tools — audit

Reviewed before writing any of the above, per the project rule: use as guides,
copy nothing, and check they are not malicious.

### `scdatatools` — Python, MIT

**Provenance problem, and the reason we are not depending on it.** The canonical
upstream at `gitlab.com/scmodding/frameworks/scdatatools` now returns *"project
could not be found or you don't have permission"* — it is gone or private. The
GitHub mirror `ExterraGroup/scdatatools` was archived in December 2020. The
copy reviewed here is the fork `TheCodingLand/scdatatools` (MIT, last pushed
October 2024, 0 stars).

A dependency whose upstream has vanished and whose surviving copy is an
unstarred personal fork is exactly what we should not be importing. Reading it
for format knowledge is fine; shipping it would not be.

| Check | Result |
|---|---|
| Network imports (`requests`, `urllib`, `socket`, …) | **None** outside tests |
| `eval` / `exec` / `pickle.loads` / `__import__` | **None** — all grep hits were `re.compile` |
| Obfuscation (long base64/hex blobs, decode-then-run) | **None** |
| Install-time hooks (`cmdclass`, post-install) | **None** — plain `setup.py` |
| Subprocess use | Present, purpose-clear: Blender, `cgf-converter`, `texconv`/`compressonatorcli`, `revorb`. All external asset converters, none reached from p4k/DataCore parsing. |

*Code-quality note, not malice:* six `subprocess` calls use `shell=True` with
interpolated paths (`engine/chunkfile/converter.py:85`, `wwise/__init__.py:157`,
others). That is a command-injection footgun on attacker-influenced filenames.
Our extractor spawns no processes at all, so we do not inherit it — but it is a
concrete reason not to vendor this code.

**Verdict: not malicious. Safe to read. Not safe to depend on** — dead upstream.

### `StarBreaker` — Rust, ~121 stars, actively developed

| Check | Result |
|---|---|
| Hardcoded URLs / exfiltration in source | **None** |
| `build.rs` | One, in the Tauri app: shells out to `git` to embed a commit SHA. Standard practice, benign. |
| `unsafe` | 11 occurrences, all mundane — `Send`/`Sync` impls on a progress struct, a Windows API extern in an example. Nothing in the parsing hot paths that looked reckless. |
| Process spawning | Blender and `tasklist`, via a helper that suppresses console-window flash on Windows. Purpose-clear. |
| Network | `reqwest` declared in `starbreaker-chf`'s `Cargo.toml` but **no usage found in that crate's source** — a vestigial dependency. Worth noting; not evidence of wrongdoing. |

Cleanly separated crates (`starbreaker-p4k`, `starbreaker-datacore`,
`starbreaker-cryxml`), and it openly credits its own format knowledge to
`unp4k` and `scdatatools`.

**Verdict: not malicious. The better reference of the two** — current, actively
maintained, and structured so the format logic is readable in isolation.

### What was taken

Format facts only: signature values, header field order, record sizes, the
encryption scheme and its flag location, the version numbers that exist. These
are properties of CIG's file format, not authored expression — the same facts
any implementation must encode.

No source was copied, adapted, or vendored from either project. Both clones
live outside the repository and are not distributed with it.
