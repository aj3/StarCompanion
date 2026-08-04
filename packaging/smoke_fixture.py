"""Create a tiny synthetic install for frozen-build smoke tests.

The strings are deliberately invented test data.  No game files are copied or
downloaded, and the resulting archive exercises only the ZIP subset needed by
the stock-localization reader.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


GLOBAL_INI = (
    "\ufeff"
    "Foxwell_Test_title=Do the synthetic contract\n"
    "Foxwell_Test_desc=Travel to ~mission(Location|Address) and complete the synthetic job.\n"
)


def build(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "Data.p4k"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(
            "Data/Localization/english/global.ini",
            GLOBAL_INI.encode("utf-8"),
        )
    return root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(build(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
