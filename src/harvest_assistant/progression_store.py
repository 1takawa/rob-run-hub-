"""Local season-specific persistence for user-authored progression goals."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from .rob_run_tools import normalize_goal


class ProgressionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data: dict = {"version": 1, "plans": {}}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and isinstance(saved.get("plans"), dict):
                for season_key, raw_rows in saved["plans"].items():
                    if not isinstance(raw_rows, list):
                        continue
                    rows = []
                    for raw in raw_rows:
                        if not isinstance(raw, dict):
                            continue
                        try:
                            row = normalize_goal(raw)
                            row["id"] = row["id"] or str(uuid.uuid4())
                            rows.append(row)
                        except ValueError:
                            continue
                    self.data["plans"][str(season_key)] = rows
        except (OSError, ValueError):
            pass

    @staticmethod
    def _key(season_id: int | None) -> str:
        return str(int(season_id)) if season_id is not None else "unassigned"

    def rows_for_season(self, season_id: int | None) -> list[dict]:
        with self._lock:
            rows = self.data["plans"].get(self._key(season_id), [])
            return [dict(row) for row in rows if isinstance(row, dict)]

    def add(self, season_id: int | None, row: dict) -> dict:
        with self._lock:
            normalized = normalize_goal(row)
            normalized["id"] = str(uuid.uuid4())
            self.data["plans"].setdefault(self._key(season_id), []).append(normalized)
            self.save()
            return dict(normalized)

    def update(self, season_id: int | None, row_id: str, fields: dict) -> dict | None:
        with self._lock:
            rows = self.data["plans"].get(self._key(season_id), [])
            for index, row in enumerate(rows):
                if isinstance(row, dict) and row.get("id") == row_id:
                    updated = normalize_goal({**row, **fields, "id": row_id})
                    rows[index] = updated
                    self.save()
                    return dict(updated)
        return None

    def remove(self, season_id: int | None, row_id: str) -> bool:
        with self._lock:
            key = self._key(season_id)
            rows = self.data["plans"].get(key, [])
            retained = [row for row in rows if not isinstance(row, dict) or row.get("id") != row_id]
            if len(retained) == len(rows):
                return False
            self.data["plans"][key] = retained
            self.save()
            return True

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                pass
