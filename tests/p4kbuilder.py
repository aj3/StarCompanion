"""Build p4k-shaped archives for tests.

Written independently of the reader so the tests exercise the format rather
than the reader's own assumptions: this module packs bytes from the spec in
docs/format-notes.md, including the CIG deviations, and the reader has to cope.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

CENTRAL_DIR_SIGNATURE = 0x02014B50
EOCD_SIGNATURE = 0x06054B50
EOCD64_SIGNATURE = 0x06064B50
ZIP64_LOCATOR_SIGNATURE = 0x07064B50
LOCAL_FILE_SIGNATURE = 0x04034B50
LOCAL_FILE_CIG_SIGNATURE = 0x14034B50

METHOD_STORE = 0
METHOD_DEFLATE = 8
METHOD_ZSTD = 100

UINT32_MAX = 0xFFFFFFFF
ENCRYPTION_FLAG_OFFSET = 168
KEY = bytes.fromhex("5E7A2002302EEB1A3BB617C30FDE1E47")


@dataclass
class Member:
    name: str
    data: bytes
    method: int = METHOD_STORE
    encrypted: bool = False
    cig_signature: bool = False
    zip64: bool = False


@dataclass
class Builder:
    members: list[Member] = field(default_factory=list)
    force_zip64_eocd: bool = False

    def add(self, name: str, data: bytes, **kwargs) -> Builder:
        self.members.append(Member(name, data, **kwargs))
        return self

    def build(self) -> bytes:
        out = bytearray()
        directory: list[tuple[Member, int, bytes, int]] = []

        for member in self.members:
            offset = len(out)
            payload = _compress(member.data, member.method)
            if member.encrypted:
                payload = _encrypt(payload)

            signature = (
                LOCAL_FILE_CIG_SIGNATURE if member.cig_signature else LOCAL_FILE_SIGNATURE
            )
            name = member.name.encode("utf-8")
            # sig, version, flags, method, time, date, crc, comp, uncomp, name, extra
            out += struct.pack(
                "<IHHHHHIIIHH",
                signature, 20, 0, member.method, 0, 0,
                zlib.crc32(member.data), len(payload), len(member.data),
                len(name), 0,
            )
            out += name
            out += payload

            directory.append((member, offset, payload, len(payload)))

        cd_offset = len(out)
        for member, offset, _payload, compressed_len in directory:
            extra = _extra_field(member, compressed_len, len(member.data), offset)
            name = member.name.encode("utf-8")

            stored_compressed = UINT32_MAX if member.zip64 else compressed_len
            stored_size = UINT32_MAX if member.zip64 else len(member.data)
            stored_offset = UINT32_MAX if member.zip64 else offset

            out += struct.pack(
                "<IHHHHHHIIIHHHHHII",
                CENTRAL_DIR_SIGNATURE, 20, 20, 0, member.method, 0, 0,
                zlib.crc32(member.data), stored_compressed, stored_size,
                len(name), len(extra), 0, 0, 0, 0, stored_offset,
            )
            out += name
            out += extra

        cd_size = len(out) - cd_offset
        count = len(directory)

        if self.force_zip64_eocd:
            eocd64_offset = len(out)
            out += struct.pack(
                "<IQHHIIQQQQ",
                EOCD64_SIGNATURE, 44, 45, 45, 0, 0, count, count, cd_size, cd_offset,
            )
            out += struct.pack(
                "<IIQI", ZIP64_LOCATOR_SIGNATURE, 0, eocd64_offset, 1
            )
            out += struct.pack(
                "<IHHHHIIH", EOCD_SIGNATURE, 0, 0, 0xFFFF, 0xFFFF,
                UINT32_MAX, UINT32_MAX, 0,
            )
        else:
            out += struct.pack(
                "<IHHHHIIH", EOCD_SIGNATURE, 0, 0, count, count, cd_size, cd_offset, 0
            )

        return bytes(out)


def _compress(data: bytes, method: int) -> bytes:
    if method == METHOD_STORE:
        return data
    if method == METHOD_DEFLATE:
        compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        return compressor.compress(data) + compressor.flush()
    if method == METHOD_ZSTD:
        import zstandard

        return zstandard.ZstdCompressor().compress(data)
    raise ValueError(f"builder cannot produce method {method}")


def _encrypt(payload: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padded = payload + b"\x00" * (-len(payload) % 16)
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(b"\x00" * 16)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _extra_field(member: Member, compressed: int, size: int, offset: int) -> bytes:
    extra = b""

    if member.zip64:
        # Field order matters: size, then compressed size, then offset.
        body = struct.pack("<QQQ", size, compressed, offset)
        extra += struct.pack("<HH", 0x0001, len(body)) + body

    if member.encrypted:
        # CIG flags encryption by a non-zero byte at a fixed offset in the
        # extra field, not by the standard general-purpose bit. Pad with a
        # filler chunk to exactly that offset, then write the flag byte.
        if len(extra) < ENCRYPTION_FLAG_OFFSET:
            filler_len = ENCRYPTION_FLAG_OFFSET - len(extra) - 4
            extra += struct.pack("<HH", 0x9999, filler_len) + b"\x00" * filler_len
        extra += b"\x01"

    return extra
