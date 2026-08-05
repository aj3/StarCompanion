"""Design tokens and the stylesheet built from them.

Every colour, space and radius in the interface comes from here. Widget code
never names a colour, so the two themes cannot drift apart: there is **one**
stylesheet generator and two palettes fed through it.

The dark palette is the intended look -- near-black blue-greys, a single cyan
accent, amber reserved for warnings and a desaturated red for danger. The light
palette exists because people ask for it, and is derived by inverting the
surface/text relationship rather than by writing a second set of rules.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

ThemeName = Literal["dark", "light"]
DEFAULT_THEME: ThemeName = "dark"


# --- scales ------------------------------------------------------------------

@dataclass(frozen=True)
class Spacing:
    """A fixed rhythm, so padding stops being decided per widget."""

    tiny: int = 4
    small: int = 8
    medium: int = 12
    large: int = 16
    xlarge: int = 24
    huge: int = 32


@dataclass(frozen=True)
class Radius:
    none: int = 0
    small: int = 3
    medium: int = 6
    large: int = 10


SPACING = Spacing()
RADIUS = Radius()


# --- palette -----------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    """Semantic colours. Names describe the role, never the hue, so the light
    theme can use a different colour for the same job."""

    name: ThemeName

    # Surfaces, back to front.
    canvas: str
    """The window behind everything."""
    surface: str
    """Panels and cards sitting on the canvas."""
    surface_raised: str
    """Inputs and controls sitting on a surface."""
    surface_hover: str

    # Lines.
    border: str
    border_strong: str

    # Text.
    text: str
    text_muted: str
    text_inverted: str
    """For text sitting on an accent fill."""

    # Meaning.
    accent: str
    accent_hover: str
    accent_muted: str
    success: str
    warning: str
    danger: str
    danger_hover: str

    # Focus ring, kept separate so it can be louder than the accent.
    focus: str


DARK = Palette(
    name="dark",
    canvas="#0b0f14",
    surface="#121820",
    surface_raised="#1a2029",
    surface_hover="#222a35",
    border="#232c38",
    border_strong="#33404f",
    text="#e6edf3",
    text_muted="#8b9bad",
    text_inverted="#06131a",
    accent="#3fd0d4",
    accent_hover="#5fe0e3",
    accent_muted="#1d5f63",
    success="#4cc98a",
    warning="#e0a44a",
    danger="#e0645f",
    danger_hover="#ec7d78",
    focus="#3fd0d4",
)

LIGHT = Palette(
    name="light",
    canvas="#eef1f5",
    surface="#ffffff",
    surface_raised="#f4f6f9",
    surface_hover="#e7ebf1",
    border="#d3dae3",
    border_strong="#b3bec9",
    text="#16202b",
    text_muted="#5c6b7c",
    text_inverted="#ffffff",
    accent="#12787c",
    accent_hover="#0e6165",
    accent_muted="#a8dcde",
    success="#1e7a52",
    warning="#9a6612",
    danger="#b23b37",
    danger_hover="#8f2f2c",
    focus="#12787c",
)

PALETTES: dict[str, Palette] = {"dark": DARK, "light": LIGHT}

COLOUR_TOKENS = tuple(f.name for f in fields(Palette) if f.name != "name")


def palette(name: str | None = None) -> Palette:
    """The named palette, falling back to the default rather than raising --
    a bad value in a saved profile should not stop the app opening."""
    return PALETTES.get((name or DEFAULT_THEME).casefold(), PALETTES[DEFAULT_THEME])


# --- typography --------------------------------------------------------------

@dataclass(frozen=True)
class TextStyle:
    size: int
    weight: int
    """CSS weight: 400 normal, 600 semibold, 700 bold."""
    spacing: float = 0.0
    """Letter spacing in px; small positive values read as 'technical'."""


@dataclass(frozen=True)
class TypeScale:
    display: TextStyle = TextStyle(22, 700, 1.5)
    heading: TextStyle = TextStyle(15, 600, 0.6)
    body: TextStyle = TextStyle(11, 400)
    caption: TextStyle = TextStyle(10, 400)
    mono: TextStyle = TextStyle(11, 400)


TYPE = TypeScale()

# Native Windows faces are preferred, with generic fallbacks retained for
# Linux and for minimal CI images. No font files or licenses need shipping.
FONT_FAMILY_UI = "Segoe UI Variable"
FONT_FAMILY_MONO = "Cascadia Mono"


def _family(preferred: str, fallback: str) -> str:
    return f'"{preferred}", {fallback}' if preferred else fallback


def ui_family() -> str:
    return _family(FONT_FAMILY_UI, "sans-serif")


def mono_family() -> str:
    return _family(FONT_FAMILY_MONO, "monospace")


# --- stylesheet --------------------------------------------------------------


def stylesheet(colours: Palette) -> str:
    """The whole interface's QSS, built from one palette.

    Written as a single generator on purpose: a second hand-written light
    stylesheet would drift from the dark one within a week.
    """
    s, r = SPACING, RADIUS

    return f"""
/* --- base ------------------------------------------------------------- */
QWidget {{
    background-color: {colours.canvas};
    color: {colours.text};
    font-family: {ui_family()};
    font-size: {TYPE.body.size}pt;
}}

QMainWindow, QDialog {{
    background-color: {colours.canvas};
}}

QLabel {{
    background: transparent;
}}

/* --- application shell ---------------------------------------------- */
QFrame#AppSidebar {{
    background-color: {colours.surface};
    border-right: 1px solid {colours.border};
}}

QFrame#BrandMark {{
    background-color: {colours.accent};
    border-radius: 2px;
}}

QWidget#AppMain, QWidget#PageCanvas, QWidget#PageScrollContent,
QScrollArea#PageViewport, QScrollArea#PageViewport > QWidget > QWidget {{
    background-color: {colours.canvas};
    border: none;
}}

QFrame#AppHeader {{
    background-color: {colours.surface};
    border-bottom: 1px solid {colours.border};
}}

QFrame#AppStatus {{
    background-color: {colours.surface};
    border-top: 1px solid {colours.border};
}}

QLabel[role="brand"] {{
    color: {colours.text};
    font-size: {TYPE.heading.size}pt;
    font-weight: 700;
    letter-spacing: 1.4px;
}}

QLabel[role="overline"], QLabel[role="nav-section"] {{
    color: {colours.text_muted};
    font-size: {TYPE.caption.size}pt;
    font-weight: 600;
    letter-spacing: 1.1px;
}}

QLabel[role="nav-section"] {{
    padding: {s.tiny}px {s.small}px;
}}

QLabel[role="page-title"] {{
    color: {colours.text};
    font-size: {TYPE.display.size}pt;
    font-weight: {TYPE.display.weight};
    letter-spacing: 0.3px;
}}

QLabel[role="page-description"] {{
    color: {colours.text_muted};
    font-size: {TYPE.body.size}pt;
}}

QLabel[role="badge"] {{
    background-color: {colours.surface_raised};
    color: {colours.text_muted};
    border: 1px solid {colours.border};
    border-radius: {r.large}px;
    padding: {s.tiny}px {s.small}px;
    font-size: {TYPE.caption.size}pt;
    font-weight: 600;
    letter-spacing: 0.5px;
}}

QLabel[role="security"] {{
    color: {colours.text_muted};
    font-size: {TYPE.caption.size}pt;
    font-weight: 600;
    letter-spacing: 0.7px;
}}

QLabel[role="hero-title"] {{
    color: {colours.text};
    font-size: {TYPE.display.size}pt;
    font-weight: {TYPE.display.weight};
    letter-spacing: 0.2px;
}}

QLabel[role="status-title"] {{
    color: {colours.text};
    font-size: {TYPE.heading.size}pt;
    font-weight: {TYPE.heading.weight};
}}

QLabel[role="empty-title"] {{
    color: {colours.text};
    font-weight: 600;
}}

QLabel[role="section-title"] {{
    color: {colours.text};
    font-size: {TYPE.heading.size}pt;
    font-weight: {TYPE.heading.weight};
    letter-spacing: 0.2px;
}}

QLabel[role="metric-value"] {{
    color: {colours.text};
    font-size: {TYPE.heading.size}pt;
    font-weight: 700;
}}

/* --- reusable state components -------------------------------------- */
QFrame[component="dashboard-hero"] {{
    background-color: {colours.surface};
    border: 1px solid {colours.border};
    border-left: 4px solid {colours.accent};
    border-radius: {r.large}px;
}}

QFrame[component="dashboard-hero"][tone="warning"] {{
    border-left-color: {colours.warning};
}}

QFrame[component="dashboard-hero"][tone="danger"] {{
    border-left-color: {colours.danger};
}}

QFrame[component="dashboard-hero"][tone="success"] {{
    border-left-color: {colours.success};
}}

QFrame[component="status-card"] {{
    background-color: {colours.surface};
    border: 1px solid {colours.border};
    border-top: 3px solid {colours.border_strong};
    border-radius: {r.large}px;
}}

QFrame[component="status-card"][tone="info"] {{
    border-top-color: {colours.accent};
}}

QFrame[component="status-card"][tone="success"] {{
    border-top-color: {colours.success};
}}

QFrame[component="status-card"][tone="warning"] {{
    border-top-color: {colours.warning};
}}

QFrame[component="status-card"][tone="danger"] {{
    border-top-color: {colours.danger};
}}

QFrame[component="empty-state"] {{
    background-color: {colours.surface_raised};
    border: 1px dashed {colours.border_strong};
    border-radius: {r.medium}px;
}}

QFrame[component="notice"] {{
    background-color: {colours.surface_raised};
    border: 1px solid {colours.border};
    border-left: 3px solid {colours.accent};
    border-radius: {r.medium}px;
}}

QFrame[component="notice"][tone="success"] {{
    border-left-color: {colours.success};
}}

QFrame[component="notice"][tone="warning"] {{
    border-left-color: {colours.warning};
}}

QFrame[component="notice"][tone="danger"] {{
    border-left-color: {colours.danger};
}}

QFrame[component="section-card"] {{
    background-color: {colours.surface};
    border: 1px solid {colours.border};
    border-radius: {r.large}px;
}}

QFrame[component="toggle-row"] {{
    background-color: {colours.surface_raised};
    border: 1px solid {colours.border};
    border-radius: {r.medium}px;
}}

QFrame[component="toggle-row"]:hover {{
    background-color: {colours.surface_hover};
    border-color: {colours.border_strong};
}}

QFrame[component="metric"] {{
    background-color: {colours.surface_raised};
    border: 1px solid {colours.border};
    border-radius: {r.medium}px;
}}

QPushButton[role="nav"] {{
    background-color: transparent;
    color: {colours.text_muted};
    border: 1px solid transparent;
    border-radius: {r.medium}px;
    padding: {s.medium}px {s.medium}px;
    text-align: left;
    font-weight: 600;
}}

QPushButton[role="nav"]:hover {{
    background-color: {colours.surface_hover};
    color: {colours.text};
    border-color: transparent;
}}

QPushButton[role="nav"]:checked {{
    background-color: {colours.accent_muted};
    color: {colours.text};
    border-left: 3px solid {colours.accent};
}}

QPushButton[role="compact"] {{
    background-color: transparent;
    color: {colours.text_muted};
    border-color: {colours.border};
    border-radius: {r.medium}px;
    padding: {s.tiny}px {s.medium}px;
    font-size: {TYPE.caption.size}pt;
    font-weight: 600;
    letter-spacing: 0.4px;
}}

QPushButton[role="compact"]:hover {{
    color: {colours.text};
    border-color: {colours.accent_muted};
}}

/* --- tabs ------------------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {colours.border};
    border-radius: {r.medium}px;
    background-color: {colours.surface};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    color: {colours.text_muted};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    padding: {s.small}px {s.large}px;
    margin-right: {s.tiny}px;
    font-weight: {TYPE.heading.weight};
}}

QTabBar::tab:hover {{
    color: {colours.text};
}}

QTabBar::tab:selected {{
    color: {colours.accent};
    border-bottom: 2px solid {colours.accent};
}}

/* --- panels ----------------------------------------------------------- */
QGroupBox {{
    background-color: {colours.surface};
    border: 1px solid {colours.border};
    border-radius: {r.medium}px;
    margin-top: {s.large}px;
    padding: {s.large}px {s.medium}px {s.medium}px {s.medium}px;
    font-weight: {TYPE.heading.weight};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {s.medium}px;
    padding: 0 {s.small}px;
    color: {colours.text_muted};
    letter-spacing: {TYPE.heading.spacing}px;
}}

/* --- buttons ---------------------------------------------------------- */
QPushButton {{
    background-color: {colours.surface_raised};
    color: {colours.text};
    border: 1px solid {colours.border_strong};
    border-radius: {r.small}px;
    padding: {s.small}px {s.large}px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {colours.surface_hover};
    border-color: {colours.accent_muted};
}}

QPushButton:focus {{
    border: 2px solid {colours.focus};
}}

QPushButton:pressed {{
    background-color: {colours.surface};
}}

QPushButton:disabled {{
    color: {colours.text_muted};
    border-color: {colours.border};
    background-color: {colours.surface};
}}

/* The one action a screen is asking for. */
QPushButton[role="primary"] {{
    background-color: {colours.accent};
    color: {colours.text_inverted};
    border: 1px solid {colours.accent};
    font-weight: 700;
    letter-spacing: {TYPE.heading.spacing}px;
}}

QPushButton[role="primary"]:hover {{
    background-color: {colours.accent_hover};
    border-color: {colours.accent_hover};
}}

QPushButton[role="primary"]:disabled {{
    background-color: {colours.accent_muted};
    border-color: {colours.accent_muted};
    color: {colours.text_muted};
}}

/* Anything that writes to the game must not look ordinary. */
QPushButton[role="danger"] {{
    background-color: transparent;
    color: {colours.danger};
    border: 1px solid {colours.danger};
}}

QPushButton[role="danger"]:hover {{
    background-color: {colours.danger};
    color: {colours.text_inverted};
    border-color: {colours.danger_hover};
}}

QPushButton[role="danger"]:disabled {{
    background-color: {colours.surface};
    color: {colours.text_muted};
    border-color: {colours.border};
}}

/* --- inputs ----------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{
    background-color: {colours.surface_raised};
    color: {colours.text};
    border: 1px solid {colours.border_strong};
    border-radius: {r.small}px;
    padding: {s.tiny}px {s.small}px;
    selection-background-color: {colours.accent_muted};
    selection-color: {colours.text};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{
    border-color: {colours.focus};
}}

QPlainTextEdit, QTextEdit {{
    font-family: {mono_family()};
}}

QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled {{
    color: {colours.text_muted};
    background-color: {colours.surface};
}}

/* --- combo boxes ------------------------------------------------------ */
QComboBox {{
    background-color: {colours.surface_raised};
    color: {colours.text};
    border: 1px solid {colours.border_strong};
    border-radius: {r.small}px;
    padding: {s.tiny}px {s.small}px;
    min-height: 20px;
}}

QComboBox:hover {{
    border-color: {colours.accent_muted};
}}

QComboBox:focus {{
    border-color: {colours.focus};
}}

QComboBox::drop-down {{
    border: none;
    width: {s.xlarge}px;
}}

QComboBox QAbstractItemView {{
    background-color: {colours.surface_raised};
    color: {colours.text};
    border: 1px solid {colours.border_strong};
    selection-background-color: {colours.accent_muted};
    selection-color: {colours.text};
    outline: none;
}}

/* --- check boxes ------------------------------------------------------ */
QCheckBox {{
    background: transparent;
    spacing: {s.small}px;
}}

QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {colours.border_strong};
    border-radius: {r.small}px;
    background-color: {colours.surface_raised};
}}

QCheckBox::indicator:hover {{
    border-color: {colours.accent};
}}

QCheckBox::indicator:checked {{
    background-color: {colours.accent};
    border-color: {colours.accent};
}}

QCheckBox:focus {{
    color: {colours.text};
}}

QCheckBox::indicator:focus {{
    border: 2px solid {colours.focus};
}}

QCheckBox:disabled {{
    color: {colours.text_muted};
}}

/* --- lists ------------------------------------------------------------ */
QListWidget, QTreeWidget, QTableView {{
    background-color: {colours.surface_raised};
    border: 1px solid {colours.border};
    border-radius: {r.small}px;
    outline: none;
}}

QListWidget::item, QTreeWidget::item, QTableView::item {{
    padding: {s.tiny}px {s.small}px;
}}

QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
    background-color: {colours.accent_muted};
    color: {colours.text};
}}

QListWidget:focus, QTreeWidget:focus, QTableView:focus {{
    border: 2px solid {colours.focus};
}}

QHeaderView::section {{
    background-color: {colours.surface};
    color: {colours.text_muted};
    border: none;
    border-bottom: 1px solid {colours.border_strong};
    padding: {s.small}px;
    font-size: {TYPE.caption.size}pt;
    font-weight: 600;
}}

/* --- labels ----------------------------------------------------------- */
QLabel[role="display"] {{
    font-size: {TYPE.display.size}pt;
    font-weight: {TYPE.display.weight};
    letter-spacing: {TYPE.display.spacing}px;
}}

QLabel[role="muted"] {{
    color: {colours.text_muted};
    font-size: {TYPE.caption.size}pt;
}}

QLabel[role="success"] {{ color: {colours.success}; }}
QLabel[role="warning"] {{ color: {colours.warning}; }}
QLabel[role="danger"]  {{ color: {colours.danger}; }}

/* Qt greys out disabled labels; the app uses that for captions, so make it
   readable rather than nearly invisible. */
QLabel:disabled {{
    color: {colours.text_muted};
}}

/* --- scrollbars ------------------------------------------------------- */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    width: 10px;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {colours.border_strong};
    border-radius: {r.small}px;
    min-height: 24px;
    min-width: 24px;
}}

QScrollBar::handle:hover {{
    background: {colours.text_muted};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- misc ------------------------------------------------------------- */
QToolTip {{
    background-color: {colours.surface_raised};
    color: {colours.text};
    border: 1px solid {colours.border_strong};
    padding: {s.tiny}px {s.small}px;
}}

QProgressDialog {{
    background-color: {colours.surface};
}}

QProgressBar {{
    background-color: {colours.surface_raised};
    border: 1px solid {colours.border};
    border-radius: {r.small}px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {colours.accent};
    border-radius: {r.small}px;
}}

QMenuBar, QMenu {{
    background-color: {colours.surface};
    color: {colours.text};
}}

QMenu::item:selected, QMenuBar::item:selected {{
    background-color: {colours.accent_muted};
}}

QToolBar {{
    background-color: {colours.surface};
    border-bottom: 1px solid {colours.border};
    spacing: {s.small}px;
    padding: {s.tiny}px;
}}

QSplitter::handle {{
    background-color: {colours.border};
}}

QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {colours.border};
}}
"""


_APPLIED_THEME_PROPERTY = "starcompanion.appliedTheme"


def apply_theme(app, name: str | None = None) -> Palette:
    """Style the whole application. Returns the palette that was applied.

    Sets the `QPalette` as well as the stylesheet: some dialogs Qt draws for
    itself ignore QSS, and would otherwise stay light inside a dark app.
    """
    from PySide6.QtGui import QColor, QPalette as QtPalette

    colours = palette(name)
    if app.property(_APPLIED_THEME_PROPERTY) == colours.name:
        return colours
    app.setStyleSheet(stylesheet(colours))

    qt = QtPalette()
    qt.setColor(QtPalette.ColorRole.Window, QColor(colours.canvas))
    qt.setColor(QtPalette.ColorRole.WindowText, QColor(colours.text))
    qt.setColor(QtPalette.ColorRole.Base, QColor(colours.surface_raised))
    qt.setColor(QtPalette.ColorRole.AlternateBase, QColor(colours.surface))
    qt.setColor(QtPalette.ColorRole.Text, QColor(colours.text))
    qt.setColor(QtPalette.ColorRole.Button, QColor(colours.surface_raised))
    qt.setColor(QtPalette.ColorRole.ButtonText, QColor(colours.text))
    qt.setColor(QtPalette.ColorRole.Highlight, QColor(colours.accent))
    qt.setColor(QtPalette.ColorRole.HighlightedText, QColor(colours.text_inverted))
    qt.setColor(QtPalette.ColorRole.ToolTipBase, QColor(colours.surface_raised))
    qt.setColor(QtPalette.ColorRole.ToolTipText, QColor(colours.text))
    qt.setColor(QtPalette.ColorRole.PlaceholderText, QColor(colours.text_muted))
    app.setPalette(qt)
    app.setProperty(_APPLIED_THEME_PROPERTY, colours.name)

    return colours


def set_role(widget, role: str | None) -> None:
    """Tag a widget so the stylesheet can treat it specially.

    Qt only re-evaluates a selector when the style is refreshed, so this does
    that too -- otherwise setting a role after construction appears to do
    nothing.
    """
    widget.setProperty("role", role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
