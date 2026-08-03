import os
import struct
import time
from pathlib import Path

import pytest

import p4kbuilder as B
from starcompanion.extract.p4k import (
    CorruptArchiveError,
    NotAnArchiveError,
    P4KArchive,
    UnsupportedCompressionError,
)
from starcompanion.ini import LocalizationFile

GLOBAL_INI = b"\xef\xbb\xbfFoo=bar\nBaz,P=qux\n"


def write(tmp_path, builder: B.Builder, name: str = "Data.p4k") -> Path:
    path = tmp_path / name
    path.write_bytes(builder.build())
    return path


@pytest.fixture
def archive_path(tmp_path):
    return write(
        tmp_path,
        B.Builder()
        .add("Data/Localization/english/global.ini", GLOBAL_INI)
        .add("Data/Localization/french/global.ini", b"\xef\xbb\xbfFoo=baz\n")
        .add("Data/Game.dcb", b"DCB-CONTENT" * 100, method=B.METHOD_ZSTD)
        .add("Data/plain.bin", b"stored"),
    )


# --- listing -----------------------------------------------------------------


def test_lists_entries(archive_path):
    with P4KArchive(archive_path) as archive:
        assert len(archive) == 4
        assert "Data/Game.dcb" in archive
        assert "nope" not in archive


def test_getinfo_reports_entry_metadata(archive_path):
    with P4KArchive(archive_path) as archive:
        entry = archive.getinfo("Data/Game.dcb")
        assert entry.compression == "zstd"
        assert entry.file_size == len(b"DCB-CONTENT" * 100)
        assert not entry.is_encrypted


def test_getinfo_names_the_archive_when_missing(archive_path):
    with P4KArchive(archive_path) as archive:
        with pytest.raises(KeyError, match="Data.p4k"):
            archive.getinfo("Data/nope")


def test_search_is_glob_and_case_insensitive(archive_path):
    with P4KArchive(archive_path) as archive:
        assert len(archive.search("Data/Localization/*/global.ini")) == 2
        assert len(archive.search("data/localization/*/GLOBAL.INI")) == 2


def test_languages_are_discovered(archive_path):
    with P4KArchive(archive_path) as archive:
        assert archive.languages() == ["english", "french"]


# --- compression methods -----------------------------------------------------


def test_stored_entry(archive_path):
    with P4KArchive(archive_path) as archive:
        assert archive.read("Data/plain.bin") == b"stored"


def test_zstd_entry(archive_path):
    """Method 100 is outside the ZIP spec; stock tooling refuses it."""
    with P4KArchive(archive_path) as archive:
        assert archive.read("Data/Game.dcb") == b"DCB-CONTENT" * 100


def test_deflate_entry(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"z" * 5000, method=B.METHOD_DEFLATE))
    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == b"z" * 5000


def test_unknown_compression_method_is_reported_clearly(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"data"))
    raw = bytearray(path.read_bytes())
    # Rewrite the method field in the central directory header.
    index = raw.rfind(struct.pack("<I", B.CENTRAL_DIR_SIGNATURE))
    struct.pack_into("<H", raw, index + 10, 42)
    path.write_bytes(raw)

    with P4KArchive(path) as archive:
        with pytest.raises(UnsupportedCompressionError, match="42"):
            archive.read("a.bin")


# --- CIG deviations ----------------------------------------------------------


def test_cig_local_header_signature_is_accepted(tmp_path):
    """CIG uses 0x14034B50 where the spec says 0x04034B50."""
    path = write(tmp_path, B.Builder().add("a.bin", b"cig entry", cig_signature=True))
    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == b"cig entry"


def test_standard_local_header_signature_still_works(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"std entry"))
    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == b"std entry"


def test_encryption_is_detected_from_the_extra_field(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"secret", encrypted=True))
    with P4KArchive(path) as archive:
        assert archive.getinfo("a.bin").is_encrypted


def test_encrypted_entry_round_trips(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"classified payload", encrypted=True))
    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == b"classified payload"


def test_encrypted_and_compressed_entry(tmp_path):
    payload = b"compressed and encrypted " * 50
    path = write(
        tmp_path,
        B.Builder().add("a.bin", payload, method=B.METHOD_ZSTD, encrypted=True),
    )
    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == payload


def test_plain_entries_are_not_marked_encrypted(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"plain"))
    with P4KArchive(path) as archive:
        assert not archive.getinfo("a.bin").is_encrypted


# --- ZIP64 -------------------------------------------------------------------


def test_zip64_entry_sizes_and_offsets(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"zip64 entry", zip64=True))
    with P4KArchive(path) as archive:
        entry = archive.getinfo("a.bin")
        assert entry.file_size == len(b"zip64 entry")
        assert entry.header_offset != 0xFFFFFFFF
        assert archive.read("a.bin") == b"zip64 entry"


def test_zip64_end_of_central_directory(tmp_path):
    builder = B.Builder(force_zip64_eocd=True)
    builder.add("a.bin", b"one").add("b.bin", b"two")
    path = write(tmp_path, builder)

    with P4KArchive(path) as archive:
        assert archive.namelist() == ["a.bin", "b.bin"]
        assert archive.read("b.bin") == b"two"


def test_zip64_entry_and_zip64_eocd_together(tmp_path):
    builder = B.Builder(force_zip64_eocd=True)
    builder.add("a.bin", b"both", zip64=True)
    path = write(tmp_path, builder)

    with P4KArchive(path) as archive:
        assert archive.read("a.bin") == b"both"


# --- localization ------------------------------------------------------------


def test_read_localization_returns_the_stock_file(archive_path):
    with P4KArchive(archive_path) as archive:
        assert archive.read_localization("english") == GLOBAL_INI


def test_stock_localization_parses_with_the_ini_reader(archive_path):
    """The extracted bytes must feed straight into the Phase 0 parser."""
    with P4KArchive(archive_path) as archive:
        data = archive.read_localization("english")

    parsed = LocalizationFile.loads(data.decode("utf-8"))
    assert parsed.get("Foo") == "bar"
    assert parsed.resolve_key("Baz") == "Baz,P"
    assert parsed.dumps().encode("utf-8") == data


def test_missing_language_lists_what_is_available(archive_path):
    with P4KArchive(archive_path) as archive:
        with pytest.raises(KeyError, match="english, french"):
            archive.read_localization("klingon")


# --- extraction --------------------------------------------------------------


def test_extract_writes_only_under_the_destination(archive_path, tmp_path):
    destination = tmp_path / "out" / "global.ini"
    with P4KArchive(archive_path) as archive:
        written = archive.extract("Data/Localization/english/global.ini", destination)

    assert written == destination
    assert destination.read_bytes() == GLOBAL_INI


def test_extract_into_a_directory_uses_the_entry_name(archive_path, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with P4KArchive(archive_path) as archive:
        written = archive.extract("Data/Localization/english/global.ini", out)

    assert written == out / "global.ini"


# --- read-only guarantee -----------------------------------------------------


def test_archive_is_not_modified_by_reading(archive_path):
    before = archive_path.read_bytes()
    stat_before = archive_path.stat()
    time.sleep(0.01)

    with P4KArchive(archive_path) as archive:
        for entry in archive.entries:
            archive.read(entry)

    assert archive_path.read_bytes() == before
    assert archive_path.stat().st_size == stat_before.st_size
    assert archive_path.stat().st_mtime == stat_before.st_mtime


def test_archive_can_be_read_from_a_readonly_file(archive_path):
    archive_path.chmod(0o444)
    try:
        with P4KArchive(archive_path) as archive:
            assert archive.read("Data/plain.bin") == b"stored"
    finally:
        archive_path.chmod(0o644)


def test_closing_releases_the_handle(archive_path):
    archive = P4KArchive(archive_path)
    archive.close()
    archive.close()  # idempotent


# --- failure handling --------------------------------------------------------


def test_missing_file_reports_the_path(tmp_path):
    from starcompanion.extract.p4k import P4KError

    with pytest.raises(P4KError, match="cannot open"):
        P4KArchive(tmp_path / "nope.p4k")


def test_empty_file_is_not_an_archive(tmp_path):
    path = tmp_path / "empty.p4k"
    path.write_bytes(b"")
    with pytest.raises(NotAnArchiveError, match="too small"):
        P4KArchive(path)


def test_random_bytes_are_not_an_archive(tmp_path):
    path = tmp_path / "junk.p4k"
    path.write_bytes(os.urandom(4096))
    with pytest.raises(NotAnArchiveError, match="no end-of-central-directory"):
        P4KArchive(path)


def test_truncated_archive_fails_with_a_clear_error(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"x" * 100))
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])

    with pytest.raises(NotAnArchiveError):
        P4KArchive(path)


def test_entry_data_cut_short_is_reported(tmp_path):
    builder = B.Builder().add("a.bin", b"x" * 5000)
    path = write(tmp_path, builder)
    raw = bytearray(path.read_bytes())
    # Claim a much larger compressed size than the archive holds.
    index = raw.rfind(struct.pack("<I", B.CENTRAL_DIR_SIGNATURE))
    struct.pack_into("<I", raw, index + 20, 10_000_000)
    path.write_bytes(raw)

    with P4KArchive(path) as archive:
        with pytest.raises(CorruptArchiveError, match="archive ended early"):
            archive.read("a.bin")


def test_bad_local_header_signature_is_reported(tmp_path):
    path = write(tmp_path, B.Builder().add("a.bin", b"data"))
    raw = bytearray(path.read_bytes())
    struct.pack_into("<I", raw, 0, 0xDEADBEEF)
    path.write_bytes(raw)

    with P4KArchive(path) as archive:
        with pytest.raises(CorruptArchiveError, match="bad local header signature"):
            archive.read("a.bin")


def test_failed_open_does_not_leak_a_handle(tmp_path):
    path = tmp_path / "junk.p4k"
    path.write_bytes(os.urandom(4096))
    with pytest.raises(NotAnArchiveError):
        P4KArchive(path)
    path.unlink()  # would fail on Windows if the handle were still open


# --- memory ------------------------------------------------------------------


def test_reading_one_entry_does_not_load_the_whole_archive(tmp_path):
    """Bounded reads matter: the real archive is multi-gigabyte."""
    filler = b"F" * (4 << 20)
    builder = B.Builder()
    for i in range(4):
        builder.add(f"filler{i}.bin", filler)
    builder.add("Data/small.ini", b"wanted")
    path = write(tmp_path, builder)

    assert path.stat().st_size > 16 << 20

    reads: list[int] = []

    with P4KArchive(path) as archive:
        original = archive._fp.read

        def counting_read(size=-1):
            data = original(size)
            reads.append(len(data))
            return data

        archive._fp.read = counting_read
        assert archive.read("Data/small.ini") == b"wanted"

    assert max(reads) < 1 << 20, "a single read pulled in far more than the entry"
    assert sum(reads) < 1 << 20, "reading one small entry touched too much data"
