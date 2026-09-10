"""G1 virtualized source-graph editor model and performance coverage."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTableView
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)

from starcompanion.gui.string_editor import (
    COLUMN_FILTER_KEYS,
    StringEditorDocument,
    StringFilterProxyModel,
    StringTableModel,
    build_string_snapshot,
)
from starcompanion.model import Contract, ContractSet, Evidence, Org, StringKind
from starcompanion.render import RenderResult


@pytest.fixture(scope="session")
def qapp_editor():
    return QApplication.instance() or QApplication([])


class CountingProxy(StringFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.refilter_count = 0

    def _refilter(self):
        self.refilter_count += 1
        super()._refilter()


def corpus(count: int = 2) -> tuple[ContractSet, RenderResult]:
    org = Org("test", "Test Mission Giver")
    keys = [f"Test_{number:05d}_title" for number in range(count)]
    stock = {key: f"Stock contract {number}" for number, key in enumerate(keys)}
    contract = Contract(
        "test-contract",
        org,
        "Delivery",
        keys={StringKind.TITLE: keys},
        texts=stock,
        base_texts=stock,
    )
    rendered = RenderResult(
        values={key: f"Rendered contract {number}" for number, key in enumerate(keys)},
        provenance={
            keys[0]: (
                Evidence(
                    "local-dataforge-missions",
                    "record-1",
                    "missions/test",
                    "reward.reputation",
                    100,
                ),
            )
        },
    )
    return ContractSet([contract], {org.id: org}), rendered


def test_snapshot_uses_c3_precedence_plan_and_provenance():
    contracts, rendered = corpus()
    snapshot = build_string_snapshot(
        contracts,
        rendered,
        profile_name="default",
        user_values={"Test_00000_title": "My explicit wording"},
    )
    record = snapshot.by_key["Test_00000_title"]

    assert record.stock == "Stock contract 0"
    assert record.rendered == "Rendered contract 0"
    assert record.merged == "My explicit wording"
    assert record.winner.source_id == "user.ini"
    assert record.winner.kind.value == "user"
    assert record.conflicted
    assert record.operation == "change"
    assert record.providers == ("local-dataforge-missions",)
    assert record.key in snapshot.plan.updated


def test_document_supports_bounded_model_level_undo_redo_and_multi_reset():
    document = StringEditorDocument({"A": "saved-a", "B": "saved-b"})

    document.set_value("A", "draft-a")
    document.reset(("A", "B"))
    assert document.values == {}
    assert document.can_undo and document.dirty

    document.undo()
    assert document.values == {"A": "draft-a", "B": "saved-b"}
    document.undo()
    assert document.values == {"A": "saved-a", "B": "saved-b"}
    assert not document.dirty and document.can_redo

    document.redo()
    command = document.save_command()
    assert command is not None
    assert command.label == "save advanced editor changes"
    assert [change.key for change in command.changes] == ["A"]


def test_table_is_virtualized_and_filters_cached_records(qapp_editor):
    contracts, rendered = corpus(200)
    document = StringEditorDocument()
    model = StringTableModel(document)
    model.set_inputs(contracts, rendered, "default")
    proxy = StringFilterProxyModel()
    proxy.setSourceModel(model)
    table = QTableView()
    table.setModel(proxy)

    assert model.rowCount() == 200
    assert table.indexWidget(proxy.index(0, 0)) is None
    assert not (
        model.flags(model.index(0, StringTableModel.MERGED))
        & Qt.ItemFlag.ItemIsEditable
    )

    proxy.set_query("00199")
    assert proxy.rowCount() == 1
    proxy.set_query("")
    proxy.set_provider_filter("local-dataforge-missions")
    assert proxy.rowCount() == 1


def test_column_filters_are_cached_batched_and_combined_with_existing_filters(
    qapp_editor,
):
    contracts, rendered = corpus(20)
    model = StringTableModel(StringEditorDocument())
    model.set_inputs(contracts, rendered, "default")
    proxy = CountingProxy()
    proxy.setSourceModel(model)

    record = model.record(0)
    assert record is not None
    assert record.normalized_columns is record.normalized_columns
    assert len(record.normalized_columns) == len(COLUMN_FILTER_KEYS) == 7

    proxy.set_column_filters(
        {
            "key": "  00019  ",
            "category": "TITLE",
            "stock": "stock contract",
            "rendered": "rendered contract 19",
            "merged": "contract 19",
            "source": "PROFILE:DEFAULT",
            "outcome": "CHANGE",
        }
    )
    assert proxy.refilter_count == 1
    assert proxy.rowCount() == 1
    assert proxy.column_filters["key"] == "00019"

    # Global and typed filters still combine with the column batch.
    proxy.set_query("test mission giver")
    assert proxy.rowCount() == 1
    proxy.set_provider_filter("local-dataforge-missions")
    assert proxy.rowCount() == 0

    # Empty strings clear filters, and an identical normalized batch does not
    # cause another invalidation.
    proxy.set_provider_filter("all")
    before = proxy.refilter_count
    proxy.set_column_filters({"key": ""})
    assert proxy.refilter_count == before + 1
    proxy.set_column_filters({"key": "   "})
    assert proxy.refilter_count == before + 1
    assert proxy.rowCount() == 20


def test_invalid_column_filter_batch_is_rejected_without_changing_state(qapp_editor):
    contracts, rendered = corpus()
    model = StringTableModel(StringEditorDocument())
    model.set_inputs(contracts, rendered, "default")
    proxy = CountingProxy()
    proxy.setSourceModel(model)
    proxy.set_column_filters({"key": "00000"})

    with pytest.raises(ValueError, match="unknown string-editor column"):
        proxy.set_column_filters({"key": "00001", "bogus": "value"})
    assert proxy.column_filters == {"key": "00000"}
    assert proxy.rowCount() == 1

    with pytest.raises(TypeError, match="values must be text"):
        proxy.set_column_filters({"key": 1})
    assert proxy.column_filters == {"key": "00000"}


def test_50k_snapshot_and_exact_search_benchmark(qapp_editor):
    contracts, rendered = corpus(50_000)
    started = time.perf_counter()
    document = StringEditorDocument()
    model = StringTableModel(document)
    model.set_inputs(contracts, rendered, "benchmark")
    build_seconds = time.perf_counter() - started

    proxy = CountingProxy()
    proxy.setSourceModel(model)
    started = time.perf_counter()
    proxy.set_query("test_49999_title")
    visible = proxy.rowCount()
    filter_seconds = time.perf_counter() - started

    proxy.set_query("")
    proxy.refilter_count = 0
    started = time.perf_counter()
    proxy.set_column_filters(
        {
            "key": "test_49999_title",
            "category": "title",
            "stock": "stock contract 49999",
            "rendered": "rendered contract 49999",
            "merged": "rendered contract 49999",
            "source": "profile:benchmark (generated)",
            "outcome": "change",
        }
    )
    column_visible = proxy.rowCount()
    column_filter_seconds = time.perf_counter() - started

    assert model.rowCount() == 50_000
    assert visible == 1
    assert column_visible == 1
    assert proxy.refilter_count == 1
    assert build_seconds < 10.0, f"50k editor build took {build_seconds:.2f}s"
    assert filter_seconds < 5.0, f"50k exact search took {filter_seconds:.2f}s"
    assert column_filter_seconds < 5.0, (
        f"50k seven-column filter took {column_filter_seconds:.2f}s"
    )
