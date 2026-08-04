"""Parent-owned filesystem contract for archive helper processes.

Helpers may write only the paths declared here.  The parent creates the
workspace before spawning, validates every path before use, and removes the
known files without a recursive delete.  This keeps cleanup reliable even
when a child is terminated and cannot execute ``finally`` blocks.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HelperArtifacts:
    root: Path
    baseline: Path
    input: Path
    result: Path
    datacore: Path | None = None

    @classmethod
    def create(cls) -> HelperArtifacts:
        root = Path(tempfile.mkdtemp(prefix="starcompanion-helper-"))
        artifacts = cls(
            root=root,
            baseline=root / "baseline.ini",
            input=root / "input.jsonl",
            result=root / "result.jsonl",
            datacore=root / "Game2.dcb",
        )
        artifacts.validate()
        return artifacts

    @property
    def paths(self) -> tuple[Path, ...]:
        declared = (self.baseline, self.input, self.result)
        return declared + ((self.datacore,) if self.datacore is not None else ())

    def validate(self) -> None:
        root = self.root.resolve()
        for path in self.paths:
            if path.resolve().parent != root:
                raise ValueError(f"helper artifact escapes its workspace: {path}")

    def discard_result(self) -> None:
        self.validate()
        self.result.unlink(missing_ok=True)

    def discard_transient(self) -> None:
        self.validate()
        self.input.unlink(missing_ok=True)
        self.result.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Remove only declared artifacts, then the now-empty workspace."""
        self.validate()
        for path in self.paths:
            path.unlink(missing_ok=True)
        try:
            self.root.rmdir()
        except FileNotFoundError:
            pass


__all__ = ["HelperArtifacts"]
