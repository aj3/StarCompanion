from html.parser import HTMLParser

import pytest

from starcompanion.gui.localization_preview import (
    MAX_PREVIEW_CHARACTERS,
    render_localization_preview,
)


class _MarkupInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(name for name, _value in attrs)


def test_untrusted_html_is_escaped_and_cannot_create_links_or_resources():
    payload = (
        '<script>alert("x")</script><img src="file:///secret">'
        '<a href="https://example.invalid">open</a><style>body{background:url(x)}</style>'
    )

    result = render_localization_preview(payload)
    inventory = _MarkupInventory()
    inventory.feed(result.html)

    assert set(inventory.tags) <= {"style", "div", "span", "strong", "em"}
    assert not {"href", "src", "action", "formaction"} & set(inventory.attributes)
    assert "&lt;script&gt;" in result.html
    assert "&lt;img src=&quot;file:///secret&quot;&gt;" in result.html
    assert "https://example.invalid" in result.html
    assert "background:url(x)" in result.html


def test_balanced_allowlisted_tags_are_interpreted_and_emphasis_is_annotated():
    result = render_localization_preview("<EM4>Reward <b>important</b> <i>now</i></EM4>")

    assert '<span class="sc-cig-em">' in result.html
    assert '<span class="sc-cig-em-label">[EM4]</span>' in result.html
    assert "<strong>important</strong>" in result.html
    assert "<em>now</em>" in result.html
    assert "<EM4>" not in result.html


@pytest.mark.parametrize(
    "value",
    [
        "<EM4>unclosed",
        "<EM4><b>crossed</EM4></b>",
        "</EM4>orphan",
        "<EM4 class=x>malformed</EM4>",
        "<em4>wrong case</em4>",
        "<script>unknown</script>",
    ],
)
def test_unknown_malformed_or_unbalanced_tags_remain_literal(value):
    result = render_localization_preview(value)

    assert '<span class="sc-cig-em-label">' not in result.html
    assert "<strong>" not in result.html
    assert "&lt;" in result.html


def test_invalid_group_does_not_hide_a_later_balanced_group():
    result = render_localization_preview("<EM4>bad</EM3> then <EM2>good</EM2>")

    assert "&lt;EM4&gt;bad&lt;/EM3&gt;" in result.html
    assert '<span class="sc-cig-em-label">[EM2]</span>' in result.html


def test_none_marker_is_visible_and_an_invalid_close_stays_literal():
    result = render_localization_preview("Before <None> after </None>")

    assert '<span class="sc-cig-none">&lt;None&gt;</span>' in result.html
    assert "&lt;/None&gt;" in result.html


@pytest.mark.parametrize(
    ("token", "recognized"),
    [
        ("~mission(Location|Address)", True),
        ("~mission( Objective )", True),
        ("~mission()", False),
        ("~mission(Location|)", False),
        ("~mission(|Address)", False),
        ("~mission(Unclosed", False),
        ("~Mission(Location)", False),
    ],
)
def test_only_recognized_mission_tokens_receive_token_styling(token, recognized):
    result = render_localization_preview(token)

    assert ('class="sc-cig-token"' in result.html) is recognized
    assert token.replace("<", "&lt;").replace(">", "&gt;") in result.html


def test_truncation_is_bounded_visible_and_reported_without_interpreting_cut_tag():
    result = render_localization_preview("prefix <EM4>long value</EM4>", max_characters=12)

    assert result.truncated
    assert result.source_characters == 28
    assert result.rendered_characters == 12
    assert "truncated to 12 of 28 characters" in result.message
    assert result.message in result.html
    assert '<span class="sc-cig-em-label">' not in result.html
    assert "&lt;EM4&gt;" in result.html


def test_callers_cannot_raise_the_hard_preview_ceiling():
    value = "x" * (MAX_PREVIEW_CHARACTERS + 10)
    result = render_localization_preview(
        value,
        max_characters=MAX_PREVIEW_CHARACTERS * 2,
    )

    assert result.truncated
    assert result.rendered_characters == MAX_PREVIEW_CHARACTERS
    assert len(result.html) < MAX_PREVIEW_CHARACTERS * 2


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_preview_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        render_localization_preview("value", max_characters=limit)


@pytest.mark.parametrize("limit", [True, 1.5, "10"])
def test_non_integer_preview_limits_are_rejected(limit):
    with pytest.raises(TypeError):
        render_localization_preview("value", max_characters=limit)
