"""Typed, versioned GUI preferences over the C5 portable preference store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..portability import PortabilityError, PreferencesStore
from ..user_edits import data_dir
from .theme import DEFAULT_THEME, ThemeName

UI_PREFERENCE_SCHEMA = 1
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

    def with_theme(self, value: ThemeName) -> "UiPreferences":
        return replace(self, theme=value)

    def with_page(self, value: str) -> "UiPreferences":
        return replace(self, last_page=value if value in PAGE_KEYS else DEFAULT_PAGE)


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
        fallback = legacy_theme if legacy_theme in {"dark", "light"} else DEFAULT_THEME
        try:
            values = self._store.load()
        except (OSError, PortabilityError) as exc:
            return PreferenceLoad(
                UiPreferences(theme=fallback),
                f"Interface preferences could not be loaded: {exc}. "
                "The file was left unchanged.",
            )

        schema = values.get("ui_schema")
        if schema not in (None, UI_PREFERENCE_SCHEMA):
            return PreferenceLoad(
                UiPreferences(theme=fallback),
                f"Interface preference schema {schema!r} is newer than this build. "
                "The file was left unchanged.",
            )

        theme = values.get("theme", fallback)
        page = values.get("last_page", DEFAULT_PAGE)
        preferences = UiPreferences(
            theme=theme if theme in {"dark", "light"} else fallback,
            last_page=page if page in PAGE_KEYS else DEFAULT_PAGE,
        )

        if schema is None:
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
        if schema not in (None, UI_PREFERENCE_SCHEMA):
            raise PortabilityError(
                f"interface preference schema {schema!r} is newer than this build"
            )
        values.update(
            {
                "ui_schema": UI_PREFERENCE_SCHEMA,
                "theme": preferences.theme,
                "last_page": preferences.last_page,
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
