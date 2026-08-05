"""Reusable presentation components for consistent C6 interface states."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import SPACING


class Tone(str, Enum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


def _set_property(widget: QWidget, name: str, value: str) -> None:
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class NoticeBanner(QFrame):
    """Inline information, warning, or error that never blocks interaction."""

    def __init__(
        self,
        text: str = "",
        *,
        tone: Tone = Tone.INFO,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("component", "notice")
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            SPACING.medium,
            SPACING.small,
            SPACING.medium,
            SPACING.small,
        )
        layout.addWidget(self._label, 1)
        self.setAccessibleDescription(text)
        self.set_tone(tone)

    def setText(self, text: str) -> None:  # noqa: N802 - QLabel compatibility
        self._label.setText(text)
        self.setAccessibleDescription(text)

    def text(self) -> str:
        return self._label.text()

    def setWordWrap(self, enabled: bool) -> None:  # noqa: N802
        self._label.setWordWrap(enabled)

    def set_tone(self, tone: Tone) -> None:
        _set_property(self, "tone", tone.value)
        names = {
            Tone.NEUTRAL: "Status",
            Tone.INFO: "Information",
            Tone.SUCCESS: "Success",
            Tone.WARNING: "Warning",
            Tone.DANGER: "Error",
        }
        self.setAccessibleName(names[tone])


class EmptyState(QFrame):
    """A compact, reusable explanation for unavailable or not-yet-loaded data."""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("component", "empty-state")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.medium,
            SPACING.medium,
            SPACING.medium,
            SPACING.medium,
        )
        layout.setSpacing(SPACING.tiny)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "empty-title")
        self.description_label = QLabel(description)
        self.description_label.setProperty("role", "muted")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)


class StatusCard(QFrame):
    """A dashboard card with one concise state and optional details/actions."""

    def __init__(self, title: str, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("component", "status-card")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.large,
            SPACING.large,
            SPACING.large,
            SPACING.large,
        )
        layout.setSpacing(SPACING.small)

        self.title_label = QLabel(title.upper())
        self.title_label.setProperty("role", "overline")
        self.status_label = QLabel()
        self.status_label.setProperty("role", "status-title")
        self.status_label.setWordWrap(True)
        self.detail_label = QLabel()
        self.detail_label.setProperty("role", "muted")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.detail_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(SPACING.small)
        layout.addLayout(self.body_layout)
        layout.addStretch(1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(SPACING.small)
        layout.addLayout(self.actions_layout)

        self.set_status("Not available", Tone.NEUTRAL)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_action(self, widget: QWidget) -> None:
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.actions_layout.addWidget(widget, 1)

    def set_status(
        self,
        headline: str,
        tone: Tone = Tone.NEUTRAL,
        detail: str = "",
    ) -> None:
        self.status_label.setText(headline)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        _set_property(self, "tone", tone.value)
        self.setAccessibleName(self.title_label.text().title())
        self.setAccessibleDescription(". ".join(part for part in (headline, detail) if part))


class DashboardHero(QFrame):
    """Overview primary action and current readiness in one visual anchor."""

    def __init__(self, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("component", "dashboard-hero")
        root = QHBoxLayout(self)
        root.setContentsMargins(
            SPACING.xlarge,
            SPACING.xlarge,
            SPACING.xlarge,
            SPACING.xlarge,
        )
        root.setSpacing(SPACING.xlarge)

        copy = QVBoxLayout()
        copy.setSpacing(SPACING.tiny)
        eyebrow = QLabel("LOCAL UPDATE WORKSPACE")
        eyebrow.setProperty("role", "overline")
        self.title_label = QLabel()
        self.title_label.setProperty("role", "hero-title")
        self.title_label.setWordWrap(True)
        self.description_label = QLabel()
        self.description_label.setProperty("role", "page-description")
        self.description_label.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(self.title_label)
        copy.addWidget(self.description_label)
        root.addLayout(copy, 1)

        self.actions_layout = QVBoxLayout()
        self.actions_layout.setSpacing(SPACING.small)
        self.actions_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(self.actions_layout)

    def add_action(self, widget: QWidget) -> None:
        self.actions_layout.addWidget(widget)

    def set_message(self, title: str, description: str, tone: Tone) -> None:
        self.title_label.setText(title)
        self.description_label.setText(description)
        _set_property(self, "tone", tone.value)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)


class SectionCard(QFrame):
    """A titled settings or diagnostics section with consistent hierarchy."""

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("component", "section-card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.large,
            SPACING.large,
            SPACING.large,
            SPACING.large,
        )
        layout.setSpacing(SPACING.small)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "section-title")
        layout.addWidget(self.title_label)
        self.description_label = QLabel(description)
        self.description_label.setProperty("role", "muted")
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(bool(description))
        layout.addWidget(self.description_label)
        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(SPACING.medium)
        layout.addLayout(self.body_layout)
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.body_layout.addWidget(widget, stretch)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)


class ToggleRow(QFrame):
    """One output choice with a description that is exposed to assistive tech."""

    def __init__(
        self,
        label: str,
        description: str,
        *,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("component", "toggle-row")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.medium,
            SPACING.small,
            SPACING.medium,
            SPACING.small,
        )
        layout.setSpacing(SPACING.tiny)
        self.checkbox = QCheckBox(label)
        self.checkbox.setAccessibleName(label)
        self.checkbox.setAccessibleDescription(description)
        self.description_label = QLabel(description)
        self.description_label.setProperty("role", "muted")
        self.description_label.setWordWrap(True)
        self.description_label.setIndent(SPACING.xlarge)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.description_label)


class MetricTile(QFrame):
    """Compact aggregate value used by summaries and provider cards."""

    def __init__(
        self,
        label: str,
        value: str = "—",
        detail: str = "",
        *,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("component", "metric")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.medium,
            SPACING.small,
            SPACING.medium,
            SPACING.small,
        )
        layout.setSpacing(0)
        self.label = QLabel(label.upper())
        self.label.setProperty("role", "overline")
        self.value = QLabel(value)
        self.value.setProperty("role", "metric-value")
        self.value.setWordWrap(True)
        self.detail = QLabel(detail)
        self.detail.setProperty("role", "muted")
        self.detail.setWordWrap(True)
        self.detail.setVisible(bool(detail))
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        self.set_value(value, detail)

    def set_value(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setAccessibleName(self.label.text().title())
        self.setAccessibleDescription(". ".join(part for part in (value, detail) if part))


class ProviderHealthCard(StatusCard):
    """Reusable provider capability, aggregate coverage, and diagnostics view."""

    def __init__(self, provider: str, *, parent: QWidget | None = None):
        self.provider_id = provider
        display_name = provider.replace("-", " ").title()
        super().__init__(display_name, parent=parent)
        self.setProperty("kind", "provider-health")
        self.metadata = QLabel()
        self.metadata.setProperty("role", "muted")
        self.metadata.setWordWrap(True)
        self.add_widget(self.metadata)

        metrics = QGridLayout()
        metrics.setSpacing(SPACING.small)
        self.facts = MetricTile("Facts seen")
        self.enhanced = MetricTile("Contracts enhanced")
        self.evidence = MetricTile("Evidence links")
        self.coverage = MetricTile("Reward coverage")
        metrics.addWidget(self.facts, 0, 0)
        metrics.addWidget(self.enhanced, 0, 1)
        metrics.addWidget(self.evidence, 0, 2)
        metrics.addWidget(self.coverage, 0, 3)
        self.body_layout.addLayout(metrics)

        self.diagnostic = NoticeBanner(tone=Tone.WARNING)
        self.diagnostic.setVisible(False)
        self.add_widget(self.diagnostic)

    def set_health(
        self,
        *,
        status: str,
        tone: Tone,
        version: str,
        build: str,
        facts_seen: int,
        contracts_enhanced: int,
        evidence_links: int,
        matched_facts: int,
        reward_facts: int,
        unmatched_facts: int,
        diagnostic: str = "",
    ) -> None:
        self.set_status(status, tone)
        self.metadata.setText(
            f"{self.provider_id} / provider {version} / build {build or 'unknown'}"
        )
        self.facts.set_value(f"{facts_seen:,}")
        self.enhanced.set_value(f"{contracts_enhanced:,}")
        self.evidence.set_value(f"{evidence_links:,}")
        coverage = f"{matched_facts:,} / {reward_facts:,}" if reward_facts else "Not reported"
        self.coverage.set_value(coverage, f"{unmatched_facts:,} unmatched")
        self.diagnostic.setText(diagnostic)
        self.diagnostic.setVisible(bool(diagnostic))
        self.setAccessibleDescription(
            f"{status}. Provider {version}. Build {build or 'unknown'}. "
            f"{contracts_enhanced:,} contracts enhanced. {evidence_links:,} evidence links."
        )


class OperationPlanView(QFrame):
    """Serializable operation-plan outcomes and per-key source provenance."""

    _OUTCOMES = (
        ("add", "Added", "added"),
        ("change", "Changed", "updated"),
        ("remove", "Removed", "removed"),
        ("unchanged", "Unchanged", "unchanged"),
        ("skipped", "Skipped", "skipped"),
        ("warning", "Warning", "warnings"),
        ("error", "Error", "errors"),
    )

    def __init__(self, *, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("component", "section-card")
        self._plan = None
        root = QVBoxLayout(self)
        root.setContentsMargins(
            SPACING.large,
            SPACING.large,
            SPACING.large,
            SPACING.large,
        )
        root.setSpacing(SPACING.small)

        title = QLabel("Operation plan")
        title.setProperty("role", "section-title")
        root.addWidget(title)
        description = QLabel(
            "Fingerprint-locked C3 diff with per-key winning-source evidence."
        )
        description.setProperty("role", "muted")
        description.setWordWrap(True)
        root.addWidget(description)
        self.summary_label = QLabel("Prepare a preview to review every outcome.")
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty("role", "muted")
        root.addWidget(self.summary_label)

        metrics = QGridLayout()
        metrics.setSpacing(SPACING.small)
        self.added_metric = MetricTile("Add")
        self.changed_metric = MetricTile("Change")
        self.removed_metric = MetricTile("Remove")
        self.unchanged_metric = MetricTile("Unchanged")
        for column, metric in enumerate(
            (
                self.added_metric,
                self.changed_metric,
                self.removed_metric,
                self.unchanged_metric,
            )
        ):
            metrics.addWidget(metric, 0, column)
        root.addLayout(metrics)

        self.metadata = QLabel()
        self.metadata.setProperty("role", "muted")
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        root.addWidget(self.metadata)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Show")
        self.filter = QComboBox()
        self.filter.setAccessibleName("Operation outcome filter")
        self.filter.setAccessibleDescription(
            "Filter the reviewed operation plan by add, change, remove, unchanged, "
            "skipped, warning, or error outcome."
        )
        self.filter.addItem("All reviewed outcomes", "all")
        for key, label, _attribute in self._OUTCOMES:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self._populate)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.filter, 1)
        root.addLayout(filter_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(("Outcome", "Localization key", "Source and state"))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setAccessibleName("Reviewed localization differences")
        self.tree.setAccessibleDescription(
            "Every operation-plan outcome with its winning source and conflict state."
        )
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(2, 190)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.tree, 1)

        self.validation = NoticeBanner(tone=Tone.INFO)
        root.addWidget(self.validation)
        self.clear_plan()
        self.setAccessibleName("Reviewed operation plan")

    def clear_plan(self, message: str = "Prepare a preview to review every outcome.") -> None:
        self._plan = None
        for metric in (
            self.added_metric,
            self.changed_metric,
            self.removed_metric,
            self.unchanged_metric,
        ):
            metric.set_value("—")
        self.metadata.setText("No serialized plan is active.")
        self.summary_label.setText(message)
        self.tree.clear()
        self.validation.set_tone(Tone.INFO)
        self.validation.setText(message)
        self.setAccessibleDescription(message)

    def set_plan(self, plan) -> None:
        self._plan = plan
        self.summary_label.setText(plan.summary())
        self.added_metric.set_value(f"{len(plan.added):,}")
        self.changed_metric.set_value(f"{len(plan.updated):,}")
        self.removed_metric.set_value(f"{len(plan.removed):,}")
        self.unchanged_metric.set_value(f"{len(plan.unchanged):,}")
        target_id = "missing override"
        if plan.target_fingerprint and plan.target_fingerprint.sha256:
            target_id = f"{plan.target_fingerprint.sha256[:12]}…"
        result_id = f"{plan.desired_sha256[:12]}…" if plan.desired_sha256 else "unbound"
        plan_id = f"{plan.plan_id[:12]}…" if plan.plan_id else "unbound"
        self.metadata.setText(
            f"Plan {plan_id}  /  Target {target_id}  /  Result {result_id}\n"
            f"{plan.channel or 'manual'}  /  {plan.language or 'unknown'}  /  "
            f"{plan.mode or 'unknown'}  /  baseline {plan.baseline_source or 'unknown'}"
        )
        self.metadata.setToolTip(
            "Source precedence: "
            + (" → ".join(plan.source_precedence) or "not reported")
        )
        issue_count = len(plan.errors) + len(plan.warnings)
        if plan.errors:
            self.validation.set_tone(Tone.DANGER)
            self.validation.setText(
                f"Blocked: {len(plan.errors):,} validation errors, "
                f"{len(plan.warnings):,} warnings, and {len(plan.skipped):,} skipped keys."
            )
        elif plan.warnings or plan.skipped:
            self.validation.set_tone(Tone.WARNING)
            self.validation.setText(
                f"Review {len(plan.warnings):,} warnings and "
                f"{len(plan.skipped):,} skipped keys before applying."
            )
        else:
            self.validation.set_tone(Tone.SUCCESS)
            self.validation.setText("Validation passed. The target fingerprint is locked to this plan.")
        self._populate()
        self.setAccessibleDescription(
            f"{plan.summary()}. {issue_count:,} validation issues. "
            f"Plan identity {plan.plan_id or 'unbound'}. Source precedence: "
            f"{' then '.join(plan.source_precedence) or 'not reported'}."
        )

    def _populate(self, *_args) -> None:
        self.tree.clear()
        if self._plan is None:
            return
        selected = self.filter.currentData() or "all"
        for outcome, label, attribute in self._OUTCOMES:
            if selected not in ("all", outcome):
                continue
            values = getattr(self._plan, attribute)
            for value in values:
                if outcome in ("warning", "error"):
                    key, issue = value
                    source = self._source_for(key)
                    state = f"{issue.code}: {issue.message}"
                else:
                    key = value
                    source = self._source_for(key)
                    state = self._source_state(key)
                detail = f"{source} / {state}"
                item = QTreeWidgetItem(self.tree, (label, key, detail))
                item.setToolTip(2, detail)

    def _source_for(self, key: str) -> str:
        source = self._plan.sources.get(key, {}) if self._plan is not None else {}
        winner = source.get("winner")
        kind = source.get("winner_kind")
        if winner and kind:
            return f"{winner} ({kind})"
        return "Not reported"

    def _source_state(self, key: str) -> str:
        source = self._plan.sources.get(key, {}) if self._plan is not None else {}
        return "Conflict resolved" if source.get("conflicted") else "No conflict"


__all__ = [
    "DashboardHero",
    "EmptyState",
    "MetricTile",
    "NoticeBanner",
    "OperationPlanView",
    "ProviderHealthCard",
    "SectionCard",
    "StatusCard",
    "ToggleRow",
    "Tone",
]
