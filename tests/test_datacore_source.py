import pytest

import dcbbuilder as B
from starcompanion.extract import datacore
from starcompanion.extract.datacore import DataCoreError, DataType
from starcompanion.ini import BOM, LocalizationFile
from starcompanion.model import StringKind
from starcompanion.sources import datacore_source
from test_datacore import REAL_DCB

LOCALE = int(DataType.LOCALE)


def build(records: list[tuple[str, str | None, str | None]]) -> bytes:
    """A DataCore holding MissionBrokerEntry records with title/description."""
    builder = (
        B.Builder()
        .add_struct(
            "MissionBrokerEntry",
            attribute_count=2,
            first_attribute_index=0,
            struct_size=8,
        )
        .add_property("title", data_type=LOCALE)
        .add_property("description", data_type=LOCALE)
    )
    for name, _title, _desc in records:
        builder.add_record(name, f"{name}.xml", 0)
    return builder.build()


def test_missing_struct_fails_loudly():
    core = datacore.loads(B.Builder().add_struct("Something").build())
    with pytest.raises(DataCoreError, match="schema changed"):
        datacore_source.extract(core)


def test_records_without_keys_are_recorded_not_dropped():
    core = datacore.loads(build([("MissionBrokerEntry.PU_Bounty", None, None)]))
    result = datacore_source.extract(core)

    assert result.contracts == []
    assert result.unparsed and "no localization keys" in result.unparsed[0][1]


def test_org_and_family_come_from_the_record_name():
    core = datacore.loads(build([("MissionBrokerEntry.PU_Bounty", None, None)]))
    # No keys, so inspect the helpers directly on a synthesised record.
    record = core.records_of("MissionBrokerEntry")[0]
    orgs: dict = {}

    assert datacore_source._org_for(record, orgs).name == "PU"
    assert datacore_source._family(record) == "Bounty"


def test_placeholder_keys_are_ignored():
    keys, _ = datacore_source._strings({"title": "@LOC_UNINITIALIZED"})
    assert keys == {}


def test_locale_prefix_is_stripped():
    keys, texts = datacore_source._strings(
        {"title": "@bounty_title", "description": "@bounty_desc"}
    )
    assert keys[StringKind.TITLE] == ["bounty_title"]
    assert keys[StringKind.DESC] == ["bounty_desc"]
    assert set(texts) == {"bounty_title", "bounty_desc"}


def test_non_locale_values_are_ignored():
    assert datacore_source._strings({"title": "not a locale ref"}) == ({}, {})


def test_duplicate_keys_are_not_repeated():
    keys, _ = datacore_source._strings({"title": "@same", "titleHUD": "@same"})
    assert keys[StringKind.TITLE] == ["same"]


def test_resolve_texts_fills_in_from_global_ini():
    from starcompanion.model import Contract, ContractSet, Org

    contract = Contract(
        id="x",
        org=Org(id="pu", name="PU"),
        family="Bounty",
        keys={StringKind.TITLE: ["bounty_title"]},
        texts={"bounty_title": ""},
        base_texts={"bounty_title": ""},
    )
    strings = LocalizationFile.loads(BOM + "bounty_title=Hunt them down\n")

    assert datacore_source.resolve_texts(ContractSet(contracts=[contract]), strings) == 1
    assert contract.texts["bounty_title"] == "Hunt them down"


def test_extracted_contracts_carry_no_invented_rewards():
    """Reward values are not in the DataCore; empty must stay empty."""
    core = datacore.loads(build([("MissionBrokerEntry.PU_Bounty", None, None)]))
    for contract in datacore_source.extract(core).contracts:
        assert contract.reward.is_empty


# --- against the real database -----------------------------------------------

real = pytest.mark.skipif(
    REAL_DCB is None,
    reason="no Game2.dcb; set STARCOMPANION_DCB or place one in tests/samples/",
)


@pytest.fixture(scope="module")
def extracted():
    if REAL_DCB is None:
        pytest.skip("no Game2.dcb available")
    return datacore_source.load(REAL_DCB)


@real
def test_real_extraction_produces_the_shared_domain_model(extracted):
    assert len(extracted.contracts) > 1_000
    assert extracted.orgs

    sample = extracted.contracts[0]
    assert sample.org.id in extracted.orgs
    assert sample.all_keys()


@real
def test_real_extraction_finds_contracts_starstrings_lacks(extracted):
    """The point of extracting ourselves rather than reusing their output."""
    from pathlib import Path

    from starcompanion.sources import contracts_ini

    samples = Path(__file__).parent / "samples" / "contracts.ini"
    if not samples.exists():
        pytest.skip("contracts.ini sample not present")

    theirs = {
        key
        for c in contracts_ini.load(samples).contracts
        for key in c.all_keys()
    }
    ours = {key for c in extracted.contracts for key in c.all_keys()}

    assert len(ours - theirs) > 500, "should surface many contracts they miss"
    assert ours & theirs, "and should still agree on some"


@real
def test_real_keys_resolve_against_the_stock_strings(extracted):
    from pathlib import Path

    stock = Path(__file__).parent / "samples" / "stock-global.ini"
    if not stock.exists():
        pytest.skip("stock global.ini not extracted")

    strings = LocalizationFile.load(stock)
    ours = {key for c in extracted.contracts for key in c.all_keys()}
    resolved = sum(1 for key in ours if strings.resolve_key(key))

    assert resolved / len(ours) > 0.85


@real
def test_real_rewards_are_absent_rather_than_fabricated(extracted):
    """Documented limitation, asserted so it cannot regress into fake data."""
    assert all(c.reward.is_empty for c in extracted.contracts)
