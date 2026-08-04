import itertools

from starcompanion.source_graph import (
    PRECEDENCE,
    SourceGraph,
    SourceKind,
    SourceLayer,
    report,
)


def layer(kind, value, *, source_id=None, order=0):
    return SourceLayer(source_id or kind.value, kind, {"Key": value}, order=order)


def test_precedence_is_explicit_and_complete():
    assert PRECEDENCE == (
        SourceKind.STOCK,
        SourceKind.LANGUAGE_OVERLAY,
        SourceKind.IMPORT,
        SourceKind.GENERATED,
        SourceKind.USER,
    )


def test_every_pairwise_conflict_uses_declared_precedence_regardless_of_add_order():
    for lower, higher in itertools.combinations(PRECEDENCE, 2):
        for layers in (
            [layer(lower, "lower"), layer(higher, "higher")],
            [layer(higher, "higher"), layer(lower, "lower")],
        ):
            entry = SourceGraph(layers).resolve().entries["Key"]
            assert entry.value == "higher"
            assert entry.winner.kind is higher
            assert entry.conflicted


def test_import_order_is_deterministic_and_later_import_wins():
    result = SourceGraph(
        [
            layer(SourceKind.IMPORT, "second", source_id="z", order=2),
            layer(SourceKind.IMPORT, "first", source_id="a", order=1),
        ]
    ).resolve()
    assert result.values["Key"] == "second"


def test_equal_contributions_are_not_false_conflicts():
    result = SourceGraph(
        [layer(SourceKind.STOCK, "same"), layer(SourceKind.USER, "same")]
    ).resolve()
    assert result.conflicts == {}
    assert result.entries["Key"].winner.kind is SourceKind.USER


def test_report_retains_winner_shadowed_values_and_provenance():
    result = SourceGraph(
        [
            SourceLayer("stock", SourceKind.STOCK, {"Key": "old"}),
            SourceLayer(
                "generated",
                SourceKind.GENERATED,
                {"Key": "new"},
                provenance={"Key": ("record:field",)},
            ),
        ]
    ).resolve()
    data = report(result)
    assert data["conflict_count"] == 1
    assert data["entries"]["Key"]["winner"] == "generated"
    assert data["entries"]["Key"]["contributions"][1]["provenance"] == [
        "record:field"
    ]


def test_duplicate_source_ids_are_rejected():
    graph = SourceGraph([layer(SourceKind.STOCK, "one", source_id="same")])
    try:
        graph.add(layer(SourceKind.USER, "two", source_id="same"))
    except ValueError as exc:
        assert "duplicate source_id" in str(exc)
    else:
        raise AssertionError("duplicate source id was accepted")


def test_layers_snapshot_mutable_caller_data():
    values = {"Key": "stable"}
    source = SourceLayer("stock", SourceKind.STOCK, values)
    values["Key"] = "mutated"
    assert SourceGraph([source]).resolve().values == {"Key": "stable"}
