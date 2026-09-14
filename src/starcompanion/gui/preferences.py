"""Typed, versioned GUI preferences over the C5 portable preference store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..portability import PortabilityError, PreferencesStore
from ..install import normalize_language
from ..user_edits import data_dir
from .theme import DEFAULT_THEME, PALETTES, ThemeName
from .ui_text import DEFAULT_UI_LOCALE, normalize_ui_locale

UI_PREFERENCE_SCHEMA = 3
DEFAULT_PAGE = "overview"
PAGE_KEYS = frozenset(
    {
        "overview",
        "content",
        "presentation",
        "blueprints",
        "templates",
        "string-editor",
        "provenance",
        "manual-apply",
        "support",
    }
)


@dataclass(frozen=True)
class UiPreferences:
    theme: ThemeName = DEFAULT_THEME
    last_page: str = DEFAULT_PAGE
    link_live_hotfix: bool = True
    default_language: str = "english"
    simple_mode: bool = False
    tutorial_completed: bool = False
    interface_locale: str = DEFAULT_UI_LOCALE

    def with_theme(self, value: ThemeName) -> "UiPreferences":
        return replace(self, theme=value)

    def with_page(self, value: str) -> "UiPreferences":
        return replace(self, last_page=value if value in PAGE_KEYS else DEFAULT_PAGE)

    def with_live_hotfix_link(self, enabled: bool) -> "UiPreferences":
        return replace(self, link_live_hotfix=bool(enabled))

    def with_default_language(self, value: str) -> "UiPreferences":
        return replace(self, default_language=normalize_language(value))

    def with_simple_mode(self, enabled: bool) -> "UiPreferences":
        return replace(self, simple_mode=bool(enabled))

    def with_tutorial_completed(self, completed: bool = True) -> "UiPreferences":
        return replace(self, tutorial_completed=bool(completed))

    def with_interface_locale(self, value: str) -> "UiPreferences":
        return replace(self, interface_locale=normalize_ui_locale(value))


@dataclass(frozen=True)
class PreferenceLoad:
    preferences: UiPreferences
    warning: str | None = None


class UiPreferencesStore:
    """Preserve unrelated C5 preferences and never overwrite invalid input."""

    def __init__(self, root: Path | None = None):
        self._store = PreferencesStore(Path(root or data_dir()))

    @property
    def path(self) -> Path:
        return self._store.path

    def load(self, *, legacy_theme: str = DEFAULT_THEME) -> PreferenceLoad:
        fallback = legacy_theme if legacy_theme in PALETTES else DEFAULT_THEME
        try:
            values = self._store.load()
        except (OSError, PortabilityError) as exc:
            return PreferenceLoad(
                UiPreferences(theme=fallback),
                f"Interface preferences could not be loaded: {exc}. "
                "The file was left unchanged.",
            )

        schema = values.get("ui_schema")
        if schema not in (None, 1, 2, UI_PREFERENCE_SCHEMA):
            return PreferenceLoad(
                UiPreferences(theme=fallback),
                f"Interface preference schema {schema!r} is newer than this build. "
                "The file was left unchanged.",
            )

        theme = values.get("theme", fallback)
        page = values.get("last_page", DEFAULT_PAGE)
        try:
            language = normalize_language(str(values.get("default_language", "english")))
        except ValueError:
            language = "english"
        preferences = UiPreferences(
            theme=theme if theme in PALETTES else fallback,
            last_page=page if page in PAGE_KEYS else DEFAULT_PAGE,
            link_live_hotfix=bool(values.get("link_live_hotfix", True)),
            default_language=language,
            simple_mode=bool(values.get("simple_mode", False)),
            tutorial_completed=bool(values.get("tutorial_completed", False)),
            interface_locale=normalize_ui_locale(
                values.get("interface_locale", DEFAULT_UI_LOCALE)
            ),
        )

        if schema != UI_PREFERENCE_SCHEMA:
            try:
                self.save(preferences, existing=values)
            except (OSError, PortabilityError) as exc:
                return PreferenceLoad(
                    preferences,
                    f"Interface preferences could not be migrated: {exc}. "
                    "The existing file was left unchanged.",
                )
        return PreferenceLoad(preferences)

    def save(
        self,
        preferences: UiPreferences,
        *,
        existing: dict[str, object] | None = None,
    ) -> None:
        values = dict(self._store.load() if existing is None else existing)
        schema = values.get("ui_schema")
        if schema not in (None, 1, 2, UI_PREFERENCE_SCHEMA):
            raise PortabilityError(
                f"interface preference schema {schema!r} is newer than this build"
            )
        values.update(
            {
                "ui_schema": UI_PREFERENCE_SCHEMA,
                "theme": preferences.theme,
                "last_page": preferences.last_page,
                "link_live_hotfix": preferences.link_live_hotfix,
                "default_language": preferences.default_language,
                "simple_mode": preferences.simple_mode,
                "tutorial_completed": preferences.tutorial_completed,
                "interface_locale": preferences.interface_locale,
            }
        )
        self._store.save(values)


__all__ = [
    "DEFAULT_PAGE",
    "PAGE_KEYS",
    "PreferenceLoad",
    "UI_PREFERENCE_SCHEMA",
    "UiPreferences",
    "UiPreferencesStore",
]
