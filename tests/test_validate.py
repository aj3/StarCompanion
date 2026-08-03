from pathlib import Path

import pytest

from starcompanion.ini import LocalizationFile
from starcompanion.validate import Severity, has_errors, validate_value

SAMPLES = Path(__file__).parent / "samples"


def codes(value: str) -> set[str]:
    return {i.code for i in validate_value(value)}


def test_clean_value_passes():
    value = r"<EM4>Reputation Awarded:</EM4> 100\n- Antium Helmet Jet"
    assert validate_value(value) == []


def test_real_newline_is_an_error():
    issues = validate_value("line one\nline two")
    assert "real-newline" in {i.code for i in issues}
    assert has_errors(issues)


def test_literal_backslash_n_is_fine():
    assert validate_value(r"line one\nline two") == []


def test_unknown_tag_is_an_error():
    issues = validate_value("<color=#ff0000>red</color>")
    assert "unknown-tag" in {i.code for i in issues}
    assert has_errors(issues)


def test_all_allowed_tags_pass():
    value = "<EM>a</EM><EM1>b</EM1><EM2>c</EM2><EM3>d</EM3><EM4>e</EM4><b>f</b><i>g</i><None>"
    assert validate_value(value) == []


def test_mismatched_tags_warn_but_do_not_error():
    issues = validate_value("<EM>text</EM4>")
    assert "mismatched-tag" in {i.code for i in issues}
    assert not has_errors(issues)


def test_unclosed_tag_warns():
    assert "unbalanced-tag" in codes("<EM4>text")


def test_stray_closing_tag_warns():
    assert "unbalanced-tag" in codes("text</EM4>")


def test_mission_tokens_pass_through():
    assert validate_value("~mission(Location|Address) and ~mission(TargetName)") == []


def test_unclosed_mission_token_is_an_error():
    assert "unclosed-token" in codes("~mission(Location")


def test_empty_mission_token_is_an_error():
    assert "empty-token" in codes("~mission()")


def test_mission_token_with_empty_segment_is_an_error():
    assert "malformed-token" in codes("~mission(Location|)")


@pytest.mark.skipif(
    not (SAMPLES / "contracts.ini").exists(), reason="real sample not present"
)
def test_real_contracts_have_no_validation_errors():
    source = LocalizationFile.load(SAMPLES / "contracts.ini")
    failures = [
        (e.key, i) for e in source.entries() for i in validate_value(e.value)
        if i.severity is Severity.ERROR
    ]
    assert failures == []
