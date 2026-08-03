"""Build CryXmlB files for tests, packed from the spec in docs/format-notes.md.

Independent of the reader, so tests exercise the format rather than the
reader's own assumptions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"CryXmlB\x00"
NODE_SIZE = 28


@dataclass
class Element:
    tag: str
    attributes: dict[str, str] = field(default_factory=dict)
    children: list["Element"] = field(default_factory=list)

    def add(self, child: "Element") -> "Element":
        self.children.append(child)
        return self


class _Strings:
    def __init__(self):
        self._blob = bytearray()
        self._offsets: dict[str, int] = {}

    def add(self, text: str) -> int:
        if text not in self._offsets:
            self._offsets[text] = len(self._blob)
            self._blob += text.encode("utf-8") + b"\x00"
        return self._offsets[text]

    @property
    def blob(self) -> bytes:
        return bytes(self._blob)


def build(root: Element) -> bytes:
    strings = _Strings()

    flat: list[Element] = []
    parents: list[int] = []

    def flatten(node: Element, parent: int) -> None:
        index = len(flat)
        flat.append(node)
        parents.append(parent)
        for child in node.children:
            flatten(child, index)

    flatten(root, -1)

    index_of = {id(node): i for i, node in enumerate(flat)}

    attribute_rows: list[tuple[int, int]] = []
    child_rows: list[int] = []
    node_rows: list[bytes] = []

    for node, parent in zip(flat, parents):
        first_attribute = len(attribute_rows)
        for key, value in node.attributes.items():
            attribute_rows.append((strings.add(key), strings.add(value)))

        first_child = len(child_rows)
        for child in node.children:
            child_rows.append(index_of[id(child)])

        node_rows.append(
            struct.pack(
                "<IIHHiiii",
                strings.add(node.tag), 0,
                len(node.attributes), len(node.children),
                parent, first_attribute, first_child, 0,
            )
        )

    assert all(len(r) == NODE_SIZE for r in node_rows)

    nodes_blob = b"".join(node_rows)
    attributes_blob = b"".join(struct.pack("<II", k, v) for k, v in attribute_rows)
    children_blob = b"".join(struct.pack("<I", i) for i in child_rows)
    string_blob = strings.blob

    header_size = struct.calcsize("<9I")
    start = len(MAGIC) + header_size

    node_position = start
    attribute_position = node_position + len(nodes_blob)
    child_position = attribute_position + len(attributes_blob)
    string_position = child_position + len(children_blob)
    total = string_position + len(string_blob)

    header = struct.pack(
        "<9I",
        total,
        node_position, len(node_rows),
        attribute_position, len(attribute_rows),
        child_position, len(child_rows),
        string_position, len(string_blob),
    )

    return MAGIC + header + nodes_blob + attributes_blob + children_blob + string_blob


def sample() -> bytes:
    """A small tree with attributes and nesting."""
    root = Element("Subsumption")
    activity = Element("Subactivity", {"ID": "abc-123", "Name": "Init"})
    activity.add(Element("Task", {"Type": "Wait", "Duration": "5"}))
    activity.add(Element("Task", {"Type": "Spawn"}))
    root.add(activity)
    root.add(Element("Variables"))
    return build(root)
