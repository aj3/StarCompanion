"""Reader/writer for Star Citizen's global.ini localization table.

The game's loader is unforgiving: the file is UTF-8 with a BOM, LF-only, one
`key=value` per line, and newlines inside a value are the literal two
characters ``\\n``. A real newline splits the entry and the contract renders
blank in-game.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BOM = "﻿"
PLURAL_SUFFIX = ",P"


@dataclass
class Entry:
    key: str
    value: str

    def render(self) -> str:
        return f"{self.key}={self.value}"


@dataclass
class RawLine:
    """A line carrying no `=`. Preserved verbatim so round-trips stay exact."""

    text: str

    def render(self) -> str:
        return self.text


class LocalizationFile:
    def __init__(
        self,
        lines: list[Entry | RawLine],
        *,
        bom: bool,
        trailing_newline: bool,
        newline: str = "\n",
    ):
        self._lines = lines
        self.bom = bom
        self.trailing_newline = trailing_newline
        self.newline = newline
        """The file's own convention. CIG ships CRLF; community packs are
        often LF. Values must not carry a stray \\r either way."""
        self._index: dict[str, int] = {}
        for i, line in enumerate(lines):
            if isinstance(line, Entry):
                self._index.setdefault(line.key, i)

    @classmethod
    def load(cls, path: Path) -> LocalizationFile:
        return cls.loads(path.read_bytes().decode("utf-8"))

    @classmethod
    def loads(cls, text: str) -> LocalizationFile:
        bom = text.startswith(BOM)
        if bom:
            text = text[len(BOM) :]

        # CIG ships CRLF; StarStrings and other packs ship LF. Detect rather
        # than assume -- splitting CRLF on "\n" leaves a stray \r on every
        # value, which then leaks into rendered output.
        newline = "\r\n" if "\r\n" in text else "\n"

        trailing_newline = text.endswith("\n")
        if trailing_newline:
            text = text[: -len(newline)] if text.endswith(newline) else text[:-1]

        lines: list[Entry | RawLine] = []
        for line in text.split(newline):
            key, sep, value = line.partition("=")
            lines.append(Entry(key, value) if sep else RawLine(line))

        return cls(
            lines, bom=bom, trailing_newline=trailing_newline, newline=newline
        )

    def dumps(self) -> str:
        text = self.newline.join(line.render() for line in self._lines)
        if self.trailing_newline:
            text += self.newline
        return (BOM if self.bom else "") + text

    def save(self, path: Path) -> None:
        path.write_bytes(self.dumps().encode("utf-8"))

    def resolve_key(self, key: str) -> str | None:
        """Return the key as it actually appears, tolerating the `,P` suffix.

        Roughly 15% of global.ini keys carry a `,P` suffix that datamined key
        lists routinely omit, so an exact-match-only lookup silently drops them.
        """
        if key in self._index:
            return key
        plural = key + PLURAL_SUFFIX
        if plural in self._index:
            return plural
        base = key.removesuffix(PLURAL_SUFFIX)
        return base if base != key and base in self._index else None

    def get(self, key: str) -> str | None:
        resolved = self.resolve_key(key)
        return self._lines[self._index[resolved]].value if resolved else None

    def set(self, key: str, value: str) -> bool:
        """Overwrite an existing entry. Returns False if the key is absent."""
        resolved = self.resolve_key(key)
        if resolved is None:
            return False
        self._lines[self._index[resolved]].value = value
        return True

    def keys(self) -> list[str]:
        return list(self._index)

    def entries(self) -> list[Entry]:
        return [line for line in self._lines if isinstance(line, Entry)]

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, key: str) -> bool:
        return self.resolve_key(key) is not None
