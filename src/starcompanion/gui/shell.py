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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._specs: list[PageSpec] = []
        self._nav_buttons: list[QPushButton] = []
        self._shortcuts: list[QShortcut] = []
        self._sections: set[str] = set()

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
        strapline = QLabel("LOCAL CONTRACT INTELLIGENCE")
        strapline.setProperty("role", "overline")
        brand_text.addWidget(brand)
        brand_text.addWidget(strapline)
        brand_row.addLayout(brand_text, 1)
        side.addLayout(brand_row)
        side.addSpacing(SPACING.xlarge)

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setSpacing(SPACING.tiny)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        side.addLayout(self.nav_layout)
        side.addStretch(1)

        local = QLabel("LOCAL-FIRST / NO TELEMETRY")
        local.setProperty("role", "security")
        local.setWordWrap(True)
        side.addWidget(local)

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
        action_row.addWidget(self.profile_button)
        action_row.addWidget(self.theme_button)
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
        self.status_text = QLabel(
            "OFFLINE BY DESIGN   |   NO TELEMETRY   |   WRITES REQUIRE CONFIRMATION"
        )
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
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Right):
                self._activate_navigation((index + 1) % len(self._nav_buttons))
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Left):
                self._activate_navigation((index - 1) % len(self._nav_buttons))
                return True
            if key == Qt.Key.Key_Home:
                self._activate_navigation(0)
                return True
            if key == Qt.Key.Key_End:
                self._activate_navigation(len(self._nav_buttons) - 1)
                return True
        return super().eventFilter(watched, event)

    def _activate_navigation(self, index: int) -> None:
        self.set_current_page(index)
        self._nav_buttons[index].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_current_page(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    def set_current_key(self, key: str) -> bool:
        for index, spec in enumerate(self._specs):
            if spec.key == key:
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
        self.profile_button.setText(f"PROFILE / {profile.upper()}")
        self.game_badge.setText(f"GAME / {(game or 'NOT FOUND').upper()}")
        self.data_badge.setText(f"DATA / {(data or 'NOT LOADED').upper()}")

    def set_theme_name(self, current: str) -> None:
        destination = "LIGHT" if current == "dark" else "DARK"
        self.theme_button.setText(f"{destination} THEME")

    def _current_changed(self, index: int) -> None:
        if not 0 <= index < len(self._specs):
            return
        self._nav_buttons[index].setChecked(True)
        spec = self._specs[index]
        self._show_spec(spec)
        self.pageChanged.emit(spec.key)

    def _show_spec(self, spec: PageSpec) -> None:
        self.page_title.setText(spec.title)
        self.page_description.setText(spec.description)
