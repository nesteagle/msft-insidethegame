"""Pitch geometry and the expected-goals model."""

from __future__ import annotations

import math

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0
GOAL_WIDTH_M = 7.32

# Penalty area in pitch units (16.5m deep, 40.32m wide).
BOX_X = 100 - 16.5 / PITCH_LENGTH_M * 100
BOX_Y_MIN = 50 - 20.16 / PITCH_WIDTH_M * 100
BOX_Y_MAX = 50 + 20.16 / PITCH_WIDTH_M * 100
PENALTY_SPOT = (100 - 11 / PITCH_LENGTH_M * 100, 50.0)

# Logistic xG coefficients: logit = B0 + B_ANGLE * angle_rad + B_DIST * distance_m.
# Gives ~0.46 at 6m central, ~0.20 at the penalty spot, ~0.11 at 16m, ~0.04 at 25m.
XG_B0 = -1.4
XG_B_ANGLE = 1.6
XG_B_DIST = -0.09
XG_HEADER_FACTOR = 0.55
XG_PENALTY = 0.76


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def flip(x: float, y: float) -> tuple[float, float]:
    """Convert a location into the opponent's frame of reference."""
    return 100.0 - x, 100.0 - y


def distance_m(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot((x2 - x1) * PITCH_LENGTH_M / 100, (y2 - y1) * PITCH_WIDTH_M / 100)


def in_box(x: float, y: float) -> bool:
    return x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX


def shot_geometry(x: float, y: float) -> tuple[float, float]:
    """Distance (m) to goal centre and the angle (rad) the goal mouth subtends."""
    dx = max((100 - x) * PITCH_LENGTH_M / 100, 0.5)
    dy = abs(y - 50) * PITCH_WIDTH_M / 100
    dist = math.hypot(dx, dy)
    angle = math.atan2(GOAL_WIDTH_M * dx, dx * dx + dy * dy - (GOAL_WIDTH_M / 2) ** 2)
    if angle < 0:
        angle += math.pi
    return dist, angle


def expected_goals(x: float, y: float, *, header: bool = False, penalty: bool = False) -> float:
    if penalty:
        return XG_PENALTY
    dist, angle = shot_geometry(x, y)
    xg = 1 / (1 + math.exp(-(XG_B0 + XG_B_ANGLE * angle + XG_B_DIST * dist)))
    if header:
        xg *= XG_HEADER_FACTOR
    return round(xg, 4)
