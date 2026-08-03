"""Readers for Star Citizen's own data files.

Everything here is strictly read-only against the game install.
"""

from . import cryxml, datacore
from .cryxml import CryXmlError, Node
from .p4k import P4KArchive, P4KEntry, P4KError

__all__ = [
    "CryXmlError",
    "Node",
    "P4KArchive",
    "P4KEntry",
    "P4KError",
    "cryxml",
    "datacore",
]
