"""Saved routes: every recorded route can be kept as a file, so a lost profile edit costs nothing.

Files live in ``profiles/routes/``. A route is archived automatically before it is replaced or wiped
(new recording, deleting a table, loading another route) and can be saved by hand.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .models import Profile, RecordedRoute
from .persistence import _route_from_dict


def _meta(path: Path, data: dict) -> dict:
    route = data.get("route", {})
    return {
        "path": path,
        "label": data.get("label", path.stem),
        "reason": data.get("reason", ""),
        "saved_at": float(data.get("saved_at", path.stat().st_mtime)),
        "waypoints": len(route.get("waypoints", [])),
        "duration": float(route.get("duration_seconds", 0.0)),
        "tables": data.get("tables", {}),
    }


def archive_route(route: RecordedRoute, profile: Profile, folder: Path, reason: str, label: str = "") -> Path | None:
    """Write the route to a new file. Returns None for an empty route (nothing worth keeping)."""
    if not route.waypoints and route.lab_offset is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    names = {table.table_id: table.name for table in profile.tables}
    data = {
        "version": 1,
        "saved_at": now,
        "label": label or time.strftime("Маршрут %d.%m %H:%M", time.localtime(now)),
        "reason": reason,
        "tables": {waypoint.table_id: names.get(waypoint.table_id, waypoint.table_id) for waypoint in route.waypoints},
        "route": asdict(route),
    }
    path = folder / f"route_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}.json"
    counter = 1
    while path.exists():
        path = folder / f"route_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}_{counter}.json"
        counter += 1
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return path


def list_routes(folder: Path) -> list[dict]:
    """Saved routes, newest first."""
    result = []
    for path in folder.glob("route_*.json"):
        try:
            result.append(_meta(path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["saved_at"], reverse=True)


def load_route(path: Path) -> tuple[RecordedRoute, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _route_from_dict(data["route"]), _meta(path, data)


def delete_route(path: Path) -> None:
    path.unlink(missing_ok=True)
