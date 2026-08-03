"""Optional behaviour that can be switched off without deleting the code.

`COMMUNITY_REWARDS` controls whether the interface offers reward numbers from a
community contract list. It is **off**: the project's purpose is to read the
game's own files, and a download from someone else's project does not belong in
the main flow.

The code behind it is untouched and fully tested -- `sources/contracts_ini.py`,
`sources/scmdb.py`, `sources/merge.py` -- so this is a hidden capability rather
than a removed one. Re-enable with:

    STARCOMPANION_COMMUNITY_REWARDS=1

The command line keeps `--contracts` and the `scmdb` command regardless: those
are explicit opt-ins that nobody encounters by accident.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def community_rewards_enabled() -> bool:
    """Whether the interface offers community-sourced reward numbers."""
    return os.environ.get("STARCOMPANION_COMMUNITY_REWARDS", "").strip().casefold() in _TRUTHY


def expert_tabs_enabled() -> bool:
    """Whether the hand-driven apply screen is shown.

    Off: the Start tab covers the whole job, and that screen exposes file
    paths and merge modes that only matter for a second game install, an older
    backup, or wiping another pack. The code is untouched and still tested.

        STARCOMPANION_EXPERT=1
    """
    return os.environ.get("STARCOMPANION_EXPERT", "").strip().casefold() in _TRUTHY
