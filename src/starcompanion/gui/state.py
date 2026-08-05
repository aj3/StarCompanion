"""Shared application state.

Holds no logic of its own -- it owns the objects the Sprint 1 modules operate on
and tells the tabs when something changed. Anything a tab needs computing is
computed by importing the real module, never reimplemented here.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..config import Profile, load_builtin
from ..model import ContractSet
from ..render import RenderResult
from ..source_graph import SourceGraph, SourceKind, SourceLayer, report as source_report


class AppState(QObject):
    contractsChanged = Signal()
    profileChanged = Signal()
    pathsChanged = Signal()
    userOverridesChanged = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.contracts: ContractSet | None = None
        self.profile: Profile = load_builtin("default")
        self.target: Path | None = None
        self.stock: Path | None = None
        self.backup_dir: Path | None = None
        self._rendered: RenderResult | None = None
        self.user_override_scope: tuple[str, str] | None = None
        self.user_overrides: dict[str, str] = {}
        self.user_overrides_ready = True

    # --- contracts -----------------------------------------------------------

    def set_contracts(self, contracts: ContractSet) -> None:
        self.contracts = contracts
        self._rendered = None
        self.contractsChanged.emit()

    @property
    def key_count(self) -> int:
        if not self.contracts:
            return 0
        return sum(len(c.all_keys()) for c in self.contracts.contracts)

    def org_ids(self) -> list[str]:
        if not self.contracts:
            return []
        return sorted(self.contracts.orgs, key=lambda i: self.contracts.orgs[i].name)

    def sample_contract(self, org_id: str | None = None):
        """A contract to preview against -- one with rewards, if any has them."""
        if not self.contracts:
            return None
        pool = (
            self.contracts.by_org(org_id) if org_id else self.contracts.contracts
        )
        return next(
            (c for c in pool if not c.reward.is_empty and len(c.keys) > 1),
            next(iter(pool), None),
        )

    # --- profile -------------------------------------------------------------

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self._rendered = None
        self.profileChanged.emit()

    def touch_profile(self) -> None:
        """Announce an in-place edit to the profile."""
        self._rendered = None
        self.profileChanged.emit()

    # --- paths ---------------------------------------------------------------

    def set_target(self, path: Path | None) -> None:
        self.target = path
        self.pathsChanged.emit()

    def set_stock(self, path: Path | None) -> None:
        self.stock = path
        self.pathsChanged.emit()

    # --- derived -------------------------------------------------------------

    def render(self) -> RenderResult:
        """Render the whole corpus, cached until profile or contracts change."""
        if self.contracts is None:
            raise RuntimeError("no contracts loaded")
        if self._rendered is None:
            self._rendered = self.profile.build_renderer().render_all(self.contracts)
        return self._rendered

    def begin_user_override_scope(self, scope: tuple[str, str] | None) -> None:
        """Clear the previous channel before a background user.ini load."""
        self.user_override_scope = scope
        self.user_overrides = {}
        self.user_overrides_ready = scope is None
        self.userOverridesChanged.emit()

    def set_user_overrides(
        self,
        scope: tuple[str, str],
        values: dict[str, str],
    ) -> bool:
        """Publish a completed background load only if its scope is current."""
        if scope != self.user_override_scope:
            return False
        self.user_overrides = dict(values)
        self.user_overrides_ready = True
        self.userOverridesChanged.emit()
        return True

    def source_merge(self):
        rendered = self.render()
        generated_provenance = {
            key: tuple(
                f"{item.provider}:{item.record_id}:{item.field_path}"
                for item in rendered.provenance.get(key, ())
            )
            for key in rendered.values
        }
        layers = [
            SourceLayer(
                f"profile:{self.profile.name}",
                SourceKind.GENERATED,
                rendered.values,
                provenance=generated_provenance,
            )
        ]
        if self.user_overrides:
            layers.append(SourceLayer("user.ini", SourceKind.USER, self.user_overrides))
        return SourceGraph(layers).resolve()

    def effective_values(self) -> dict[str, str]:
        """The C3 generated→user winner map used by preview and apply."""
        return self.source_merge().values

    def source_report(self) -> dict[str, object]:
        return source_report(self.source_merge())

    def backups(self) -> list[Path]:
        directory = self.backup_dir or (self.target.parent / "backups" if self.target else None)
        if not directory or not directory.is_dir():
            return []
        return sorted(directory.glob("*.ini"), reverse=True)
