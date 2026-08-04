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
import io
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Iterator

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
_PROGRESS_INTERVAL = 4096


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

    def __init__(
        self,
        path: Path,
        *,
        key: bytes = DEFAULT_KEY,
        progress: Callable[[int, int], None] | None = None,
        entry_progress: Callable[[str, int, int], None] | None = None,
        checkpoint: Callable[[], None] | None = None,
        entry_filter: Callable[[P4KEntry], bool] | None = None,
    ):
        self.path = Path(path)
        self.key = key
        self._progress = progress
        self._entry_progress = entry_progress
        self._checkpoint = checkpoint
        self._cig_advisory_crc: set[str] = set()
        self.integrity_warnings: list[str] = []
        if self._checkpoint is not None:
            self._checkpoint()
        try:
            self._fp: BinaryIO = self.path.open("rb")
        except OSError as exc:
            raise P4KError(f"cannot open {self.path}: {exc}") from exc

        try:
            self._entries: dict[str, P4KEntry] = {}
            for entry in self._read_central_directory():
                if entry_filter is not None and not entry_filter(entry):
                    continue
                if entry.filename in self._entries:
                    raise CorruptArchiveError(
                        f"{self.path.name}: duplicate archive entry {entry.filename!r}"
                    )
                self._entries[entry.filename] = entry
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
        from ..install import normalize_language

        languages: set[str] = set()
        for entry in self.localization_files():
            parts = entry.filename.split("/")
            if (
                len(parts) != 4
                or parts[0].casefold() != "data"
                or parts[1].casefold() != "localization"
                or parts[3].casefold() != "global.ini"
            ):
                continue
            try:
                languages.add(normalize_language(parts[2]))
            except ValueError:
                continue
        return sorted(languages)

    def _localization_entry(self, language: str) -> P4KEntry | None:
        from ..install import normalize_language

        selected = normalize_language(language)
        matches = []
        for entry in self.localization_files():
            parts = entry.filename.split("/")
            if len(parts) != 4:
                continue
            try:
                candidate = normalize_language(parts[2])
            except ValueError:
                continue
            if (
                parts[0].casefold() == "data"
                and parts[1].casefold() == "localization"
                and parts[3].casefold() == "global.ini"
                and candidate == selected
            ):
                matches.append(entry)
        if len(matches) > 1:
            raise CorruptArchiveError(
                f"multiple localization entries map to language {selected!r}"
            )
        return matches[0] if matches else None

    # --- reading -------------------------------------------------------------

    def read(self, name: str | P4KEntry) -> bytes:
        """Compatibility helper that joins :meth:`iter_read` into memory."""
        return b"".join(self.iter_read(name))

    def iter_read(self, name: str | P4KEntry) -> Iterator[bytes]:
        """Yield one entry as bounded decompressed chunks.

        Raw, decrypted, and decompressed forms are never assembled in full.
        Consumers that can stream should prefer this over :meth:`read`.
        """
        if self._checkpoint is not None:
            self._checkpoint()
        entry = name if isinstance(name, P4KEntry) else self.getinfo(name)
        chunks: Iterable[bytes] = self._iter_raw(entry)
        if entry.is_encrypted:
            chunks = self._iter_decrypt(
                chunks,
                entry.compress_size,
                strip_zero_padding=entry.compress_type != METHOD_STORE,
            )
        chunks = self._iter_decompress(entry, chunks)

        completed = 0
        checksum = 0
        for chunk in chunks:
            self._check_entry_cancelled()
            if not chunk:
                continue
            if completed + len(chunk) > entry.file_size:
                raise CorruptArchiveError(
                    f"{entry.filename}: decompressed data exceeds declared "
                    f"{entry.file_size} bytes"
                )
            completed += len(chunk)
            checksum = zlib.crc32(chunk, checksum)
            yield chunk

        self._check_entry_cancelled()
        if completed != entry.file_size:
            raise CorruptArchiveError(
                f"{entry.filename}: expected {entry.file_size} bytes, got {completed}"
            )
        if checksum != entry.crc:
            if entry.filename in self._cig_advisory_crc:
                warning = (
                    f"{entry.filename}: CIG-aligned method-100 entry uses a non-ZIP CRC field; "
                    "validated decompression and exact byte length instead"
                )
                if warning not in self.integrity_warnings:
                    self.integrity_warnings.append(warning)
            else:
                raise CorruptArchiveError(
                    f"{entry.filename}: CRC mismatch (expected {entry.crc:08x}, got {checksum:08x})"
                )

    def stream(self, name: str | P4KEntry, consumer: Callable[[bytes], object]) -> int:
        """Send decompressed chunks to ``consumer`` and return bytes written."""
        written = 0
        for chunk in self.iter_read(name):
            consumer(chunk)
            written += len(chunk)
        return written

    def extract(self, name: str | P4KEntry, destination: Path) -> Path:
        """Atomically write one verified entry under ``destination``.

        CRC, decompression, or cancellation failures remove the helper file
        and leave any prior destination untouched.
        """
        entry = name if isinstance(name, P4KEntry) else self.getinfo(name)
        destination = Path(destination)

        target = (
            destination / Path(entry.filename).name
            if destination.is_dir()
            else destination
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".partial", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                self.stream(entry, output.write)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
        return target

    def read_localization(self, language: str = "english") -> bytes:
        """The stock global.ini -- the pristine baseline for overwrite mode."""
        from ..install import normalize_language

        language = normalize_language(language)
        entry = self._localization_entry(language)
        if entry is not None:
            return self.read(entry)

        available = self.languages()
        raise KeyError(
            f"no localization for {language!r} in {self.path.name}"
            + (f"; available: {', '.join(available)}" if available else "")
        )

    def stream_localization(
        self,
        consumer: Callable[[bytes], object],
        language: str = "english",
    ) -> int:
        """Stream the pristine localization table to a chunk consumer."""
        from ..install import normalize_language

        language = normalize_language(language)
        entry = self._localization_entry(language)
        if entry is not None:
            return self.stream(entry, consumer)
        available = self.languages()
        raise KeyError(
            f"no localization for {language!r} in {self.path.name}"
            + (f"; available: {', '.join(available)}" if available else "")
        )

    # --- central directory ---------------------------------------------------

    def _read_central_directory(self) -> Iterator[P4KEntry]:
        offset, count = self._locate_central_directory()
        self._central_directory_offset = offset
        self._fp.seek(offset)

        for index in range(count):
            if index % _PROGRESS_INTERVAL == 0:
                if self._checkpoint is not None:
                    self._checkpoint()
                if self._progress is not None:
                    self._progress(index, count)
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

        if self._checkpoint is not None:
            self._checkpoint()
        if self._progress is not None:
            self._progress(count, count)

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

    def _iter_raw(self, entry: P4KEntry) -> Iterator[bytes]:
        self._fp.seek(entry.header_offset)
        header = self._fp.read(LOCAL_HEADER_SIZE)
        if len(header) < LOCAL_HEADER_SIZE:
            raise CorruptArchiveError(f"{entry.filename}: truncated local header")

        (
            signature,
            _needed,
            _flags,
            local_method,
            _time,
            _date,
            local_crc,
            local_compress_size,
            local_file_size,
            name_len,
            extra_len,
        ) = struct.unpack("<IHHHHHIIIHH", header)
        if signature not in (LOCAL_FILE_SIGNATURE, LOCAL_FILE_CIG_SIGNATURE):
            # CIG uses 0x14034B50 alongside the standard value; anything else
            # means the offset is wrong or the archive is damaged.
            raise CorruptArchiveError(
                f"{entry.filename}: bad local header signature 0x{signature:08X}"
            )

        local_name = self._fp.read(name_len).decode("utf-8", errors="replace").replace("\\", "/")
        if local_name != entry.filename:
            raise CorruptArchiveError(f"{entry.filename}: local/central filename mismatch")
        if local_method != entry.compress_type:
            raise CorruptArchiveError(f"{entry.filename}: local/central header mismatch")
        if local_crc != entry.crc:
            raise CorruptArchiveError(
                f"{entry.filename}: CRC mismatch between local/central headers"
            )
        self._fp.seek(extra_len, 1)

        # Current CIG archives align method-100 payloads to 4 KiB with a large
        # local-header padding field. Their central/local "CRC" values do not
        # equal ZIP CRC-32 of the decompressed bytes (including Game2.dcb and
        # every shipped localization inspected in LIVE 4.9). The established
        # unp4k reader likewise treats GetInputStream as the extraction path and
        # performs CRC only in its separate TestArchive operation. For this
        # unambiguous CIG layout, retain exact compressed/uncompressed length
        # and decompressor validation but treat the incompatible field as
        # advisory. Ordinary ZIP-shaped fixtures and entries still require CRC.
        data_offset = self._fp.tell()
        cig_aligned = (
            entry.compress_type == METHOD_ZSTD
            and extra_len >= 512
            and data_offset % 4096 == 0
        )
        if cig_aligned:
            self._cig_advisory_crc.add(entry.filename)

        if data_offset + entry.compress_size > self._central_directory_offset:
            raise CorruptArchiveError(
                f"{entry.filename}: wanted {entry.compress_size} bytes, "
                "archive ended early before the central directory"
            )
        if (
            local_compress_size not in (entry.compress_size, UINT32_MAX)
            or local_file_size not in (entry.file_size, UINT32_MAX)
        ):
            raise CorruptArchiveError(
                f"{entry.filename}: local/central declared size mismatch"
            )

        remaining = entry.compress_size
        completed = 0
        self._report_entry("read", 0, entry.compress_size)
        while remaining:
            self._check_entry_cancelled()
            chunk = self._fp.read(min(_CHUNK, remaining))
            if not chunk:
                raise CorruptArchiveError(
                    f"{entry.filename}: wanted {entry.compress_size} bytes, "
                    f"archive ended early after {completed}"
                )
            completed += len(chunk)
            remaining -= len(chunk)
            self._report_entry("read", completed, entry.compress_size)
            yield chunk
        self._check_entry_cancelled()

    def _iter_decrypt(
        self,
        chunks: Iterable[bytes],
        total: int,
        *,
        strip_zero_padding: bool,
    ) -> Iterator[bytes]:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise EncryptionUnavailableError(
                "this entry is encrypted; install the 'cryptography' package to read it"
            ) from exc

        if total % 16:
            raise CorruptArchiveError("encrypted entry is not a whole number of blocks")

        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(b"\x00" * 16)).decryptor()
        completed = 0
        pending = b""
        self._report_entry("decrypt", 0, total)
        for chunk in chunks:
            self._check_entry_cancelled()
            completed += len(chunk)
            plain = decryptor.update(chunk)
            self._report_entry("decrypt", completed, total)
            if plain:
                if pending:
                    yield pending
                pending = plain
        final = decryptor.finalize()
        self._check_entry_cancelled()
        if final:
            pending += final
        if strip_zero_padding and pending:
            padding = len(pending) - len(pending.rstrip(b"\0"))
            if padding > 15:
                raise CorruptArchiveError("encrypted entry has invalid zero padding")
            if padding:
                pending = pending[:-padding]
        if pending:
            yield pending

    def _iter_decompress(
        self,
        entry: P4KEntry,
        chunks: Iterable[bytes],
    ) -> Iterator[bytes]:
        if entry.compress_type == METHOD_STORE:
            completed = 0
            self._report_entry("decompress", 0, entry.file_size)
            for chunk in chunks:
                self._check_entry_cancelled()
                if entry.is_encrypted:
                    remaining = max(0, entry.file_size - completed)
                    output, padding = chunk[:remaining], chunk[remaining:]
                    if padding and any(padding):
                        raise CorruptArchiveError(
                            f"{entry.filename}: encrypted store padding is not zero"
                        )
                else:
                    output = chunk
                completed += len(output)
                self._report_entry(
                    "decompress", min(completed, entry.file_size), entry.file_size
                )
                if output:
                    yield output
            return

        if entry.compress_type == METHOD_DEFLATE:
            decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
            completed = 0
            self._report_entry("decompress", 0, entry.compress_size)
            for chunk in chunks:
                self._check_entry_cancelled()
                completed += len(chunk)
                pending = chunk
                while pending:
                    output = decompressor.decompress(pending, _CHUNK)
                    pending = decompressor.unconsumed_tail
                    if output:
                        yield output
                self._report_entry(
                    "decompress", min(completed, entry.compress_size), entry.compress_size
                )
            output = decompressor.flush()
            for offset in range(0, len(output), _CHUNK):
                yield output[offset : offset + _CHUNK]
            if not decompressor.eof:
                raise CorruptArchiveError(f"{entry.filename}: truncated deflate stream")
            if decompressor.unused_data and (
                not entry.is_encrypted or any(decompressor.unused_data)
            ):
                raise CorruptArchiveError(f"{entry.filename}: trailing deflate data")
            self._check_entry_cancelled()
            return

        if entry.compress_type == METHOD_ZSTD:
            try:
                import zstandard
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise UnsupportedCompressionError(
                    "this entry uses ZStandard; install the 'zstandard' package"
                ) from exc
            try:
                completed = 0
                total = entry.file_size or 0
                self._report_entry("decompress", 0, total)
                source = _IteratorReader(chunks)
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    while True:
                        self._check_entry_cancelled()
                        if total and completed >= total:
                            break
                        wanted = min(_CHUNK, total - completed) if total else _CHUNK
                        chunk = reader.read(wanted)
                        if not chunk:
                            break
                        completed += len(chunk)
                        self._report_entry("decompress", completed, total)
                        yield chunk
                    if total and completed == total and reader.read(1):
                        raise CorruptArchiveError(
                            f"{entry.filename}: decompressed data exceeds declared "
                            f"{entry.file_size} bytes"
                        )
                self._check_entry_cancelled()
                return
            except zstandard.ZstdError as exc:
                raise CorruptArchiveError(f"{entry.filename}: {exc}") from exc

        raise UnsupportedCompressionError(
            f"{entry.filename}: unsupported compression method {entry.compress_type}"
        )

    def _check_entry_cancelled(self) -> None:
        if self._checkpoint is not None:
            self._checkpoint()

    def _report_entry(self, phase: str, current: int, total: int) -> None:
        if self._entry_progress is not None:
            self._entry_progress(phase, current, total)


class _IteratorReader(io.RawIOBase):
    """Small file-like adapter allowing zstd to pull from a chunk iterator."""

    def __init__(self, chunks: Iterable[bytes]):
        super().__init__()
        self._chunks = iter(chunks)
        self._pending = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        target = memoryview(buffer)
        written = 0
        while written < len(target):
            if not self._pending:
                try:
                    self._pending = memoryview(next(self._chunks))
                except StopIteration:
                    break
            size = min(len(target) - written, len(self._pending))
            target[written : written + size] = self._pending[:size]
            self._pending = self._pending[size:]
            written += size
        return written


def _is_encrypted(extra: bytes) -> bool:
    return len(extra) > ENCRYPTION_FLAG_OFFSET and extra[ENCRYPTION_FLAG_OFFSET] != 0


def is_localization_entry(entry: P4KEntry) -> bool:
    """True for language global.ini files, without retaining unrelated entries."""
    return fnmatch.fnmatch(entry.filename.casefold(), LOCALIZATION_GLOB.casefold())


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
