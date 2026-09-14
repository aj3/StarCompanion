import pytest

from starcompanion.gui.ui_text import CATALOGS, UiTranslator, validate_catalog


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
