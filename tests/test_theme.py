"""The design-token layer.

The point of these is that the two themes stay in step and that colours do not
leak back into widget code, which is how a theme system rots.
"""

import re
from pathlib import Path

import pytest

from starcompanion.config import Profile
from starcompanion.gui import theme

GUI_DIR = Path(theme.__file__).parent


# --- palettes ----------------------------------------------------------------


def test_both_themes_exist():
    assert set(theme.PALETTES) == {"dark", "light"}


def test_dark_is_the_default():
    assert theme.DEFAULT_THEME == "dark"
    assert theme.palette().name == "dark"


def test_unknown_theme_falls_back_rather_than_raising():
    """A bad value in a saved profile must not stop the app opening."""
    assert theme.palette("chartreuse").name == "dark"
    assert theme.palette(None).name == "dark"


def test_theme_lookup_is_case_insensitive():
    assert theme.palette("LIGHT").name == "light"


@pytest.mark.parametrize("name", ["dark", "light"])
def test_every_colour_is_a_valid_hex_value(name):
    colours = theme.palette(name)
    for token in theme.COLOUR_TOKENS:
        value = getattr(colours, token)
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name}.{token} = {value!r}"


def test_the_two_palettes_define_the_same_tokens():
    """Neither theme may quietly gain or lose a colour."""
    dark = {f: getattr(theme.DARK, f) for f in theme.COLOUR_TOKENS}
    light = {f: getattr(theme.LIGHT, f) for f in theme.COLOUR_TOKENS}
    assert set(dark) == set(light)


def test_the_themes_actually_differ():
    assert theme.DARK.canvas != theme.LIGHT.canvas
    assert theme.DARK.text != theme.LIGHT.text


def test_dark_is_dark_and_light_is_light():
    def brightness(value: str) -> int:
        r, g, b = (int(value[i : i + 2], 16) for i in (1, 3, 5))
        return (r * 299 + g * 587 + b * 114) // 1000

    assert brightness(theme.DARK.canvas) < 60
    assert brightness(theme.LIGHT.canvas) > 200
    assert brightness(theme.DARK.text) > 180
    assert brightness(theme.LIGHT.text) < 80


# --- stylesheet --------------------------------------------------------------


@pytest.mark.parametrize("name", ["dark", "light"])
def test_stylesheet_is_produced(name):
    css = theme.stylesheet(theme.palette(name))
    assert len(css) > 2000
    assert "QPushButton" in css and "QTabBar::tab" in css


@pytest.mark.parametrize("name", ["dark", "light"])
def test_stylesheet_has_no_unfilled_placeholders(name):
    css = theme.stylesheet(theme.palette(name))
    assert "{{" not in css and "}}" not in css
    assert "None" not in css


@pytest.mark.parametrize("name", ["dark", "light"])
def test_every_colour_token_is_used(name):
    """An unused token is either dead weight or a forgotten style."""
    colours = theme.palette(name)
    css = theme.stylesheet(colours)

    unused = [
        token for token in theme.COLOUR_TOKENS
        if getattr(colours, token) not in css
    ]
    assert unused == [], f"tokens defined but never styled: {unused}"


def test_the_two_themes_share_one_generator():
    """Same rules, different values -- so light cannot drift from dark."""
    dark = theme.stylesheet(theme.DARK)
    light = theme.stylesheet(theme.LIGHT)

    selectors = lambda css: re.findall(r"^([A-Z][^\n{]*)\{", css, re.MULTILINE)
    assert selectors(dark) == selectors(light)
    assert dark != light


def test_role_styles_exist_for_the_important_actions():
    css = theme.stylesheet(theme.DARK)
    assert 'QPushButton[role="primary"]' in css
    assert 'QPushButton[role="danger"]' in css
    assert 'QPushButton[role="nav"]' in css
    assert 'QLabel[role="badge"]' in css
    assert "QFrame#AppSidebar" in css


def test_applying_the_same_global_theme_is_idempotent():
    class FakeApplication:
        def __init__(self):
            self.properties = {}
            self.stylesheets = []
            self.palettes = []

        def property(self, name):
            return self.properties.get(name)

        def setProperty(self, name, value):
            self.properties[name] = value

        def setStyleSheet(self, value):
            self.stylesheets.append(value)

        def setPalette(self, value):
            self.palettes.append(value)

    app = FakeApplication()

    theme.apply_theme(app, "dark")
    theme.apply_theme(app, "dark")
    theme.apply_theme(app, "light")

    assert len(app.stylesheets) == 2
    assert len(app.palettes) == 2


# --- no colours in widget code -----------------------------------------------


def test_widget_code_contains_no_hardcoded_colours():
    """The whole point of the token layer.

    Colours belong in theme.py. Anywhere else and the light theme breaks
    without anyone noticing.
    """
    offenders = []
    pattern = re.compile(r"#[0-9a-fA-F]{6}\b|\brgba?\s*\(")

    for path in GUI_DIR.rglob("*.py"):
        if path.name == "theme.py" or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "palette(" not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert offenders == [], "colours must come from theme.py:\n" + "\n".join(offenders)


def test_scales_are_ordered():
    s = theme.SPACING
    assert s.tiny < s.small < s.medium < s.large < s.xlarge < s.huge
    assert theme.RADIUS.none < theme.RADIUS.small < theme.RADIUS.medium


def _relative_luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    brighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (brighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("colours", [theme.DARK, theme.LIGHT])
def test_semantic_text_colours_meet_wcag_aa_contrast(colours):
    pairs = [
        (colours.text, colours.canvas),
        (colours.text, colours.surface),
        (colours.text_muted, colours.canvas),
        (colours.text_muted, colours.surface),
        (colours.text_muted, colours.surface_raised),
        (colours.text, colours.accent_muted),
        (colours.text_inverted, colours.accent),
        (colours.text_inverted, colours.danger),
        (colours.success, colours.surface),
        (colours.warning, colours.surface),
        (colours.danger, colours.surface),
    ]
    assert all(_contrast(foreground, background) >= 4.5 for foreground, background in pairs)


@pytest.mark.parametrize("colours", [theme.DARK, theme.LIGHT])
def test_focus_ring_meets_non_text_contrast(colours):
    assert _contrast(colours.focus, colours.canvas) >= 3
    assert _contrast(colours.focus, colours.surface) >= 3


def test_focus_indicators_exist_for_every_interactive_control_family():
    css = theme.stylesheet(theme.DARK)
    for selector in (
        "QPushButton:focus",
        "QLineEdit:focus",
        "QComboBox:focus",
        "QCheckBox::indicator:focus",
        "QListWidget:focus",
    ):
        assert selector in css


def test_fonts_fall_back_when_nothing_is_bundled():
    """Phase 2 ships a face; until then the app must still style correctly."""
    assert theme.ui_family().endswith("sans-serif")
    assert theme.mono_family().endswith("monospace")


# --- persistence -------------------------------------------------------------


def test_theme_defaults_to_dark_in_a_new_profile():
    assert Profile().appearance.theme == "dark"


def test_theme_round_trips_through_a_saved_profile():
    profile = Profile.model_validate({"appearance": {"theme": "light"}})
    assert Profile.loads(profile.dumps()).appearance.theme == "light"


def test_profiles_written_before_theming_still_load():
    """Additive with a default, so no schema bump was needed."""
    assert Profile.loads('{"schema_version": 1, "name": "old"}').appearance.theme == "dark"


def test_an_unknown_theme_name_is_rejected_at_the_profile_boundary():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Profile.model_validate({"appearance": {"theme": "chartreuse"}})
