"""Build DataCore (.dcb) files for tests.

Packs bytes from the layout in docs/format-notes.md, independently of the
reader, so the tests exercise the format rather than the reader's assumptions.
Supports both known record layouts (v6 = 32 bytes, v8 = 36 bytes).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER_SIZE = 120
GUID_SIZE = 16

# Order matters: these arrays follow the record table, each sized by its
# count in the header.
VALUE_ARRAYS = (
    ("boolean", 1), ("int8", 1), ("int16", 2), ("int32", 4), ("int64", 8),
    ("uint8", 1), ("uint16", 2), ("uint32", 4), ("uint64", 8),
    ("single", 4), ("double", 8), ("guid", GUID_SIZE), ("string_id", 4),
    ("locale", 4), ("enum", 4), ("strong", 8), ("weak", 8), ("reference", 20),
)


class StringTable:
    """NUL-terminated strings addressed by byte offset."""

    def __init__(self):
        self._blob = bytearray()
        self._offsets: dict[str, int] = {}

    def add(self, text: str | None) -> int:
        if text is None:
            return -1
        if text not in self._offsets:
            self._offsets[text] = len(self._blob)
            self._blob += text.encode("utf-8") + b"\x00"
        return self._offsets[text]

    @property
    def blob(self) -> bytes:
        return bytes(self._blob)


@dataclass
class Struct:
    name: str
    parent_index: int = -1
    attribute_count: int = 0
    first_attribute_index: int = 0
    struct_size: int = 0


@dataclass
class Property:
    name: str
    struct_index: int = 0
    data_type: int = 0x000A
    conversion_type: int = 0


@dataclass
class Enum:
    name: str
    value_count: int = 0
    first_value_index: int = 0


@dataclass
class RecordSpec:
    name: str
    file_name: str
    struct_index: int
    tag: str | None = None
    guid: bytes = b"\x00" * GUID_SIZE
    instance_index: int = 0
    struct_size: int = 0


@dataclass
class Builder:
    version: int = 8
    structs: list[Struct] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    enums: list[Enum] = field(default_factory=list)
    records: list[RecordSpec] = field(default_factory=list)
    enum_options: list[str] = field(default_factory=list)
    magic: int = 0

    def add_struct(self, name: str, **kwargs) -> Builder:
        self.structs.append(Struct(name, **kwargs))
        return self

    def add_property(self, name: str, **kwargs) -> Builder:
        self.properties.append(Property(name, **kwargs))
        return self

    def add_enum(self, name: str, **kwargs) -> Builder:
        self.enums.append(Enum(name, **kwargs))
        return self

    def add_record(self, name: str, file_name: str, struct_index: int, **kwargs) -> Builder:
        self.records.append(RecordSpec(name, file_name, struct_index, **kwargs))
        return self

    def build(self) -> bytes:
        text1 = StringTable()  # StringId  -- file names
        text2 = StringTable()  # StringId2 -- everything else

        struct_blob = b"".join(
            struct.pack(
                "<iiHHI",
                text2.add(s.name), s.parent_index,
                s.attribute_count, s.first_attribute_index, s.struct_size,
            )
            for s in self.structs
        )

        property_blob = b"".join(
            struct.pack(
                "<iHHHH",
                text2.add(p.name), p.struct_index, p.data_type, p.conversion_type, 0,
            )
            for p in self.properties
        )

        enum_blob = b"".join(
            struct.pack("<iHH", text2.add(e.name), e.value_count, e.first_value_index)
            for e in self.enums
        )

        record_blob = b""
        for r in self.records:
            if self.version >= 8:
                record_blob += struct.pack(
                    "<iii i 16s HH".replace(" ", ""),
                    text2.add(r.name), text1.add(r.file_name), text2.add(r.tag),
                    r.struct_index, r.guid, r.instance_index, r.struct_size,
                )
            else:
                record_blob += struct.pack(
                    "<iii16sHH",
                    text2.add(r.name), text1.add(r.file_name),
                    r.struct_index, r.guid, r.instance_index, r.struct_size,
                )

        option_blob = b"".join(struct.pack("<i", text2.add(o)) for o in self.enum_options)

        blob1, blob2 = text1.blob, text2.blob

        header = struct.pack(
            "<IIII" + "i" * 24 + "II",
            self.magic, self.version, 0, 0,
            len(self.structs), len(self.properties), len(self.enums),
            0,  # data mappings
            len(self.records),
            *([0] * 18),  # value array counts
            len(self.enum_options),
            len(blob1), len(blob2),
        )
        assert len(header) == HEADER_SIZE, len(header)

        return (
            header + struct_blob + property_blob + enum_blob
            + record_blob + option_blob + blob1 + blob2
        )


def minimal(version: int = 8) -> bytes:
    """One struct, one property, one record -- enough to prove a parse."""
    return (
        Builder(version=version)
        .add_struct("Mission", attribute_count=1, first_attribute_index=0, struct_size=8)
        .add_property("title", struct_index=0, data_type=0x000D)
        .add_record(
            "Mission.Test", "libs/foundry/records/mission/test.xml", 0,
            tag="Mission" if version >= 8 else None,
        )
        .build()
    )
