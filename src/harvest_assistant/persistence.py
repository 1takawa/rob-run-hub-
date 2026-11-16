"""JSON persistence for profiles and screen-template files."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import (
    AUTOMATION_MODES,
    InteractionCalibration,
    LaboratoryProfile,
    Profile,
    RecordedRoute,
    RouteEvent,
    ScreenMarker,
    TableProfile,
    Waypoint,
    WaypointCalibration,
)


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(asdict(profile), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def load_profile(path: Path) -> Profile:
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = profile_from_dict(data)
    # Game-open time is only tracked while this helper is running. Do not count
    # the period between app launches as observed game-open time.
    profile.last_clock_update_utc = None
    return profile


def profile_from_dict(data: dict[str, Any]) -> Profile:
    tables = [TableProfile(**_table_from_dict(item)) for item in data.get("tables", [])]
    route = _route_from_dict(data.get("route", {}))
    calibrations = {
        table_id: WaypointCalibration(**_calibration_from_dict(item))
        for table_id, item in data.get("waypoint_calibrations", {}).items()
    }
    interaction = InteractionCalibration(**data.get("interaction", {}))
    lab_data = data.get("laboratory", {})
    lab = LaboratoryProfile(
        prepare=str(lab_data.get("prepare", "")),
        actions=str(lab_data.get("actions", "")),
        finish=str(lab_data.get("finish", "")),
        station=_marker_from_dict(lab_data.get("station", {})),
        ready=_marker_from_dict(lab_data.get("ready", {})),
        running=_marker_from_dict(lab_data.get("running", {})),
    )
    if "timer" in lab_data:
        lab.timer = TableProfile(**_table_from_dict(lab_data["timer"]))
    mode = data.get("automation_mode", "plantation")
    if mode not in dict(AUTOMATION_MODES):
        mode = "plantation"
    return Profile(
        name=str(data.get("name", "My room")),
        target_window_title_match=str(data.get("target_window_title_match", "")),
        background_control=bool(data.get("background_control", False)),
        clock_mode=data.get("clock_mode", "wall_clock"),
        manual_timer_paused=bool(data.get("manual_timer_paused", False)),
        manual_timer_pause_started_utc=data.get("manual_timer_pause_started_utc"),
        last_clock_update_utc=data.get("last_clock_update_utc"),
        tables=tables,
        route=route,
        automation_mode=mode,
        waiting_route=_route_from_dict(data.get("waiting_route", {})),
        start_marker=_marker_from_dict(data.get("start_marker", {})),
        laboratory=lab,
        waypoint_calibrations=calibrations,
        interaction=interaction,
        record_route_hotkey=str(data.get("record_route_hotkey", "f5")),
        mark_waypoint_hotkey=str(data.get("mark_waypoint_hotkey", "f6")),
        pause_hotkey=str(data.get("pause_hotkey", "f8")),
        stop_hotkey=str(data.get("stop_hotkey", "f9")),
        loot_hotkey=str(data.get("loot_hotkey", "f7")),
        auto_hotkey=str(data.get("auto_hotkey", "f4")),
        visual_hotkey=str(data.get("visual_hotkey", "f3")),
        approach_hotkey=str(data.get("approach_hotkey", "f2")),
        autocop_hotkey=str(data.get("autocop_hotkey", "f10")),
        autocop_class=str(data.get("autocop_class", "Патрульный")),
        discord_enabled=bool(data.get("discord_enabled", True)),
        discord_show_server=bool(data.get("discord_show_server", True)),
        discord_show_role=bool(data.get("discord_show_role", True)),
        discord_show_party=bool(data.get("discord_show_party", True)),
        discord_show_earnings=bool(data.get("discord_show_earnings", True)),
        discord_show_hours=bool(data.get("discord_show_hours", True)),
        approach_route=_route_from_dict(data.get("approach_route", {})),
        auto_collect=bool(data.get("auto_collect", False)),
        auto_reconnect=bool(data.get("auto_reconnect", False)),
        table_vision=bool(data.get("table_vision", True)),
        vision_harvest=bool(data.get("vision_harvest", True)),
        interval_migrated=bool(data.get("interval_migrated", False)),
        loot_totals={str(k): int(v) for k, v in data.get("loot_totals", {}).items()},
        last_loot=list(data.get("last_loot", [])),
        harvest_count=int(data.get("harvest_count", 0)),
        match_threshold=float(data.get("match_threshold", 0.84)),
    )


def _table_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result["screen_regions"] = {
        key: tuple(value) for key, value in result.get("screen_regions", {}).items()
    }
    return result


def _route_from_dict(data: dict[str, Any]) -> RecordedRoute:
    events = []
    for raw_event in data.get("events", []):
        event = dict(raw_event)
        if event.get("kind") == "mouse_delta" and isinstance(event.get("value"), list):
            event["value"] = tuple(event["value"])
        events.append(RouteEvent(**event))
    waypoints = [Waypoint(**_waypoint_from_dict(item)) for item in data.get("waypoints", [])]
    return RecordedRoute(
        events=events,
        waypoints=waypoints,
        start_anchor_template=str(data.get("start_anchor_template", "")),
        end_anchor_template=str(data.get("end_anchor_template", "")),
        duration_seconds=float(data.get("duration_seconds", 0.0)),
        start_screen_region=_optional_region(data.get("start_screen_region")),
        end_screen_region=_optional_region(data.get("end_screen_region")),
        gameplay_guard_template=str(data.get("gameplay_guard_template", "")),
        gameplay_guard_region=_optional_region(data.get("gameplay_guard_region")),
        start_capture_template=str(data.get("start_capture_template", "")),
        end_capture_template=str(data.get("end_capture_template", "")),
        lab_offset=float(data["lab_offset"]) if data.get("lab_offset") is not None else None,
    )


def _marker_from_dict(data: dict[str, Any]) -> ScreenMarker:
    return ScreenMarker(str(data.get("template", "")), _optional_region(data.get("region")))


def _waypoint_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result["screen_region"] = tuple(result.get("screen_region", (0, 0, 1, 1)))
    result["capture_template"] = str(result.get("capture_template", ""))
    return result


def _calibration_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for key in ("anchor_region", "icon_region", "prompt_region", "progress_region"):
        result[key] = _optional_region(result.get(key))
    return result


def _optional_region(value: Any) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    return tuple(int(part) for part in value)  # type: ignore[return-value]
