"""Strict bundled interface text catalogs with offline fallback."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

DEFAULT_UI_LOCALE = "en-US"

_ENGLISH = {
    "app.strapline": "LOCAL CONTRACT INTELLIGENCE",
    "app.local-first": "LOCAL-FIRST / NO TELEMETRY",
    "app.privacy": "OFFLINE BY DESIGN   |   NO TELEMETRY   |   WRITES REQUIRE CONFIRMATION",
    "app.mode.simple": "SIMPLE MODE",
    "app.mode.full": "FULL MODE",
    "app.profile": "PROFILE",
    "app.game": "GAME",
    "app.data": "DATA",
    "app.not-found": "NOT FOUND",
    "app.not-loaded": "NOT LOADED",
    "app.theme": "THEME",
    "section.workspace": "WORKSPACE",
    "section.advanced": "ADVANCED",
    "section.tools": "TOOLS",
    "nav.overview": "Overview",
    "nav.content": "Contract content",
    "nav.presentation": "Presentation",
    "nav.blueprints": "Blueprints",
    "nav.templates": "Custom wording",
    "nav.string-editor": "String editor",
    "nav.provenance": "Data & provenance",
    "nav.manual-apply": "Backup & recovery",
    "nav.support": "Settings & help",
    "page.overview.title": "Contract workspace",
    "page.overview.description": "Find your install, read local data, and prepare a safe game update.",
    "page.content.title": "Choose contract intelligence",
    "page.content.description": "Control which local mission facts appear in the contract manager.",
    "page.presentation.title": "Shape the presentation",
    "page.presentation.description": "Tune title structure, labels, and formatting without changing source data.",
    "page.blueprints.title": "Track blueprint ownership",
    "page.blueprints.description": "Search the local catalog and review channel-scoped acquisition evidence.",
    "page.templates.title": "Author custom wording",
    "page.templates.description": "Edit explicit templates with live previews and model-level history.",
    "page.string-editor.title": "Inspect and edit merged strings",
    "page.string-editor.description": "Search stock, rendered, and user values with complete source and validation evidence.",
    "page.provenance.title": "Inspect local source evidence",
    "page.provenance.description": "Review provider capability, coverage, provenance, and build diagnostics.",
    "page.manual-apply.title": "Review backups and recovery",
    "page.manual-apply.description": "Browse scoped backups, resolve known interruptions, or inspect a manual plan.",
    "page.support.title": "Manage local settings and support",
    "page.support.description": "Profiles, verified portability, redacted diagnostics, and searchable offline guidance.",
}

_FRENCH = {
    **_ENGLISH,
    "app.strapline": "RENSEIGNEMENTS LOCAUX SUR LES CONTRATS",
    "app.local-first": "LOCAL D’ABORD / AUCUNE TÉLÉMÉTRIE",
    "app.mode.simple": "MODE SIMPLE",
    "app.mode.full": "MODE COMPLET",
    "app.profile": "PROFIL",
    "app.game": "JEU",
    "app.data": "DONNÉES",
    "app.not-found": "INTROUVABLE",
    "app.not-loaded": "NON CHARGÉES",
    "app.theme": "THÈME",
    "section.workspace": "ESPACE DE TRAVAIL",
    "section.advanced": "AVANCÉ",
    "section.tools": "OUTILS",
    "nav.overview": "Aperçu",
    "nav.content": "Contenu des contrats",
    "nav.presentation": "Présentation",
    "nav.blueprints": "Plans",
    "nav.templates": "Libellés personnalisés",
    "nav.string-editor": "Éditeur de chaînes",
    "nav.provenance": "Données et provenance",
    "nav.manual-apply": "Sauvegarde et récupération",
    "nav.support": "Paramètres et aide",
}

CATALOGS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "en-US": MappingProxyType(_ENGLISH),
        "fr-FR": MappingProxyType(_FRENCH),
    }
)
LOCALE_LABELS: Mapping[str, str] = MappingProxyType(
    {"en-US": "English", "fr-FR": "Français (shell preview)"}
)


def normalize_ui_locale(value: object) -> str:
    selected = str(value)
    return selected if selected in CATALOGS else DEFAULT_UI_LOCALE


def validate_catalog(locale: str, values: Mapping[str, object]) -> dict[str, str]:
    if set(values) != set(_ENGLISH):
        raise ValueError(f"interface catalog {locale!r} has missing or unknown keys")
    result = {}
    for key, value in values.items():
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 240
            or any(character in value for character in "\r\n\x00")
        ):
            raise ValueError(f"invalid interface text for {key!r}")
        result[key] = value
    return result


class UiTranslator:
    def __init__(self, locale: str = DEFAULT_UI_LOCALE):
        self.locale = normalize_ui_locale(locale)
        self._catalog = validate_catalog(self.locale, CATALOGS[self.locale])

    def text(self, key: str, fallback: str | None = None) -> str:
        return self._catalog.get(key, _ENGLISH.get(key, fallback or key))


__all__ = [
    "CATALOGS",
    "DEFAULT_UI_LOCALE",
    "LOCALE_LABELS",
    "UiTranslator",
    "normalize_ui_locale",
    "validate_catalog",
]
