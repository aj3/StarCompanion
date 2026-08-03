"""Frozen-build entry point.

PyInstaller runs its target script as `__main__`, which breaks the package's
relative imports if `gui/app.py` is used directly. Importing through the package
keeps them working.
"""

import sys

from starcompanion.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
