"""Professional application chrome around the existing feature pages.

The shell owns navigation and presentation only. Feature widgets remain the
same objects and continue to talk directly to :class:`AppState`, which keeps
the verified C0--C5 behavior outside the C6 visual boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import SPACING
from .ui_text import UiTranslator


@dataclass(frozen=True)
class PageSpec:
    """Navigation and presentation metadata for an existing feature page."""

    key: str
    legacy_label: str
    nav_label: str
    title: str
    description: str
    section: str
    scrollable: bool = True


class NavigationStack(QStackedWidget):
    """A stack with the small QTabWidget API retained for compatibility."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName("StarCompanion application navigation and workspace")
        self._tab_labels: list[str] = []

    def add_page(self, page: QWidget, label: str, *, scrollable: bool) -> int:
        host = self._host(page, scrollable=scrollable)
        index = self.addWidget(host)
        self._tab_labels.append(label)
        return index

    def tabText(self, index: int) -> str:  # noqa: N802 - Qt compatibility
        return self._tab_labels[index]

    @staticmethod
    def _host(page: QWidget, *, scrollable: bool) -> QWidget:
        canvas = QWidget()
        canvas.setObjectName("PageCanvas")
        layout = QHBoxLayout(canvas)
        layout.setContentsMargins(
            SPACING.xlarge,
            SPACING.large,
            SPACING.xlarge,
            SPACING.xlarge,
        )

        if not scrollable:
            layout.addWidget(page, 1)
            return canvas

        page.setMaximumWidth(1040)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        content = QWidget()
        content.setObjectName("PageScrollContent")
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout = QHBoxLayout(content)
        # A page's desktop size hint must not become the viewport's minimum
        # width. Responsive pages reflow their own splitters and long labels.
        content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        content_layout.setContentsMargins(0, 0, SPACING.small, 0)
        content_layout.addWidget(page, 1, Qt.AlignmentFlag.AlignTop)

        viewport = QScrollArea()
        viewport.setObjectName("PageViewport")
        viewport.setFrameShape(QFrame.Shape.NoFrame)
        viewport.setWidgetResizable(True)
        viewport.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        viewport.setWidget(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(viewport)
        return canvas


class ApplicationShell(QWidget):
    """Persistent navigation, page identity, and local-operation context."""

    pageChanged = Signal(str)
    themeRequested = Signal()
    simpleModeRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._specs: list[PageSpec] = []
        self._nav_buttons: list[QPushButton] = []
        self._shortcuts: list[QShortcut] = []
        self._sections: set[str] = set()
        self._section_labels: dict[str, QLabel] = {}
        self._translator = UiTranslator()
        self.simple_mode = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("AppSidebar")
        self.sidebar.setFixedWidth(244)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(
            SPACING.large,
            SPACING.xlarge,
            SPACING.large,
            SPACING.large,
        )
        side.setSpacing(SPACING.small)

        brand_row = QHBoxLayout()
        brand_mark = QFrame()
        brand_mark.setObjectName("BrandMark")
        brand_mark.setFixedSize(4, 42)
        brand_row.addWidget(brand_mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel("STARCOMPANION")
        brand.setProperty("role", "brand")
        self.strapline = QLabel(self._translator.text("app.strapline"))
        self.strapline.setProperty("role", "overline")
        brand_text.addWidget(brand)
        brand_text.addWidget(self.strapline)
        brand_row.addLayout(brand_text, 1)
        side.addLayout(brand_row)
        side.addSpacing(SPACING.xlarge)

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setSpacing(SPACING.tiny)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        side.addLayout(self.nav_layout)
        side.addStretch(1)

        self.local_label = QLabel(self._translator.text("app.local-first"))
        self.local_label.setProperty("role", "security")
        self.local_label.setWordWrap(True)
        side.addWidget(self.local_label)

        main = QWidget()
        main.setObjectName("AppMain")
        self.main = main
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("AppHeader")
        self.header = header
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(
            SPACING.xlarge,
            SPACING.large,
            SPACING.xlarge,
            SPACING.large,
        )
        header_layout.setSpacing(SPACING.large)

        page_identity = QVBoxLayout()
        page_identity.setSpacing(SPACING.tiny)
        self.page_title = QLabel()
        self.page_title.setProperty("role", "page-title")
        self.page_description = QLabel()
        self.page_description.setProperty("role", "page-description")
        self.page_description.setWordWrap(True)
        page_identity.addWidget(self.page_title)
        page_identity.addWidget(self.page_description)
        header_layout.addLayout(page_identity, 1)

        context = QVBoxLayout()
        context.setSpacing(SPACING.small)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(SPACING.small)
        self.game_badge = self._badge("GAME / NOT FOUND")
        self.data_badge = self._badge("DATA / NOT LOADED")
        badge_row.addWidget(self.game_badge)
        badge_row.addWidget(self.data_badge)
        context.addLayout(badge_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(SPACING.small)
        self.profile_button = QPushButton("PROFILE / DEFAULT")
        self.profile_button.setProperty("role", "compact")
        self.profile_button.setAccessibleName("Choose or save a profile")
        self.profile_button.setAccessibleDescription(
            "Shows the active output profile and opens profile actions."
        )
        self.theme_button = QPushButton("LIGHT THEME")
        self.theme_button.setProperty("role", "compact")
        self.theme_button.setAccessibleName("Switch application theme")
        self.theme_button.setAccessibleDescription(
            "Changes only the application appearance, not rendered game output."
        )
        self.theme_button.clicked.connect(self.themeRequested)
        self.mode_button = QPushButton("SIMPLE MODE")
        self.mode_button.setProperty("role", "compact")
        self.mode_button.setAccessibleName("Switch simple or full workspace mode")
        self.mode_button.setAccessibleDescription(
            "Simple mode keeps the existing safe Update and Undo workflow; full mode restores every workspace page."
        )
        self.mode_button.clicked.connect(self.simpleModeRequested)
        action_row.addWidget(self.profile_button)
        action_row.addWidget(self.theme_button)
        action_row.addWidget(self.mode_button)
        context.addLayout(action_row)
        header_layout.addLayout(context)

        self.stack = NavigationStack()

        status = QFrame()
        status.setObjectName("AppStatus")
        self.status = status
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(
            SPACING.xlarge,
            SPACING.small,
            SPACING.xlarge,
            SPACING.small,
        )
        self.status_text = QLabel(self._translator.text("app.privacy"))
        self.status_text.setProperty("role", "security")
        self.status_text.setAccessibleName("Privacy and write-safety status")
        self.status_text.setAccessibleDescription(self.status_text.text())
        status_layout.addWidget(self.status_text)
        status_layout.addStretch(1)

        main_layout.addWidget(header)
        main_layout.addWidget(self.stack, 1)
        main_layout.addWidget(status)
        root.addWidget(self.sidebar)
        root.addWidget(main, 1)

        self.stack.currentChanged.connect(self._current_changed)

    @staticmethod
    def _badge(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "badge")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def add_page(self, page: QWidget, spec: PageSpec) -> int:
        page.setAccessibleName(spec.title)
        page.setAccessibleDescription(spec.description)
        if spec.section not in self._sections:
            if self._sections:
                self.nav_layout.addSpacing(SPACING.large)
            section = QLabel(spec.section.upper())
            section.setProperty("role", "nav-section")
            self.nav_layout.addWidget(section)
            self._sections.add(spec.section)
            self._section_labels[spec.section] = section

        index = self.stack.add_page(
            page,
            spec.legacy_label,
            scrollable=spec.scrollable,
        )
        self._specs.append(spec)

        # Ampersands mark native button mnemonics in Qt. Navigation already
        # has stable Alt+number shortcuts, so escape them for literal display.
        button = QPushButton(spec.nav_label.replace("&", "&&"))
        button.setProperty("navigationLabel", spec.nav_label)
        button.setProperty("role", "nav")
        button.setCheckable(True)
        button.setAccessibleName(f"Open {spec.nav_label}")
        button.setAccessibleDescription(
            f"Navigate to {spec.title}. Keyboard shortcut Alt+{index + 1}."
        )
        button.installEventFilter(self)
        button.clicked.connect(lambda _checked=False, i=index: self.set_current_page(i))
        self.nav_group.addButton(button, index)
        self.nav_layout.addWidget(button)
        self._nav_buttons.append(button)
        if len(self._nav_buttons) > 1:
            QWidget.setTabOrder(self._nav_buttons[-2], button)

        shortcut = QShortcut(QKeySequence(f"Alt+{index + 1}"), self)
        shortcut.activated.connect(lambda i=index: self._activate_navigation(i))
        self._shortcuts.append(shortcut)

        if index == 0:
            button.setChecked(True)
            self._show_spec(spec)
        return index

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched in self._nav_buttons and event.type() == QEvent.Type.KeyPress:
            index = self._nav_buttons.index(watched)
            visible = [
                position
                for position, button in enumerate(self._nav_buttons)
                if not button.isHidden()
            ]
            position = visible.index(index) if index in visible else 0
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Right):
                self._activate_navigation(visible[(position + 1) % len(visible)])
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Left):
                self._activate_navigation(visible[(position - 1) % len(visible)])
                return True
            if key == Qt.Key.Key_Home:
                self._activate_navigation(visible[0])
                return True
            if key == Qt.Key.Key_End:
                self._activate_navigation(visible[-1])
                return True
        return super().eventFilter(watched, event)

    def _activate_navigation(self, index: int) -> None:
        if self._nav_buttons[index].isHidden():
            return
        self.set_current_page(index)
        self._nav_buttons[index].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_current_page(self, index: int) -> None:
        if (
            0 <= index < self.stack.count()
            and (not self.simple_mode or self._specs[index].key == "overview")
        ):
            self.stack.setCurrentIndex(index)

    def set_current_key(self, key: str) -> bool:
        for index, spec in enumerate(self._specs):
            if spec.key == key:
                if self.simple_mode and key != "overview":
                    return False
                self.set_current_page(index)
                return True
        return False

    def current_key(self) -> str:
        index = self.stack.currentIndex()
        if 0 <= index < len(self._specs):
            return self._specs[index].key
        return ""

    def set_context(
        self,
        *,
        profile: str,
        game: str | None,
        data: str | None,
    ) -> None:
        text = self._translator.text
        self.profile_button.setText(f"{text('app.profile')} / {profile.upper()}")
        self.game_badge.setText(
            f"{text('app.game')} / {(game or text('app.not-found')).upper()}"
        )
        self.data_badge.setText(
            f"{text('app.data')} / {(data or text('app.not-loaded')).upper()}"
        )

    def set_theme_name(self, current: str) -> None:
        self.theme_button.setText(
            f"{self._translator.text('app.theme')} / {current.replace('-', ' ').upper()}"
        )

    def set_simple_mode(self, enabled: bool) -> None:
        self.simple_mode = bool(enabled)
        if self.simple_mode and self.current_key() != "overview":
            self.set_current_page(0)
        for index, button in enumerate(self._nav_buttons):
            visible = not self.simple_mode or self._specs[index].key == "overview"
            button.setVisible(visible)
            self._shortcuts[index].setEnabled(visible)
        for section, label in self._section_labels.items():
            label.setVisible(
                any(
                    not button.isHidden() and spec.section == section
                    for button, spec in zip(self._nav_buttons, self._specs)
                )
            )
        key = "app.mode.full" if self.simple_mode else "app.mode.simple"
        self.mode_button.setText(self._translator.text(key))

    def set_translator(self, translator: UiTranslator) -> None:
        self._translator = translator
        text = translator.text
        self.strapline.setText(text("app.strapline"))
        self.local_label.setText(text("app.local-first"))
        self.status_text.setText(text("app.privacy"))
        self.status_text.setAccessibleDescription(self.status_text.text())
        for spec, button in zip(self._specs, self._nav_buttons):
            label = text(f"nav.{spec.key}", spec.nav_label)
            button.setText(label.replace("&", "&&"))
            button.setProperty("navigationLabel", label)
            button.setAccessibleName(f"Open {label}")
        for section, label in self._section_labels.items():
            label.setText(text(f"section.{section.casefold()}", section.upper()))
        if self._specs:
            self._show_spec(self._specs[self.stack.currentIndex()])
        self.set_simple_mode(self.simple_mode)

    def _current_changed(self, index: int) -> None:
        if not 0 <= index < len(self._specs):
            return
        self._nav_buttons[index].setChecked(True)
        spec = self._specs[index]
        self._show_spec(spec)
        self.pageChanged.emit(spec.key)

    def _show_spec(self, spec: PageSpec) -> None:
        self.page_title.setText(
            self._translator.text(f"page.{spec.key}.title", spec.title)
        )
        self.page_description.setText(
            self._translator.text(f"page.{spec.key}.description", spec.description)
        )
