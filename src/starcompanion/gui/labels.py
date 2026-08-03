"""Plain-language names for things the game calls something else.

The game's text styles are named `EM`, `EM1`…`EM4`, `b`, `i`. Those are the
literal tags written into `global.ini`, and they are meaningless to anyone who
has not read the file. The tag stays on the inside; only these names are shown.

Deliberately *not* claiming what each style looks like. Star Citizen's own
theme decides that, it is not documented, and inventing "bright yellow" would
be worse than admitting the app cannot preview it.
"""

from __future__ import annotations

# (tag, shown name, when to reach for it)
TEXT_STYLES: tuple[tuple[str, str, str], ...] = (
    ("EM4", "Highlight 4 — the usual choice", "What most contract packs use."),
    ("EM3", "Highlight 3", "A different emphasis style."),
    ("EM2", "Highlight 2", "A different emphasis style."),
    ("EM1", "Highlight 1", "A different emphasis style."),
    ("EM", "Highlight (plain)", "The basic emphasis style."),
    ("b", "Bold", "Ordinary bold text."),
    ("i", "Italic", "Ordinary italic text."),
)

STYLE_NAMES: dict[str, str] = {tag: name for tag, name, _ in TEXT_STYLES}
STYLE_HINTS: dict[str, str] = {tag: hint for tag, _, hint in TEXT_STYLES}

STYLE_CAPTION = (
    "These are the only text styles Star Citizen can display — it has no "
    "setting for custom colours. Exactly how each one looks is decided by the "
    "game's own theme, so pick one and check how it reads in game."
)

# Which piece of information each style setting applies to.
FIELD_NAMES: dict[str, str] = {
    "reputation": "Reputation earned",
    "pools": "Blueprint lists",
    "gates": "Rank requirements",
    "regional": "Location differences",
    "scenario": "Event points",
    "scrip": "MG Scrip",
    "title": "Tags in contract titles",
}

INHERIT = "Same as the main style"

# What each contract title prefix does, without naming the mechanism.
TITLE_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("none", "Nothing", "Leave contract titles as the game writes them."),
    (
        "org",
        "Who is offering it",
        "Puts the mission giver first, so a long list groups by company.",
    ),
    (
        "rank",
        "How hard it is",
        "Puts the difficulty number first, so easy and hard contracts stand apart.",
    ),
    (
        "org_rank",
        "Both",
        "Mission giver and difficulty together, e.g. [Foxwell 3].",
    ),
)

PREFIX_CAPTION = (
    "Star Citizen decides the order of the contract list itself, and no text "
    "change can re-sort it. Putting this at the front of each title is the "
    "next best thing: the list becomes easy to scan."
)
