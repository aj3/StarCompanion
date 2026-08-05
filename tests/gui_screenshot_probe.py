"""Render one deterministic shell screenshot in an isolated Qt process."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from starcompanion import install
from starcompanion.gui.app import MainWindow
from starcompanion.gui.theme import DARK
from starcompanion.ini import BOM
from starcompanion.model import BlueprintPool, Contract, ContractSet, Org, Reward
from starcompanion.sources import contracts_ini


def _rect(widget: QWidget, window: QWidget) -> list[int]:
    point = widget.mapTo(window, QPoint(0, 0))
    return [point.x(), point.y(), widget.width(), widget.height()]


def main() -> int:
    output = Path(sys.argv[1])
    page = sys.argv[2] if len(sys.argv) > 2 else "overview"
    page_key = "presentation" if page == "presentation-wording" else page
    logical_size = (1040, 680) if len(sys.argv) > 3 and sys.argv[3] == "minimum" else (1280, 800)
    if page == "manual-apply":
        os.environ["STARCOMPANION_EXPERT"] = "1"
    install.find_default = lambda: None
    app = QApplication([])
    window = MainWindow()
    if page in ("templates", "string-editor", "manual-apply"):
        sample = output.parent / "c6-screenshot-contracts.ini"
        sample.write_bytes(
            (
                BOM
                + "Org_x_title=Deliver medical supplies <EM4>[100 Rep]</EM4>\n"
                + "Org_x_desc=Transport the shipment safely.\n"
            ).encode("utf-8")
        )
        window.state.set_contracts(contracts_ini.load(sample))
    if page == "blueprints":
        org = Org("local", "Local Mission Giver")
        contract = Contract(
            "Local_Blueprint_Mission",
            org,
            "Delivery",
            reward=Reward(
                blueprint_pools=[
                    BlueprintPool(
                        items=["Coda Pistol", "Norfield"],
                        item_ids={
                            "Coda Pistol": "11111111-1111-1111-1111-111111111111",
                            "Norfield": "22222222-2222-2222-2222-222222222222",
                        },
                    )
                ]
            ),
        )
        window.state.set_contracts(ContractSet([contract], {org.id: org}))
    if page == "string-editor":
        window.editor.document.load({"Org_x_title": "My reviewed title wording"})
        window.editor.rebuild()
    if page == "manual-apply":
        target = output.parent / "global.ini"
        target.write_bytes(
            (BOM + "Org_x_title=Original\nOrg_x_desc=Original body.\nOther=untouched\n").encode(
                "utf-8"
            )
        )
        window.state.backup_dir = output.parent / "backups"
        window.apply.target_edit.setText(str(target))
        window.apply.refresh_plan()
    if not window.shell.set_current_key(page_key):
        raise ValueError(f"unknown screenshot page {page!r}")
    # Native menu metrics vary by platform. The shell contains equivalent
    # profile/theme controls, so the image baseline deliberately targets the
    # cross-platform application surface below the native menu.
    window.menuBar().hide()
    window.resize(*logical_size)
    window.show()
    app.processEvents()

    if page == "presentation-wording":
        parent = window.formatting.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            raise RuntimeError("presentation scroll viewport was not found")
        parent.ensureWidgetVisible(window.formatting.wording_section, 0, 0)
        app.processEvents()

    pixmap = window.grab()
    if not pixmap.save(str(output)):
        raise RuntimeError(f"could not save screenshot to {output}")
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)

    counts = {colour: 0 for colour in (DARK.canvas, DARK.surface, DARK.accent)}
    stride = max(1, round(8 * pixmap.devicePixelRatio()))
    for y in range(0, image.height(), stride):
        for x in range(0, image.width(), stride):
            colour = image.pixelColor(x, y).name()
            if colour in counts:
                counts[colour] += 1

    rect_widgets = {
        "shell": window.shell,
        "sidebar": window.shell.sidebar,
        "header": window.shell.header,
        "stack": window.shell.stack,
        "status": window.shell.status,
    }
    if page == "overview":
        rect_widgets.update(
            {
                "hero": window.start.hero,
                "game_card": window.start.game_card,
                "contract_card": window.start.contract_card,
            }
        )
    elif page == "content":
        rect_widgets.update(
            {
                "enabled_metric": window.fields.enabled_metric,
                "coverage_metric": window.fields.coverage_metric,
                "core_section": window.fields.core_section,
            }
        )
    elif page == "presentation":
        rect_widgets.update(
            {
                "style_metric": window.formatting.style_metric,
                "style_section": window.formatting.style_section,
                "title_section": window.formatting.title_section,
            }
        )
    elif page == "presentation-wording":
        rect_widgets.update(
            {
                "wording_section": window.formatting.wording_section,
                "length_section": window.formatting.length_box,
            }
        )
    elif page == "provenance":
        rect_widgets.update(
            {
                "dataset_section": window.source.dataset_section,
                "providers_section": window.source.providers_section,
            }
        )
    elif page == "templates":
        rect_widgets.update(
            {
                "override_metric": window.templates.override_metric,
                "context_section": window.templates.context_section,
                "editor_section": window.templates.editor_section,
                "preview_section": window.templates.preview_section,
            }
        )
    elif page == "string-editor":
        rect_widgets.update(
            {
                "filter_section": window.editor.filter_section,
                "table_section": window.editor.table_section,
                "detail_section": window.editor.detail_section,
                "table": window.editor.table,
            }
        )
    elif page == "blueprints":
        rect_widgets.update(
            {
                "filter_section": window.blueprints.filter_section,
                "results_section": window.blueprints.results_section,
                "table": window.blueprints.table,
            }
        )
    elif page == "manual-apply":
        rect_widgets.update(
            {
                "target_section": window.apply.target_section,
                "plan_view": window.apply.plan_view,
                "recovery_section": window.apply.recovery_section,
            }
        )
    elif page == "support":
        rect_widgets.update(
            {
                "tabs": window.support.pages,
                "profile_summary": window.support.profile_summary,
                "profile_builtin": window.support.profile_builtin,
            }
        )

    metrics = {
        "page": page,
        "logical_size": [window.width(), window.height()],
        "physical_size": [pixmap.width(), pixmap.height()],
        "device_pixel_ratio": pixmap.devicePixelRatio(),
        "colours": counts,
        "rects": {name: _rect(widget, window) for name, widget in rect_widgets.items()},
    }
    print(json.dumps(metrics, sort_keys=True))
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
