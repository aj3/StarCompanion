import json
import struct
from pathlib import Path

import pytest

import dcbbuilder as B
from starcompanion.extract import datacore
from starcompanion.extract.datacore import (
    ConversionType,
    CorruptDataCoreError,
    DataType,
    Record,
    UnsupportedVersionError,
)

def _real_dcb() -> Path | None:
    """A real Game2.dcb to test against, if one has been extracted.

    Point STARCOMPANION_DCB at it, or drop it in tests/samples/. It is 330 MB,
    so it is never committed and these tests skip without it.
    """
    from os import environ

    candidates = [
        Path(environ["STARCOMPANION_DCB"]) if environ.get("STARCOMPANION_DCB") else None,
        Path(__file__).parent / "samples" / "Game2.dcb",
    ]
    return next((p for p in candidates if p and p.is_file()), None)


REAL_DCB = _real_dcb()


# --- version handling --------------------------------------------------------


@pytest.mark.parametrize("version", [6, 8])
def test_known_versions_parse(version):
    core = datacore.loads(B.minimal(version=version))
    assert core.version == version
    assert len(core.records) == 1


def test_unknown_version_fails_loudly_with_the_value_found():
    """A changed format must refuse rather than emit records that look right
    and are not."""
    data = B.Builder(version=99).add_struct("X").build()

    with pytest.raises(UnsupportedVersionError) as exc:
        datacore.loads(data)

    assert exc.value.version == 99
    assert "99" in str(exc.value) and "6, 8" in str(exc.value)


def test_unknown_version_is_checked_before_anything_else():
    """Even a file that is otherwise nonsense reports the version, not a
    downstream corruption error."""
    header = struct.pack("<IIII" + "i" * 24 + "II", 0, 7, 0, 0, *([9999] * 26))
    with pytest.raises(UnsupportedVersionError):
        datacore.loads(header)


def test_header_format_is_exactly_the_header_size():
    """A longer format reads into the struct table and works only by accident
    while the surplus fields go unused."""
    assert struct.calcsize(datacore.HEADER_FORMAT) == datacore.HEADER_SIZE


# --- record layout -----------------------------------------------------------


def test_v8_records_carry_a_tag():
    core = datacore.loads(B.minimal(version=8))
    assert core.records[0].tag == "Mission"


def test_v6_records_have_no_tag():
    """The tag field was added in v8; v6 records genuinely lack it."""
    core = datacore.loads(B.minimal(version=6))
    assert core.records[0].tag is None


def test_record_fields_decode():
    core = datacore.loads(B.minimal())
    record = core.records[0]
    assert record.name == "Mission.Test"
    assert record.file_name == "libs/foundry/records/mission/test.xml"
    assert record.struct_index == 0


def test_guid_is_formatted():
    guid = bytes(range(16))
    data = (
        B.Builder()
        .add_struct("S")
        .add_record("R", "f.xml", 0, guid=guid)
        .build()
    )
    formatted = datacore.loads(data).records[0].guid
    assert formatted.count("-") == 4
    assert formatted == "03020100-0504-0706-0809-0a0b0c0d0e0f"


# --- string tables -----------------------------------------------------------


def test_the_two_string_tables_are_kept_apart():
    """Record names live in table 2 and file names in table 1. Mixing them up
    yields plausible but wrong strings rather than an error."""
    data = (
        B.Builder()
        .add_struct("S")
        .add_record("THE_NAME", "THE_FILE", 0)
        .build()
    )
    record = datacore.loads(data).records[0]

    assert record.name == "THE_NAME"
    assert record.file_name == "THE_FILE"


def test_absent_string_offset_reads_as_none():
    core = datacore.loads(B.minimal(version=6))
    assert core.records[0].tag is None
    assert core.text2(-1) is None


def test_offset_past_the_table_reads_as_none():
    core = datacore.loads(B.minimal())
    assert core.text1(10_000_000) is None


# --- definitions -------------------------------------------------------------

def inheritance_fixture() -> bytes:
    return (
        B.Builder()
        .add_struct("Base", attribute_count=1, first_attribute_index=0)
        .add_struct("Derived", parent_index=0, attribute_count=2, first_attribute_index=1)
        .add_property("fromBase", data_type=DataType.BOOLEAN)
        .add_property("ownA", data_type=DataType.INT32)
        .add_property("ownB", data_type=DataType.STRING, conversion_type=ConversionType.SIMPLE_ARRAY)
        .add_record("R", "f.xml", 1)
        .build()
    )


def test_properties_include_inherited_ones_base_first():
    core = datacore.loads(inheritance_fixture())
    names = [p.name for p in core.properties_of(core.struct_index("Derived"))]
    assert names == ["fromBase", "ownA", "ownB"]


def test_properties_of_a_root_struct_are_its_own():
    core = datacore.loads(inheritance_fixture())
    assert [p.name for p in core.properties_of(core.struct_index("Base"))] == ["fromBase"]


def test_array_properties_are_flagged():
    core = datacore.loads(inheritance_fixture())
    props = {p.name: p for p in core.properties_of(core.struct_index("Derived"))}
    assert not props["ownA"].is_array
    assert props["ownB"].is_array


def test_inheritance_cycle_does_not_hang():
    """Defensive: a corrupt parent chain must terminate."""
    data = (
        B.Builder()
        .add_struct("A", parent_index=1)
        .add_struct("B", parent_index=0)
        .add_record("R", "f.xml", 0)
        .build()
    )
    assert datacore.loads(data).properties_of(0) == []


# --- lookups -----------------------------------------------------------------


def test_records_are_indexed_by_struct_name():
    data = (
        B.Builder()
        .add_struct("Mission")
        .add_struct("Other")
        .add_record("M1", "a.xml", 0)
        .add_record("M2", "b.xml", 0)
        .add_record("O1", "c.xml", 1)
        .build()
    )
    core = datacore.loads(data)

    assert [r.name for r in core.records_of("Mission")] == ["M1", "M2"]
    assert [r.name for r in core.records_of("Other")] == ["O1"]
    assert core.records_of("Nonexistent") == []


def test_record_lookup_by_guid():
    guid = bytes(range(16))
    data = B.Builder().add_struct("S").add_record("R", "f.xml", 0, guid=guid).build()
    core = datacore.loads(data)

    found = core.record_by_guid(core.records[0].guid)
    assert found is not None and found.name == "R"
    assert core.record_by_guid("no-such-guid") is None


def test_find_structs_is_a_case_insensitive_substring_search():
    data = B.Builder().add_struct("MissionBrokerEntry").add_struct("ShipEntry").build()
    core = datacore.loads(data)

    assert core.find_structs("mission") == ["MissionBrokerEntry"]
    assert set(core.find_structs("entry")) == {"MissionBrokerEntry", "ShipEntry"}


# --- serialisation -----------------------------------------------------------


def test_record_round_trips_through_json():
    record = datacore.loads(B.minimal()).records[0]
    restored = Record.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record


# --- corruption --------------------------------------------------------------


def test_file_smaller_than_a_header():
    with pytest.raises(CorruptDataCoreError, match="header alone"):
        datacore.loads(b"\x00" * 10)


def test_truncated_body_is_reported_not_guessed():
    data = B.minimal()
    with pytest.raises(CorruptDataCoreError, match="past the end"):
        datacore.loads(data[: len(data) // 2])


def test_absurd_counts_are_rejected():
    data = bytearray(B.minimal())
    struct.pack_into("<i", data, 16, 10_000_000)  # struct_definition_count
    with pytest.raises(CorruptDataCoreError, match="past the end"):
        datacore.loads(bytes(data))


def test_negative_count_is_rejected():
    data = bytearray(B.minimal())
    struct.pack_into("<i", data, 16, -5)
    with pytest.raises(CorruptDataCoreError, match="negative"):
        datacore.loads(bytes(data))


# --- against the real database -----------------------------------------------

real = pytest.mark.skipif(
    REAL_DCB is None,
    reason="no Game2.dcb; set STARCOMPANION_DCB or place one in tests/samples/",
)


@pytest.fixture(scope="module")
def game():
    if REAL_DCB is None:
        pytest.skip("no Game2.dcb available")
    return datacore.load(REAL_DCB)


@real
def test_real_database_parses(game):
    assert game.version == 8
    assert len(game.records) > 100_000
    assert len(game.structs) > 5_000


@real
def test_real_record_count_is_stable(game):
    """Parsing twice must agree; a drifting count means misaligned offsets."""
    again = datacore.load(REAL_DCB)
    assert again.summary() == game.summary()


@real
def test_real_mission_records_resolve(game):
    entries = game.records_of("MissionBrokerEntry")
    assert len(entries) > 1_000

    sample = next(r for r in entries if "Bounty" in r.name)
    assert sample.name.startswith("MissionBrokerEntry.")
    assert sample.file_name.endswith(".xml")
    assert sample.guid.count("-") == 4


@real
def test_real_mission_struct_exposes_localization_fields(game):
    """The link to global.ini: LOCALE properties hold localization keys."""
    props = {p.name: p for p in game.properties_of(game.struct_index("MissionBrokerEntry"))}

    for name in ("title", "description", "missionGiver"):
        assert props[name].data_type == DataType.LOCALE


@real
def test_real_reward_structures_are_present(game):
    """Phase 9 depends on these existing."""
    assert game.struct_index("BlueprintReward") is not None
    assert game.struct_index("ContractPrerequisite_Reputation") is not None

    props = {p.name for p in game.properties_of(game.struct_index("BlueprintReward"))}
    assert {"weight", "blueprintRecord"} <= props


@real
def test_real_inheritance_resolves(game):
    """ContractPrerequisite_Reputation inherits from a base prerequisite."""
    index = game.struct_index("ContractPrerequisite_Reputation")
    assert game.structs[index].parent_index >= 0

    names = [p.name for p in game.properties_of(index)]
    assert "includePrerequisiteWhenSharing" in names  # inherited
    assert "minStanding" in names  # own


@real
def test_real_strings_come_from_the_right_tables(game):
    """File names are paths (table 1); record names are dotted (table 2)."""
    sample = game.records[:2000]
    assert all("/" in r.file_name or r.file_name == "" for r in sample)
    assert sum("." in r.name for r in sample) > len(sample) * 0.8
