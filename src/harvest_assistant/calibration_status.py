"""Readiness of a table for automation.

There is no per-table screen calibration any more: a table is ready as soon as
the recorded route has a waypoint for it.
"""

from __future__ import annotations

from .models import Profile
from .vision import VisionService


def missing_table_calibration(profile: Profile, vision: VisionService, table_id: str) -> list[str]:
    if any(waypoint.table_id == table_id for waypoint in profile.route.waypoints):
        return []
    return ["точка маршрута (F6 у стола при записи)"]


def table_calibration_ready(profile: Profile, vision: VisionService, table_id: str) -> bool:
    return not missing_table_calibration(profile, vision, table_id)
