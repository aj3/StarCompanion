import pytest

from pathlib import Path

from starcompanion.gui.translation_catalog import (
    DEFAULT_REVIEW_PATH,
    catalog_document,
    collect_ui_strings,
    render_catalog,
)
from starcompanion.gui.ui_text import (
    CATALOGS,
    UiTranslator,
    pseudo_localize,
    validate_catalog,
)


def test_every_bundled_interface_catalog_is_complete_and_bounded():
    english = set(CATALOGS["en-US"])
    assert english
    for locale, catalog in CATALOGS.items():
        assert set(catalog) == english
        assert validate_catalog(locale, catalog) == dict(catalog)


def test_unknown_interface_locale_falls_back_offline_to_english():
    translator = UiTranslator("download-me")
    assert translator.locale == "en-US"
    assert translator.text("nav.overview") == "Overview"


def test_catalog_rejects_missing_unknown_multiline_and_oversized_values():
    source = dict(CATALOGS["en-US"])
    source.pop("nav.overview")
    with pytest.raises(ValueError, match="missing or unknown"):
        validate_catalog("bad", source)

    source = dict(CATALOGS["en-US"])
    source["nav.overview"] = "unsafe\ntext"
    with pytest.raises(ValueError, match="invalid interface text"):
        validate_catalog("bad", source)

    source = dict(CATALOGS["en-US"])
    source["nav.overview"] = "x" * 241
    with pytest.raises(ValueError, match="invalid interface text"):
        validate_catalog("bad", source)


def test_pseudo_locale_expands_text_but_preserves_runtime_placeholders():
    source = (
        "Open {profile} at %1 for ~mission(Location|Address): "
        "%(count)04d / %2$s / %.2f / 100%%"
    )
    rendered = pseudo_localize(source)

    assert rendered.startswith("⟦") and rendered.endswith("⟧")
    assert len(rendered) > len(source)
    assert "{profile}" in rendered
    assert "%1" in rendered
    assert "~mission(Location|Address)" in rendered
    assert "%(count)04d" in rendered
    assert "%2$s" in rendered
    assert "%.2f" in rendered
    assert "100%%" in rendered
    assert UiTranslator("qps-ploc").text("nav.overview") != "Overview"


def test_translator_review_catalog_covers_every_visible_source_location():
    entries = collect_ui_strings()
    document = catalog_document()

    assert len(entries) >= 400
    assert document["string_count"] == len(entries)
    assert document["location_count"] >= len(entries)
    assert all(item.message_id.startswith("ui.") for item in entries)
    assert all(item.text and item.pseudo for item in entries)
    assert all(item.locations for item in entries)
    assert any(
        location.context == "information"
        for item in entries
        for location in item.locations
    )
    assert any(
        location.context == "HELP_ARTICLES"
        for item in entries
        for location in item.locations
    )
    contexts = {
        location.context for item in entries for location in item.locations
    }
    assert {
        "CoachStep",
        "EmptyState",
        "MetricTile",
        "QProgressDialog",
        "publish",
        "setPlainText",
    } <= contexts


def test_committed_translator_review_catalog_is_current_and_portable():
    assert Path(DEFAULT_REVIEW_PATH).read_text(encoding="utf-8") == render_catalog()
    assert "C:\\Users\\" not in render_catalog()
