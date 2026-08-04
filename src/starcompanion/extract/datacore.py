"""Reader for the DataCore binary database (`Data/Game2.dcb`).

A reflection-style store: a schema of struct and property definitions describes
how to walk typed value arrays, and records point into them. See
docs/format-notes.md.

Field counts and record sizes change between game patches, so the version is
validated up front and an unrecognised one is refused outright. Emitting
partially-parsed data would be worse than failing: it looks plausible.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterator

HEADER_SIZE = 120
# 4 unsigned (magic, version, 2 reserved) + 24 signed counts + 2 text lengths.
# Must total exactly HEADER_SIZE: a longer format silently reads into the
# struct table and only works while the extra fields go unused.
HEADER_FORMAT = "<IIII" + "i" * 24 + "II"
assert struct.calcsize(HEADER_FORMAT) == HEADER_SIZE

SUPPORTED_VERSIONS = (6, 8)

STRUCT_DEFINITION_SIZE = 16
PROPERTY_DEFINITION_SIZE = 12
ENUM_DEFINITION_SIZE = 8
DATA_MAPPING_SIZE = 8
RECORD_SIZE_V6 = 32
RECORD_SIZE_V8 = 36
POINTER_SIZE = 8
REFERENCE_SIZE = 20
GUID_SIZE = 16

# Sentinel used for absent string offsets and null pointers.
NO_STRING = -1

_NULL_GUID = "00000000-0000-0000-0000-000000000000"
_MAX_INLINE_NESTING = 8
"""Inline structs are bounded by struct_size, but cap nesting anyway."""
_MAX_ARRAY = 100_000


class DataType(IntEnum):
    BOOLEAN = 0x0001
    SBYTE = 0x0002
    INT16 = 0x0003
    INT32 = 0x0004
    INT64 = 0x0005
    BYTE = 0x0006
    UINT16 = 0x0007
    UINT32 = 0x0008
    UINT64 = 0x0009
    STRING = 0x000A
    SINGLE = 0x000B
    DOUBLE = 0x000C
    LOCALE = 0x000D
    GUID = 0x000E
    ENUM_CHOICE = 0x000F
    CLASS = 0x0010
    STRONG_POINTER = 0x0110
    WEAK_POINTER = 0x0210
    REFERENCE = 0x0310


class ConversionType(IntEnum):
    ATTRIBUTE = 0x00
    COMPLEX_ARRAY = 0x01
    SIMPLE_ARRAY = 0x02
    CLASS_ARRAY = 0x03


# Scalar arrays appear in this order after the record table, one array per
# type, each sized by its count in the header.
_VALUE_ARRAYS: tuple[tuple[str, DataType, int, str], ...] = (
    ("boolean", DataType.BOOLEAN, 1, "?"),
    ("int8", DataType.SBYTE, 1, "b"),
    ("int16", DataType.INT16, 2, "h"),
    ("int32", DataType.INT32, 4, "i"),
    ("int64", DataType.INT64, 8, "q"),
    ("uint8", DataType.BYTE, 1, "B"),
    ("uint16", DataType.UINT16, 2, "H"),
    ("uint32", DataType.UINT32, 4, "I"),
    ("uint64", DataType.UINT64, 8, "Q"),
    ("single", DataType.SINGLE, 4, "f"),
    ("double", DataType.DOUBLE, 8, "d"),
    ("guid", DataType.GUID, GUID_SIZE, None),
    ("string_id", DataType.STRING, 4, "i"),
    ("locale", DataType.LOCALE, 4, "i"),
    ("enum", DataType.ENUM_CHOICE, 4, "i"),
    ("strong", DataType.STRONG_POINTER, POINTER_SIZE, None),
    ("weak", DataType.WEAK_POINTER, POINTER_SIZE, None),
    ("reference", DataType.REFERENCE, REFERENCE_SIZE, None),
)


# Inline width of each scalar type within an instance.
_SCALAR_WIDTHS: dict[int, int] = {
    DataType.BOOLEAN: 1, DataType.SBYTE: 1, DataType.BYTE: 1,
    DataType.INT16: 2, DataType.UINT16: 2,
    DataType.INT32: 4, DataType.UINT32: 4, DataType.SINGLE: 4,
    DataType.INT64: 8, DataType.UINT64: 8, DataType.DOUBLE: 8,
    DataType.STRING: 4, DataType.LOCALE: 4, DataType.ENUM_CHOICE: 4,
    DataType.GUID: GUID_SIZE,
    DataType.STRONG_POINTER: POINTER_SIZE, DataType.WEAK_POINTER: POINTER_SIZE,
    DataType.REFERENCE: REFERENCE_SIZE,
}

_SCALAR_FORMATS: dict[int, str] = {
    DataType.SBYTE: "<b", DataType.BYTE: "<B",
    DataType.INT16: "<h", DataType.UINT16: "<H",
    DataType.INT32: "<i", DataType.UINT32: "<I",
    DataType.INT64: "<q", DataType.UINT64: "<Q",
    DataType.SINGLE: "<f", DataType.DOUBLE: "<d",
}

# Which typed array an array-valued property draws from.
_ARRAY_NAMES: dict[int, str] = {
    DataType.BOOLEAN: "boolean", DataType.SBYTE: "int8", DataType.INT16: "int16",
    DataType.INT32: "int32", DataType.INT64: "int64", DataType.BYTE: "uint8",
    DataType.UINT16: "uint16", DataType.UINT32: "uint32", DataType.UINT64: "uint64",
    DataType.SINGLE: "single", DataType.DOUBLE: "double", DataType.GUID: "guid",
    DataType.STRING: "string_id", DataType.LOCALE: "locale",
    DataType.ENUM_CHOICE: "enum", DataType.STRONG_POINTER: "strong",
    DataType.WEAK_POINTER: "weak", DataType.REFERENCE: "reference",
}


class DataCoreError(Exception):
    pass


class UnsupportedVersionError(DataCoreError):
    def __init__(self, found: int):
        super().__init__(
            f"DataCore version {found} is not supported by this build "
            f"(known: {', '.join(map(str, SUPPORTED_VERSIONS))}). "
            f"The game's data format changed; StarCompanion needs updating "
            f"before it can read this patch."
        )
        self.version = found


class CorruptDataCoreError(DataCoreError):
    pass


@dataclass(frozen=True)
class StructDefinition:
    name: str
    parent_index: int
    attribute_count: int
    first_attribute_index: int
    struct_size: int


@dataclass(frozen=True)
class PropertyDefinition:
    name: str
    struct_index: int
    data_type: int
    conversion_type: int

    @property
    def is_array(self) -> bool:
        return self.conversion_type != ConversionType.ATTRIBUTE


@dataclass(frozen=True)
class EnumDefinition:
    name: str
    value_count: int
    first_value_index: int


@dataclass(frozen=True)
class Record:
    name: str
    file_name: str
    tag: str | None
    struct_index: int
    guid: str
    instance_index: int
    struct_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file_name": self.file_name,
            "tag": self.tag,
            "struct_index": self.struct_index,
            "guid": self.guid,
            "instance_index": self.instance_index,
            "struct_size": self.struct_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        return cls(**data)


@dataclass
class DataCore:
    """A parsed DataCore database.

    Definition tables are decoded eagerly (they are small); record *contents*
    are resolved on demand, since the real file holds hundreds of thousands.
    """

    version: int
    structs: list[StructDefinition] = field(default_factory=list)
    properties: list[PropertyDefinition] = field(default_factory=list)
    enums: list[EnumDefinition] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    enum_options: list[str] = field(default_factory=list)

    _values: dict[str, Any] = field(default_factory=dict, repr=False)
    _text1: bytes = field(default=b"", repr=False)
    _text2: bytes = field(default=b"", repr=False)
    _struct_by_name: dict[str, int] = field(default_factory=dict, repr=False)
    _records_by_struct: dict[int, list[int]] = field(default_factory=dict, repr=False)
    _record_by_guid: dict[str, int] = field(default_factory=dict, repr=False)
    _instance_data: bytes = field(default=b"", repr=False)
    _instance_offsets: dict[int, int] = field(default_factory=dict, repr=False)

    # --- lookups -------------------------------------------------------------

    def struct_index(self, name: str) -> int | None:
        return self._struct_by_name.get(name)

    def struct_names(self) -> list[str]:
        return sorted(self._struct_by_name)

    def records_of(self, struct_name: str) -> list[Record]:
        index = self.struct_index(struct_name)
        if index is None:
            return []
        return [self.records[i] for i in self._records_by_struct.get(index, ())]

    def record_by_guid(self, guid: str) -> Record | None:
        position = self._record_by_guid.get(guid)
        return self.records[position] if position is not None else None

    def find_structs(self, substring: str) -> list[str]:
        lowered = substring.casefold()
        return [name for name in self.struct_names() if lowered in name.casefold()]

    def properties_of(self, struct_index: int) -> list[PropertyDefinition]:
        """Own properties plus inherited ones, base class first."""
        chain: list[int] = []
        seen: set[int] = set()
        current = struct_index
        while 0 <= current < len(self.structs) and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self.structs[current].parent_index

        result: list[PropertyDefinition] = []
        for index in reversed(chain):
            definition = self.structs[index]
            start = definition.first_attribute_index
            result.extend(self.properties[start : start + definition.attribute_count])
        return result

    # --- strings -------------------------------------------------------------

    def text1(self, offset: int) -> str | None:
        """String table 1, indexed by `StringId`."""
        return _read_cstring(self._text1, offset)

    def text2(self, offset: int) -> str | None:
        """String table 2, indexed by `StringId2`. A different table -- mixing
        them up yields plausible but wrong strings."""
        return _read_cstring(self._text2, offset)

    # --- instance data -------------------------------------------------------

    def instance_bytes(self, struct_index: int, instance_index: int) -> bytes | None:
        """The raw bytes of one struct instance.

        Instances live in a flat region after the string tables, blocked by
        struct in data-mapping order.
        """
        base = self._instance_offsets.get(struct_index)
        if base is None or not 0 <= struct_index < len(self.structs):
            return None

        size = self.structs[struct_index].struct_size
        start = base + instance_index * size
        if start < 0 or start + size > len(self._instance_data):
            return None
        return self._instance_data[start : start + size]

    def read_instance(
        self, struct_index: int, instance_index: int, *, max_depth: int = 0, _depth: int = 0
    ) -> dict[str, Any]:
        """Decode one instance into a plain dict, keyed by property name.

        `max_depth` counts pointer hops. It defaults to 0 -- scalars and inline
        nested structs only -- because following pointers fans out across the
        whole database and is far too slow for a bulk scan. Raise it when you
        need a specific record's linked data.
        """
        raw = self.instance_bytes(struct_index, instance_index)
        if raw is None:
            return {}
        return self._read_struct(raw, struct_index, max_depth, _depth)[0]

    def read_record(self, record: Record, *, max_depth: int = 0) -> dict[str, Any]:
        return self.read_instance(
            record.struct_index, record.instance_index, max_depth=max_depth
        )

    def struct_width(self, struct_index: int) -> int | None:
        """Total inline width of a struct's properties.

        Should equal its declared `struct_size`; a mismatch means our field
        widths are wrong and every value after it would be misaligned.
        """
        total = 0
        for prop in self.properties_of(struct_index):
            width = self._property_width(prop)
            if width is None:
                return None
            total += width
        return total

    def _property_width(self, prop: PropertyDefinition) -> int | None:
        if prop.is_array:
            return 8  # count + first index
        if prop.data_type == DataType.CLASS:
            if not 0 <= prop.struct_index < len(self.structs):
                return None
            return self.structs[prop.struct_index].struct_size
        return _SCALAR_WIDTHS.get(prop.data_type)

    def _read_struct(
        self, raw: bytes, struct_index: int, max_depth: int, depth: int
    ) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        offset = 0

        for prop in self.properties_of(struct_index):
            width = self._property_width(prop)
            if width is None or offset + width > len(raw):
                break

            chunk = raw[offset : offset + width]
            result[prop.name] = self._read_property(prop, chunk, max_depth, depth)
            offset += width

        return result, offset

    def _read_property(
        self, prop: PropertyDefinition, chunk: bytes, max_depth: int, depth: int
    ) -> Any:
        if prop.is_array:
            count, first = struct.unpack("<II", chunk)
            return self._read_array(prop, count, first, max_depth, depth)

        if prop.data_type == DataType.CLASS:
            # Inline, within this instance's own bytes -- cheap, so it is
            # bounded by nesting depth rather than the pointer budget.
            if depth >= _MAX_INLINE_NESTING:
                return None
            return self._read_struct(chunk, prop.struct_index, max_depth, depth + 1)[0]

        return self._read_scalar(prop.data_type, chunk, max_depth, depth)

    def _read_scalar(self, data_type: int, chunk: bytes, max_depth: int, depth: int) -> Any:
        if data_type == DataType.BOOLEAN:
            return chunk[0] != 0
        if data_type in _SCALAR_FORMATS:
            return struct.unpack(_SCALAR_FORMATS[data_type], chunk)[0]
        if data_type in (DataType.STRING, DataType.LOCALE):
            # Localization keys and plain strings both live in table 1.
            return self.text1(struct.unpack("<i", chunk)[0])
        if data_type == DataType.ENUM_CHOICE:
            # Enum values are offsets into the text table (the same table as
            # LOCALE), not the schema/blob table used by definition names.
            return self.text1(struct.unpack("<i", chunk)[0])
        if data_type == DataType.GUID:
            return _format_guid(chunk)
        if data_type in (DataType.STRONG_POINTER, DataType.WEAK_POINTER):
            # DataForge Pointer is UInt32 struct + UInt16 variant + UInt16
            # padding. Reading the last four bytes as a signed instance index
            # silently folds non-zero padding into the variant (e.g. 28,409
            # becomes 749,305), resolving unrelated instance data.
            target_struct, target_instance, _padding = struct.unpack("<IHH", chunk)
            if target_struct == 0xFFFFFFFF and target_instance == 0xFFFF:
                return None
            if depth >= max_depth:
                # Unfollowed: report where it points rather than pretending null.
                return {"$struct": target_struct, "$instance": target_instance}
            return self.read_instance(
                target_struct, target_instance, max_depth=max_depth, _depth=depth + 1
            )
        if data_type == DataType.REFERENCE:
            _instance, guid = struct.unpack("<i16s", chunk)
            formatted = _format_guid(guid)
            return None if formatted == _NULL_GUID else formatted
        return None

    def _read_array(
        self, prop: PropertyDefinition, count: int, first: int, max_depth: int, depth: int
    ) -> list[Any]:
        if count == 0 or count > _MAX_ARRAY:
            return []

        # Conversion type controls XML/container representation; the data type
        # still determines which backing array owns the elements. In
        # particular, a COMPLEX_ARRAY of REFERENCE values lives in the global
        # reference array. Treating every complex array as inline CLASS data
        # resolves its first index against an unrelated struct instance block.
        if prop.data_type == DataType.CLASS:
            if depth >= max_depth:
                return [{"$struct": prop.struct_index, "$instance": first + i} for i in range(count)]
            return [
                self.read_instance(
                    prop.struct_index, first + i, max_depth=max_depth, _depth=depth + 1
                )
                for i in range(count)
            ]

        name = _ARRAY_NAMES.get(prop.data_type)
        if name is None:
            return []

        values = self._values.get(name, [])
        slice_ = values[first : first + count]

        if prop.data_type in (DataType.STRING, DataType.LOCALE):
            return [self.text1(v) for v in slice_]
        if prop.data_type == DataType.ENUM_CHOICE:
            return [self.text1(v) for v in slice_]
        if prop.data_type == DataType.GUID:
            return [_format_guid(v) for v in slice_]
        if prop.data_type in (DataType.STRONG_POINTER, DataType.WEAK_POINTER):
            resolved = []
            for raw in slice_:
                target_struct, target_instance, _padding = struct.unpack("<IHH", raw)
                if target_struct == 0xFFFFFFFF and target_instance == 0xFFFF:
                    resolved.append(None)
                elif depth >= max_depth:
                    resolved.append({"$struct": target_struct, "$instance": target_instance})
                else:
                    resolved.append(
                        self.read_instance(
                            target_struct, target_instance, max_depth=max_depth, _depth=depth + 1
                        )
                    )
            return resolved
        if prop.data_type == DataType.REFERENCE:
            out = []
            for raw in slice_:
                _instance, guid = struct.unpack("<i16s", raw)
                formatted = _format_guid(guid)
                out.append(None if formatted == _NULL_GUID else formatted)
            return out
        return list(slice_)

    # --- iteration -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)

    def summary(self) -> dict[str, int]:
        return {
            "version": self.version,
            "structs": len(self.structs),
            "properties": len(self.properties),
            "enums": len(self.enums),
            "records": len(self.records),
        }


def load(path: Path) -> DataCore:
    return loads(Path(path).read_bytes())


def loads(data: bytes) -> DataCore:
    if len(data) < HEADER_SIZE:
        raise CorruptDataCoreError(
            f"file is {len(data)} bytes; a DataCore header alone is {HEADER_SIZE}"
        )

    fields = struct.unpack_from(HEADER_FORMAT, data, 0)
    _magic, version = fields[0], fields[1]

    # Checked before anything else: a changed format must fail loudly rather
    # than produce records that look right and are not.
    if version not in SUPPORTED_VERSIONS:
        raise UnsupportedVersionError(version)

    (
        struct_count, property_count, enum_count, mapping_count, record_count,
    ) = fields[4:9]
    value_counts = fields[9:27]
    enum_option_count = fields[27]
    text_length, text_length2 = fields[28], fields[29]

    offset = HEADER_SIZE
    core = DataCore(version=version)

    raw_structs, offset = _slice(data, offset, struct_count, STRUCT_DEFINITION_SIZE, "structs")
    raw_properties, offset = _slice(data, offset, property_count, PROPERTY_DEFINITION_SIZE, "properties")
    raw_enums, offset = _slice(data, offset, enum_count, ENUM_DEFINITION_SIZE, "enums")
    raw_mappings, offset = _slice(data, offset, mapping_count, DATA_MAPPING_SIZE, "data mappings")

    record_size = RECORD_SIZE_V8 if version >= 8 else RECORD_SIZE_V6
    raw_records, offset = _slice(data, offset, record_count, record_size, "records")

    raw_values: dict[str, bytes] = {}
    for (name, _type, item_size, _fmt), count in zip(_VALUE_ARRAYS, value_counts):
        raw_values[name], offset = _slice(data, offset, count, item_size, f"{name} values")

    raw_enum_options, offset = _slice(data, offset, enum_option_count, 4, "enum options")

    text1_end = offset + text_length
    text2_end = text1_end + text_length2
    if text2_end > len(data):
        raise CorruptDataCoreError(
            f"string tables run past the end of the file "
            f"(need {text2_end} bytes, have {len(data)})"
        )
    core._text1 = data[offset:text1_end]
    core._text2 = data[text1_end:text2_end]

    # Everything after the string tables is instance data, blocked by struct in
    # data-mapping order.
    core._instance_data = data[text2_end:]
    running = 0
    for count, struct_index in struct.iter_unpack("<Ii", raw_mappings):
        if 0 <= struct_index < struct_count:
            core._instance_offsets[struct_index] = running
            running += count * struct.unpack_from(
                "<I", raw_structs, struct_index * STRUCT_DEFINITION_SIZE + 12
            )[0]

    # Definitions decoded now; they are small and everything else refers to them.
    core.structs = [
        StructDefinition(
            name=core.text2(name_offset) or "",
            parent_index=parent,
            attribute_count=attribute_count,
            first_attribute_index=first_attribute,
            struct_size=size,
        )
        for name_offset, parent, attribute_count, first_attribute, size in struct.iter_unpack(
            "<iiHHI", raw_structs
        )
    ]

    core.properties = [
        PropertyDefinition(
            name=core.text2(name_offset) or "",
            struct_index=struct_index,
            data_type=data_type,
            conversion_type=conversion_type,
        )
        for name_offset, struct_index, data_type, conversion_type, _pad in struct.iter_unpack(
            "<iHHHH", raw_properties
        )
    ]

    core.enums = [
        EnumDefinition(
            name=core.text2(name_offset) or "",
            value_count=value_count,
            first_value_index=first_value,
        )
        for name_offset, value_count, first_value in struct.iter_unpack(
            "<iHH", raw_enums
        )
    ]

    core.enum_options = [
        core.text1(offset) or ""
        for (offset,) in struct.iter_unpack("<i", raw_enum_options)
    ]

    core.records = _decode_records(core, raw_records, version)
    core._values = _decode_values(raw_values)

    core._struct_by_name = {
        definition.name: index
        for index, definition in enumerate(core.structs)
        if definition.name
    }

    by_struct: dict[int, list[int]] = {}
    by_guid: dict[str, int] = {}
    for index, record in enumerate(core.records):
        by_struct.setdefault(record.struct_index, []).append(index)
        by_guid.setdefault(record.guid, index)
    core._records_by_struct = by_struct
    core._record_by_guid = by_guid

    return core


def _decode_records(core: DataCore, raw: bytes, version: int) -> list[Record]:
    records: list[Record] = []

    if version >= 8:
        # v8 inserted tag_offset between file_name_offset and struct_index.
        layout = "<iii i 16s HH".replace(" ", "")
        for name_off, file_off, tag_off, struct_index, guid, instance, size in struct.iter_unpack(
            layout, raw
        ):
            records.append(
                Record(
                    name=core.text2(name_off) or "",
                    file_name=core.text1(file_off) or "",
                    tag=core.text2(tag_off),
                    struct_index=struct_index,
                    guid=_format_guid(guid),
                    instance_index=instance,
                    struct_size=size,
                )
            )
        return records

    for name_off, file_off, struct_index, guid, instance, size in struct.iter_unpack(
        "<iii16sHH", raw
    ):
        records.append(
            Record(
                name=core.text2(name_off) or "",
                file_name=core.text1(file_off) or "",
                tag=None,
                struct_index=struct_index,
                guid=_format_guid(guid),
                instance_index=instance,
                struct_size=size,
            )
        )
    return records


def _decode_values(raw: dict[str, bytes]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, _type, item_size, fmt in _VALUE_ARRAYS:
        blob = raw.get(name, b"")
        if fmt is None:
            values[name] = [
                blob[i : i + item_size] for i in range(0, len(blob), item_size)
            ]
        else:
            values[name] = list(struct.unpack(f"<{len(blob) // item_size}{fmt}", blob))
    return values


def _slice(
    data: bytes, offset: int, count: int, item_size: int, what: str
) -> tuple[bytes, int]:
    if count < 0:
        raise CorruptDataCoreError(f"negative {what} count ({count})")
    end = offset + count * item_size
    if end > len(data):
        raise CorruptDataCoreError(
            f"{what} run past the end of the file "
            f"(need {end} bytes, have {len(data)})"
        )
    return data[offset:end], end


def _read_cstring(table: bytes, offset: int) -> str | None:
    if offset < 0 or offset >= len(table):
        return None
    end = table.find(b"\x00", offset)
    if end == -1:
        end = len(table)
    return table[offset:end].decode("utf-8", errors="replace")


def _format_guid(raw: bytes) -> str:
    if len(raw) != GUID_SIZE:
        return ""
    a, b, c, d = struct.unpack("<IHH8s", raw)
    return f"{a:08x}-{b:04x}-{c:04x}-{d[:2].hex()}-{d[2:].hex()}"
