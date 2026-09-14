from __future__ import annotations

from starcompanion.extract.dataforge import DataForgeIndex
from test_dataforge import GUID, SyntheticDataCore


def test_selected_field_walk_preserves_synthetic_evidence_paths():
    source = SyntheticDataCore(
        [
            (
                "Entity.Test",
                "records/entities/test.xml",
                GUID["entity"],
                {
                    "ignored": {"deep": 1},
                    "display": {"displayName": "@Item_Test"},
                    "stats": [{"mass": 42}],
                },
            )
        ]
    )
    index = DataForgeIndex(source)
    fields = tuple(index.iter_selected_fields(index.nodes[0], ("displayName", "mass")))

    assert [(item.path, item.value) for item in fields] == [
        ("$.display.displayName", "@Item_Test"),
        ("$.stats[0].mass", 42),
    ]


def test_inline_field_walk_does_not_follow_pointer_placeholders():
    source = SyntheticDataCore(
        [
            (
                "Entity.Test",
                "records/entities/test.xml",
                GUID["entity"],
                {
                    "stats": {"mass": 42},
                    "pointer": {"$struct": 0, "$instance": 1, "mass": 99},
                },
            )
        ]
    )
    index = DataForgeIndex(source)

    fields = tuple(index.iter_inline_fields(index.nodes[0], ("mass",)))

    assert [(item.path, item.value) for item in fields] == [("$.stats.mass", 42)]
