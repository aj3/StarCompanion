"""Frozen-build entry point for the command line.

Separate from `entry.py` because the two builds differ in one important way:
this one is a console application, so `--help` and command output are visible.
The GUI build has no console attached.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    from starcompanion.offline import enforce_offline_from_environment

    enforce_offline_from_environment()
    multiprocessing.freeze_support()
    from starcompanion.__main__ import main

    sys.exit(main())
