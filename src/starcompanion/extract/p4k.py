"""Read-only reader for Star Citizen's Data.p4k archive.

A ZIP variant with three deviations that defeat stock ZIP tooling: CIG's own
local-header signature, ZStandard as compression method 100, and AES entries
flagged inside the extra field rather than by the standard general-purpose bit.
See docs/format-notes.md.

The archive is multi-gigabyte and belongs to the user's game install, so this
opens read-only, reads the central directory plus the entries asked for, and
never loads the whole file.
"""

from __future__ import annotations

import fnmatch
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

EOCD_SIGNATURE = 0x06054B50
ZIP64_LOCATOR_SIGNATURE = 0x07064B50
EOCD64_SIGNATURE = 0x06064B50
CENTRAL_DIR_SIGNATURE = 0x02014B50
LOCAL_FILE_SIGNATURE = 0x04034B50
LOCAL_FILE_CIG_SIGNATURE = 0x14034B50

METHOD_STORE = 0
METHOD_DEFLATE = 8
METHOD_ZSTD = 100

EOCD_SIZE = 22
EOCD64_LOCATOR_SIZE = 20
CENTRAL_DIR_SIZE = 46
LOCAL_HEADER_SIZE = 30

MAX_COMMENT = 0xFFFF
UINT32_MAX = 0xFFFFFFFF
UINT64_MAX = 0xFFFFFFFFFFFFFFFF

# Offset within an entry's extra field whose non-zero value marks encryption.
# CIG does not set the standard general-purpose encryption bit.
ENCRYPTION_FLAG_OFFSET = 168

# Published by the community tools; a fixed obfuscation constant, not a secret.
DEFAULT_KEY = bytes.fromhex("5E7A2002302EEB1A3BB617C30FDE1E47")

LOCALIZATION_GLOB = "Data/Localization/*/global.ini"
DATACORE_PATH = "Data/Game.dcb"

_CHUNK = 1 << 20


class P4KError(Exception):
    """Base for archive problems. Carries the archive path for context."""


class NotAnArchiveError(P4KError):
    pass


class CorruptArchiveError(P4KError):
    pass


class UnsupportedCompressionError(P4KError):
    pass


class EncryptionUnavailableError(P4KError):
    pass


@dataclass(frozen=True)
class P4KEntry:
    filename: str
    compress_type: int
    compress_size: int
    file_size: int
    header_offset: int
    is_encrypted: bool
    crc: int = 0

    @property
    def name(self) -> str:
        return self.filename

    @property
    def compression(self) -> str:
        return {
            METHOD_STORE: "store",
            METHOD_DEFLATE: "deflate",
            METHOD_ZSTD: "zstd",
        }.get(self.compress_type, f"method-{self.compress_type}")


class P4KArchive:
    """Read-only view over a .p4k file.

    Usable as a context manager. The underlying handle is opened ``rb`` and the
    archive is never modified.
    """

    def __init__(self, path: Path, *, key: bytes = DEFAULT_KEY):
        self.path = Path(path)
        self.key = key
        try:
            self._fp: BinaryIO = self.path.open("rb")
        except OSError as exc:
            raise P4KError(f"cannot open {self.path}: {exc}") from exc

        try:
            self._entries = {e.filename: e for e in self._read_central_directory()}
        except Exception:
            self._fp.close()
            raise

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()

    def __enter__(self) -> P4KArchive:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    # --- listing -------------------------------------------------------------

    @property
    def entries(self) -> list[P4KEntry]:
        return list(self._entries.values())

    def namelist(self) -> list[str]:
        return list(self._entries)

    def getinfo(self, name: str) -> P4KEntry:
        try:
            return self._entries[name]
        except KeyError:
            raise KeyError(f"{name!r} is not in {self.path.name}") from None

    def search(self, pattern: str) -> list[P4KEntry]:
        """Glob over entry names, case-insensitively."""
        lowered = pattern.casefold()
        return [
            entry
            for entry in self._entries.values()
            if fnmatch.fnmatch(entry.filename.casefold(), lowered)
        ]

    def localization_files(self) -> list[P4KEntry]:
        return sorted(self.search(LOCALIZATION_GLOB), key=lambda e: e.filename)

    def languages(self) -> list[str]:
        return [entry.filename.split("/")[2] for entry in self.localization_files()]

    # --- reading -------------------------------------------------------------

    def read(self, name: str | P4KEntry) -> bytes:
        entry = name if isinstance(name, P4KEntry) else self.getinfo(name)
        raw = self._read_raw(entry)

        if entry.is_encrypted:
            raw = self._decrypt(raw)

        data = self._decompress(entry, raw)

        if len(data) != entry.file_size and entry.file_size:
            raise CorruptArchiveError(
                f"{entry.filename}: expected {entry.file_size} bytes, got {len(data)}"
            )
        return data

    def extract(self, name: str | P4KEntry, destination: Path) -> Path:
        """Write one entry out. Only ever writes under `destination`."""
        entry = name if isinstance(name, P4KEntry) else self.getinfo(name)
        destination = Path(destination)

        target = (
            destination / Path(entry.filename).name
            if destination.is_dir()
            else destination
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.read(entry))
        return target

    def read_localization(self, language: str = "english") -> bytes:
        """The stock global.ini -- the pristine baseline for overwrite mode."""
        path = f"Data/Localization/{language}/global.ini"
        if path in self._entries:
            return self.read(path)

        available = self.languages()
        raise KeyError(
            f"no localization for {language!r} in {self.path.name}"
            + (f"; available: {', '.join(available)}" if available else "")
        )

    # --- central directory ---------------------------------------------------

    def _read_central_directory(self) -> Iterator[P4KEntry]:
        offset, count = self._locate_central_directory()
        self._fp.seek(offset)

        for index in range(count):
            header = self._fp.read(CENTRAL_DIR_SIZE)
            if len(header) < CENTRAL_DIR_SIZE:
                raise CorruptArchiveError(
                    f"{self.path.name}: central directory ends after {index} of {count} entries"
                )

            (
                signature, _version, _needed, _flags, method, _time, _date, crc,
                compress_size, file_size, name_len, extra_len, comment_len,
                _disk, _internal, _external, header_offset,
            ) = struct.unpack("<IHHHHHHIIIHHHHHII", header)

            if signature != CENTRAL_DIR_SIGNATURE:
                raise CorruptArchiveError(
                    f"{self.path.name}: bad central directory signature "
                    f"0x{signature:08X} at entry {index}"
                )

            filename = self._fp.read(name_len).decode("utf-8", errors="replace")
            extra = self._fp.read(extra_len)
            self._fp.seek(comment_len, 1)

            compress_size, file_size, header_offset = _apply_zip64(
                extra, compress_size, file_size, header_offset
            )

            yield P4KEntry(
                filename=filename.replace("\\", "/"),
                compress_type=method,
                compress_size=compress_size,
                file_size=file_size,
                header_offset=header_offset,
                is_encrypted=_is_encrypted(extra),
                crc=crc,
            )

    def _locate_central_directory(self) -> tuple[int, int]:
        eocd_offset, eocd = self._find_eocd()
        (
            _sig, _disk, _start_disk, _entries_disk, total_entries,
            _size, cd_offset, _comment_len,
        ) = struct.unpack("<IHHHHIIH", eocd)

        needs_zip64 = (
            total_entries == 0xFFFF or cd_offset == UINT32_MAX or _size == UINT32_MAX
        )
        if needs_zip64:
            return self._read_zip64(eocd_offset)
        return cd_offset, total_entries

    def _find_eocd(self) -> tuple[int, bytes]:
        size = self.path.stat().st_size
        if size < EOCD_SIZE:
            raise NotAnArchiveError(f"{self.path.name} is too small to be an archive")

        window = min(size, EOCD_SIZE + MAX_COMMENT)
        self._fp.seek(size - window)
        tail = self._fp.read(window)

        magic = struct.pack("<I", EOCD_SIGNATURE)
        position = tail.rfind(magic)
        while position != -1:
            if position + EOCD_SIZE <= len(tail):
                return size - window + position, tail[position : position + EOCD_SIZE]
            position = tail.rfind(magic, 0, position)

        raise NotAnArchiveError(
            f"{self.path.name}: no end-of-central-directory record found; "
            f"not a .p4k/zip archive or truncated"
        )

    def _read_zip64(self, eocd_offset: int) -> tuple[int, int]:
        locator_offset = eocd_offset - EOCD64_LOCATOR_SIZE
        if locator_offset < 0:
            raise CorruptArchiveError(f"{self.path.name}: ZIP64 locator missing")

        self._fp.seek(locator_offset)
        signature, _disk, eocd64_offset, _disks = struct.unpack(
            "<IIQI", self._fp.read(EOCD64_LOCATOR_SIZE)
        )
        if signature != ZIP64_LOCATOR_SIGNATURE:
            raise CorruptArchiveError(
                f"{self.path.name}: bad ZIP64 locator signature 0x{signature:08X}"
            )

        self._fp.seek(eocd64_offset)
        header = self._fp.read(56)
        if len(header) < 56:
            raise CorruptArchiveError(f"{self.path.name}: truncated ZIP64 record")

        (
            signature, _size, _version, _needed, _disk, _start_disk,
            _entries_disk, total_entries, _cd_size, cd_offset,
        ) = struct.unpack("<IQHHIIQQQQ", header)

        if signature != EOCD64_SIGNATURE:
            raise CorruptArchiveError(
                f"{self.path.name}: bad ZIP64 record signature 0x{signature:08X}"
            )
        return cd_offset, total_entries

    # --- entry data ----------------------------------------------------------

    def _read_raw(self, entry: P4KEntry) -> bytes:
        self._fp.seek(entry.header_offset)
        header = self._fp.read(LOCAL_HEADER_SIZE)
        if len(header) < LOCAL_HEADER_SIZE:
            raise CorruptArchiveError(f"{entry.filename}: truncated local header")

        signature, *_rest = struct.unpack("<IHHHHHIIIHH", header)
        if signature not in (LOCAL_FILE_SIGNATURE, LOCAL_FILE_CIG_SIGNATURE):
            # CIG uses 0x14034B50 alongside the standard value; anything else
            # means the offset is wrong or the archive is damaged.
            raise CorruptArchiveError(
                f"{entry.filename}: bad local header signature 0x{signature:08X}"
            )

        name_len, extra_len = struct.unpack("<HH", header[26:30])
        self._fp.seek(name_len + extra_len, 1)

        data = self._fp.read(entry.compress_size)
        if len(data) < entry.compress_size:
            raise CorruptArchiveError(
                f"{entry.filename}: wanted {entry.compress_size} bytes, archive ended early"
            )
        return data

    def _decrypt(self, data: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise EncryptionUnavailableError(
                "this entry is encrypted; install the 'cryptography' package to read it"
            ) from exc

        if len(data) % 16:
            raise CorruptArchiveError("encrypted entry is not a whole number of blocks")

        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(b"\x00" * 16)).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()

        # Zero padding is used rather than PKCS#7; the real length comes from
        # the entry header, so trailing padding is simply left for the caller.
        return plain

    def _decompress(self, entry: P4KEntry, raw: bytes) -> bytes:
        if entry.compress_type == METHOD_STORE:
            return raw[: entry.file_size] if entry.file_size else raw

        if entry.compress_type == METHOD_DEFLATE:
            return zlib.decompress(raw, -zlib.MAX_WBITS)

        if entry.compress_type == METHOD_ZSTD:
            try:
                import zstandard
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise UnsupportedCompressionError(
                    "this entry uses ZStandard; install the 'zstandard' package"
                ) from exc
            try:
                return zstandard.ZstdDecompressor().decompress(
                    raw, max_output_size=entry.file_size or 0
                )
            except zstandard.ZstdError as exc:
                raise CorruptArchiveError(f"{entry.filename}: {exc}") from exc

        raise UnsupportedCompressionError(
            f"{entry.filename}: unsupported compression method {entry.compress_type}"
        )


def _is_encrypted(extra: bytes) -> bool:
    return len(extra) > ENCRYPTION_FLAG_OFFSET and extra[ENCRYPTION_FLAG_OFFSET] != 0


def _apply_zip64(
    extra: bytes, compress_size: int, file_size: int, header_offset: int
) -> tuple[int, int, int]:
    """Replace 0xFFFFFFFF sentinels from the ZIP64 extra field, in field order."""
    if UINT32_MAX not in (compress_size, file_size, header_offset):
        return compress_size, file_size, header_offset

    offset = 0
    while offset + 4 <= len(extra):
        tag, size = struct.unpack("<HH", extra[offset : offset + 4])
        body = extra[offset + 4 : offset + 4 + size]
        offset += 4 + size

        if tag != 0x0001:
            continue

        cursor = 0

        def take(current: int) -> int:
            """Next 64-bit field, or the existing value if the body is short.

            Compared against None rather than truthiness: a header offset of 0
            is legitimate for the first entry in the archive.
            """
            nonlocal cursor
            if cursor + 8 > len(body):
                return current
            value = struct.unpack("<Q", body[cursor : cursor + 8])[0]
            cursor += 8
            return value

        if file_size in (UINT32_MAX, UINT64_MAX):
            file_size = take(file_size)
        if compress_size == UINT32_MAX:
            compress_size = take(compress_size)
        if header_offset == UINT32_MAX:
            header_offset = take(header_offset)
        break

    return compress_size, file_size, header_offset
