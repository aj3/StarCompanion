"""Frozen-build entry point.

PyInstaller runs its target script as `__main__`, which breaks the package's
relative imports if `gui/app.py` is used directly. Importing through the package
keeps them working.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    from starcompanion.offline import enforce_offline_from_environment

    enforce_offline_from_environment()
    multiprocessing.freeze_support()
    from starcompanion.gui.app import main

    sys.exit(main())
