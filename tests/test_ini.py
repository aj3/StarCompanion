from pathlib import Path

import pytest

from starcompanion.ini import BOM, LocalizationFile

SAMPLES = Path(__file__).parent / "samples"

CRLF_SAMPLE = BOM + "Foo=bar\r\nBaz,P=qux\r\n"


def test_crlf_file_roundtrips_and_values_carry_no_carriage_return():
    r"""CIG ships global.ini as CRLF; community packs ship LF. Splitting on
    "\n" alone leaves a stray \r on the end of every value, which then leaks
    into rendered output."""
    f = LocalizationFile.loads(CRLF_SAMPLE)

    assert f.newline == "\r\n"
    assert f.get("Foo") == "bar"
    assert f.get("Baz") == "qux"
    assert f.dumps() == CRLF_SAMPLE


def test_lf_file_keeps_lf():
    text = BOM + "Foo=bar\nBaz=qux\n"
    f = LocalizationFile.loads(text)
    assert f.newline == "\n"
    assert f.dumps() == text


def test_edit_to_a_crlf_file_stays_crlf():
    f = LocalizationFile.loads(CRLF_SAMPLE)
    f.set("Foo", "changed")
    assert f.dumps() == BOM + "Foo=changed\r\nBaz,P=qux\r\n"


def test_no_value_retains_a_carriage_return():
    f = LocalizationFile.loads(BOM + "A=one\r\nB=two\r\nC=three\r\n")
    assert not any("\r" in e.value for e in f.entries())


def test_roundtrip_is_byte_identical():
    text = BOM + "Foo=bar\nBaz,P=qux\n"
    assert LocalizationFile.loads(text).dumps() == text


def test_roundtrip_without_bom_or_trailing_newline():
    text = "Foo=bar\nBaz=qux"
    assert LocalizationFile.loads(text).dumps() == text


def test_value_containing_equals_is_preserved():
    text = BOM + "Foo=a=b=c\n"
    f = LocalizationFile.loads(text)
    assert f.get("Foo") == "a=b=c"
    assert f.dumps() == text


def test_lines_without_equals_are_preserved_verbatim():
    text = BOM + "Foo=bar\n\nnot an entry\nBaz=qux\n"
    f = LocalizationFile.loads(text)
    assert len(f) == 2
    assert f.dumps() == text


def test_resolve_key_tolerates_plural_suffix():
    f = LocalizationFile.loads(BOM + "Foo,P=bar\nPlain=baz\n")
    assert f.resolve_key("Foo") == "Foo,P"
    assert f.resolve_key("Foo,P") == "Foo,P"
    assert f.resolve_key("Plain,P") == "Plain"
    assert f.resolve_key("Missing") is None


def test_set_writes_through_suffix_tolerant_lookup():
    f = LocalizationFile.loads(BOM + "Foo,P=bar\n")
    assert f.set("Foo", "changed") is True
    assert f.dumps() == BOM + "Foo,P=changed\n"


def test_set_returns_false_for_unknown_key():
    f = LocalizationFile.loads(BOM + "Foo=bar\n")
    assert f.set("Nope", "x") is False


def test_first_occurrence_wins_for_duplicate_keys():
    f = LocalizationFile.loads(BOM + "Dup=first\nDup=second\n")
    assert f.get("Dup") == "first"


@pytest.mark.skipif(
    not (SAMPLES / "global.ini").exists(), reason="real global.ini sample not present"
)
def test_real_global_ini_roundtrips_byte_identical():
    path = SAMPLES / "global.ini"
    original = path.read_bytes()
    assert LocalizationFile.loads(original.decode("utf-8")).dumps().encode("utf-8") == original


@pytest.mark.skipif(
    not (SAMPLES / "global.ini").exists() or not (SAMPLES / "contracts.ini").exists(),
    reason="real samples not present",
)
def test_every_contract_key_resolves_against_global():
    target = LocalizationFile.load(SAMPLES / "global.ini")
    source = LocalizationFile.load(SAMPLES / "contracts.ini")

    unresolved = [e.key for e in source.entries() if target.resolve_key(e.key) is None]
    assert unresolved == []
