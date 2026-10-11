"""Formations, role coordinates and positional compatibility."""

from __future__ import annotations

# Each slot: (role, base_x, base_y) in the team's attacking frame (see footballfoundry_schema).
FORMATIONS: dict[str, list[tuple[str, float, float]]] = {
    "4-3-3": [
        ("GK", 5, 50), ("RB", 30, 12), ("CB", 20, 37), ("CB", 20, 63), ("LB", 30, 88),
        ("DM", 40, 50), ("CM", 52, 32), ("CM", 52, 68),
        ("RW", 74, 14), ("LW", 74, 86), ("ST", 82, 50),
    ],
    "4-2-3-1": [
        ("GK", 5, 50), ("RB", 30, 12), ("CB", 20, 37), ("CB", 20, 63), ("LB", 30, 88),
        ("DM", 40, 38), ("DM", 40, 62), ("AM", 64, 50),
        ("RW", 70, 15), ("LW", 70, 85), ("ST", 82, 50),
    ],
    "4-4-2": [
        ("GK", 5, 50), ("RB", 28, 12), ("CB", 20, 37), ("CB", 20, 63), ("LB", 28, 88),
        ("RM", 55, 14), ("CM", 48, 38), ("CM", 48, 62), ("LM", 55, 86),
        ("ST", 78, 40), ("ST", 78, 60),
    ],
    "3-4-3": [
        ("GK", 5, 50), ("CB", 20, 28), ("CB", 18, 50), ("CB", 20, 72),
        ("RWB", 45, 10), ("CM", 45, 38), ("CM", 45, 62), ("LWB", 45, 90),
        ("RW", 72, 20), ("LW", 72, 80), ("ST", 82, 50),
    ],
    "3-5-2": [
        ("GK", 5, 50), ("CB", 20, 28), ("CB", 18, 50), ("CB", 20, 72),
        ("RWB", 45, 10), ("DM", 40, 50), ("CM", 52, 34), ("CM", 52, 66), ("LWB", 45, 90),
        ("ST", 78, 40), ("ST", 78, 60),
    ],
}

# Which squad positions can fill a formation role, in order of preference.
COMPATIBLE: dict[str, list[str]] = {
    "GK": ["GK"],
    "RB": ["RB", "LB", "CB"],
    "LB": ["LB", "RB", "CB"],
    "CB": ["CB", "DM", "RB", "LB"],
    "RWB": ["RB", "RW", "LB"],
    "LWB": ["LB", "LW", "RB"],
    "DM": ["DM", "CM", "CB"],
    "CM": ["CM", "DM", "AM"],
    "AM": ["AM", "CM", "RW", "LW"],
    "RM": ["RW", "LW", "CM", "RB"],
    "LM": ["LW", "RW", "CM", "LB"],
    "RW": ["RW", "LW", "AM", "ST"],
    "LW": ["LW", "RW", "AM", "ST"],
    "ST": ["ST", "AM", "RW", "LW"],
}

DEFENSIVE_ROLES = {"RB", "CB", "LB", "RWB", "LWB"}
ATTACKING_ROLES = {"ST", "RW", "LW", "AM"}

# Where a role plays when a player is moved into it mid-match (RoleChange), outside any formation table.
ROLE_COORDS: dict[str, tuple[float, float]] = {
    "GK": (5, 50), "RB": (30, 12), "LB": (30, 88), "CB": (19, 50), "RWB": (45, 10), "LWB": (45, 90),
    "DM": (40, 50), "CM": (50, 50), "AM": (64, 50), "RM": (55, 14), "LM": (55, 86), "RW": (72, 16),
    "LW": (72, 84), "ST": (82, 50),
}

# Nearest equivalent role when a formation doesn't have the one a scenario names.
ROLE_EQUIVALENTS: dict[str, list[str]] = {
    "RW": ["RM", "RWB"], "LW": ["LM", "LWB"], "RM": ["RW", "RWB"], "LM": ["LW", "LWB"], "AM": ["CM"],
    "DM": ["CM"], "RB": ["RWB"], "LB": ["LWB"], "RWB": ["RB"], "LWB": ["LB"],
}
