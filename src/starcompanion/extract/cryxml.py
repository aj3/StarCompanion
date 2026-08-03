"""Reader for CryEngine binary XML (`CryXmlB`).

Star Citizen stores its mission definitions in this format rather than text XML.
The layout is a header of section offsets, then four tables -- nodes, attributes,
child indices, and string data -- with every name and value being a byte offset
into the string blob. See docs/format-notes.md.

Read-only, and tolerant: a file that is plain text XML is passed through
untouched rather than rejected.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

MAGIC = b"CryXmlB\x00"

HEADER_FORMAT = "<9I"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

NODE_SIZE = 28
ATTRIBUTE_SIZE = 8
CHILD_INDEX_SIZE = 4

_MAX_NODES = 5_000_000


class CryXmlError(Exception):
    pass


class NotCryXmlError(CryXmlError):
    pass


@dataclass
class Node:
    tag: str
    attributes: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    # --- navigation ----------------------------------------------------------

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attributes.get(name, default)

    def find(self, tag: str) -> Node | None:
        return next(self.iter_tag(tag), None)

    def findall(self, tag: str) -> list[Node]:
        return list(self.iter_tag(tag))

    def iter_tag(self, tag: str) -> Iterator[Node]:
        """Every descendant with this tag, case-insensitively."""
        wanted = tag.casefold()
        for node in self.walk():
            if node.tag.casefold() == wanted:
                yield node

    def walk(self) -> Iterator[Node]:
        """This node and every descendant, depth first."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def __iter__(self) -> Iterator[Node]:
        return iter(self.children)

    def __repr__(self) -> str:
        return f"<{self.tag} attrs={len(self.attributes)} children={len(self.children)}>"


def is_cryxml(data: bytes) -> bool:
    return data[: len(MAGIC)] == MAGIC


def load(path: Path) -> Node:
    return loads(Path(path).read_bytes())


def loads(data: bytes) -> Node:
    if not is_cryxml(data):
        raise NotCryXmlError(
            f"not CryXmlB (starts with {data[:8]!r}); "
            f"if this is text XML, parse it with a normal XML reader"
        )

    if len(data) < len(MAGIC) + HEADER_SIZE:
        raise CryXmlError("file is too small to hold a CryXmlB header")

    (
        _xml_size,
        node_position, node_count,
        attribute_position, attribute_count,
        child_position, child_count,
        string_position, string_size,
    ) = struct.unpack_from(HEADER_FORMAT, data, len(MAGIC))

    if node_count == 0:
        raise CryXmlError("CryXmlB holds no nodes")
    if node_count > _MAX_NODES:
        raise CryXmlError(f"implausible node count ({node_count:,}); file is likely corrupt")

    strings = _section(data, string_position, string_size, "string data")
    nodes_raw = _section(data, node_position, node_count * NODE_SIZE, "node table")
    attributes_raw = _section(
        data, attribute_position, attribute_count * ATTRIBUTE_SIZE, "attribute table"
    )
    children_raw = _section(
        data, child_position, child_count * CHILD_INDEX_SIZE, "child table"
    )

    attributes = list(struct.iter_unpack("<II", attributes_raw))
    child_indices = [v for (v,) in struct.iter_unpack("<I", children_raw)]

    nodes: list[Node] = []
    layout: list[tuple[int, int, int, int]] = []

    for (
        tag_offset, _item_type, node_attribute_count, node_child_count,
        _parent, first_attribute, first_child, _reserved,
    ) in struct.iter_unpack("<IIHHiiii", nodes_raw):
        node = Node(tag=_string(strings, tag_offset))

        for i in range(first_attribute, first_attribute + node_attribute_count):
            if 0 <= i < len(attributes):
                key, value = attributes[i]
                node.attributes[_string(strings, key)] = _string(strings, value)

        nodes.append(node)
        layout.append((first_child, node_child_count, 0, 0))

    # Children are wired after every node exists, since a child can precede its
    # parent in the table.
    for node, (first_child, count, _a, _b) in zip(nodes, layout):
        for i in range(first_child, first_child + count):
            if 0 <= i < len(child_indices):
                target = child_indices[i]
                if 0 <= target < len(nodes):
                    node.children.append(nodes[target])

    return nodes[0]


def _section(data: bytes, position: int, size: int, what: str) -> bytes:
    end = position + size
    if position < 0 or size < 0 or end > len(data):
        raise CryXmlError(
            f"{what} runs past the end of the file "
            f"(needs {end:,} bytes, have {len(data):,})"
        )
    return data[position:end]


def _string(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\x00", offset)
    if end == -1:
        end = len(blob)
    return blob[offset:end].decode("utf-8", errors="replace")
