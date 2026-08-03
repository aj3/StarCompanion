"""Frozen-build entry point for the command line.

Separate from `entry.py` because the two builds differ in one important way:
this one is a console application, so `--help` and command output are visible.
The GUI build has no console attached.
"""

import sys

from starcompanion.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
