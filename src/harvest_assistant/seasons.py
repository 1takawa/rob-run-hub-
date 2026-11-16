"""Seasons: one state (server) of the game from its start to its wipe, seven days later (or earlier, by hand).

A season is entered by hand: «ШТАТ EU-06242», «ROCKY VALLEY», when the server started. Only one is current: the
character lives in one state at a time. What a season sums up:
  * packages and their money: the loot events of stats.json within the season;
  * cop salary: banked by game_watch when a shift ends (the blue counter top right goes away);
  * time in the game: counted by game_watch while the game is in a match (not in the main menu);
  * knocks, deaths, arrests (the player was arrested): the overlay's sessions that began within the season;
  * kills and the prison term: entered from the court sentence shown at the wipe.

Kept in profiles/seasons.json (personal data, never in git).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .stats import KINDS, PRICES

WIPE_DAYS = 7
WIPE_SECONDS = WIPE_DAYS * 86400


ROLES = ("civilian", "police", "criminal")   # the time in each role is counted per season


def new_season(code: str, name: str, started_at: float, *, auto_start: bool = False) -> dict:
    return {
        "id": int(time.time() * 1000), "code": code.strip().upper(), "name": name.strip().upper(),
        "started_at": float(started_at), "ended_at": None, "early": False, "auto_start": auto_start,
        "kills": None, "years": None, "playtime": 0.0, "salary": 0, "shifts": 0,
    }


def normalize_code(text: str) -> str:
    """«06242», «eu-06242», «EU 06242» -> «EU-06242»."""
    digits = "".join(char for char in text if char.isdigit())
    return f"EU-{digits}" if digits else text.strip().upper()


class SeasonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data: dict = {"version": 1, "current": None, "seasons": []}
        self._dirty_since: float | None = None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and isinstance(saved.get("seasons"), list):
                self.data.update(saved)
        except (OSError, ValueError):
            pass

    # ---- persistence ----------------------------------------------------------------------------------------------

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
                temporary.replace(self.path)
                self._dirty_since = None
            except OSError:
                pass

    def _save_soon(self) -> None:
        """Small counters (playtime) are saved at most every half minute."""
        now = time.monotonic()
        if self._dirty_since is None:
            self._dirty_since = now
        elif now - self._dirty_since > 30:
            self.save()

    # ---- seasons --------------------------------------------------------------------------------------------------

    def current(self) -> dict | None:
        with self._lock:
            wanted = self.data.get("current")
            return next((s for s in self.data["seasons"] if s["id"] == wanted), None)

    def get(self, season_id: int | None) -> dict | None:
        with self._lock:
            return next((s for s in self.data["seasons"] if s["id"] == season_id), None)

    def history(self) -> list[dict]:
        """Every season, newest first."""
        with self._lock:
            return sorted(self.data["seasons"], key=lambda s: s["started_at"], reverse=True)

    def start(self, code: str, name: str, started_at: float, *, auto_start: bool = False) -> dict:
        """A new state: the current one ends now (early when its seven days are not over)."""
        with self._lock:
            self._close_current(time.time())
            season = new_season(normalize_code(code), name, started_at, auto_start=auto_start)
            taken = {s["id"] for s in self.data["seasons"]}
            while season["id"] in taken:     # the id is the time in ms: two states made in one ms must not share it
                season["id"] += 1
            self.data["seasons"].append(season)
            self.data["current"] = season["id"]
            self.save()
            return season

    def _close_current(self, now: float) -> None:
        season = self.current()
        if season is not None and season.get("ended_at") is None:
            wipe = season["started_at"] + WIPE_SECONDS
            season["ended_at"] = min(now, wipe)
            season["early"] = now < wipe - 3600
        self.data["current"] = None

    def roll_over(self, now: float | None = None) -> None:
        """Seven days after its start a season is over by itself (the server is wiped)."""
        now = time.time() if now is None else now
        with self._lock:
            season = self.current()
            if season is not None and now >= season["started_at"] + WIPE_SECONDS:
                season["ended_at"] = season["started_at"] + WIPE_SECONDS
                season["early"] = False
                self.data["current"] = None
                self.save()

    def update(self, season_id: int, **fields) -> None:
        with self._lock:
            season = self.get(season_id)
            if season is None:
                return
            if "code" in fields:
                fields["code"] = normalize_code(fields["code"])
            if "name" in fields:
                fields["name"] = str(fields["name"]).strip().upper()
            season.update(fields)
            self.save()

    def correct_start(self, estimate: float) -> bool:
        """The main menu says how long the server has existed. A start that was not typed by hand follows it."""
        with self._lock:
            season = self.current()
            if season is None or not season.get("auto_start") or abs(season["started_at"] - estimate) < 120:
                return False
            season["started_at"] = float(estimate)
            self.save()
            return True

    # ---- counters (from game_watch) ---------------------------------------------------------------------------------

    def add_playtime(self, seconds: float) -> None:
        with self._lock:
            season = self.current()
            if season is not None and seconds > 0:
                season["playtime"] = float(season.get("playtime", 0.0)) + seconds
                self._save_soon()

    def add_role_time(self, role: str, seconds: float) -> None:
        """Time as a civilian / criminal / police (the role comes from Player.log)."""
        with self._lock:
            season = self.current()
            if season is not None and seconds > 0 and role in ROLES:
                roles = season.setdefault("roles", {})
                roles[role] = float(roles.get(role, 0.0)) + seconds
                self._save_soon()

    def add_salary(self, amount: int, *, new_shift: bool) -> None:
        with self._lock:
            season = self.current()
            if season is not None and amount > 0:
                season["salary"] = int(season.get("salary", 0)) + int(amount)
                if new_shift:
                    season["shifts"] = int(season.get("shifts", 0)) + 1
                self.save()

    # ---- the numbers of a season --------------------------------------------------------------------------------------

    @staticmethod
    def summary(season: dict, loot_events: list[dict], combat_sessions: list[dict], now: float | None = None) -> dict:
        now = time.time() if now is None else now
        start = float(season["started_at"])
        end = float(season["ended_at"]) if season.get("ended_at") else now
        counts = {kind: 0 for kind in KINDS}
        for event in loot_events:
            if event.get("k") == "loot" and start <= float(event.get("t", 0)) <= end and event.get("kind") in counts:
                counts[event["kind"]] += int(event.get("n", 0))
        packages = {kind: counts[kind] * PRICES[kind] for kind in KINDS}
        salary = int(season.get("salary", 0))
        total = sum(packages.values()) + salary
        combat = {"knocks": 0, "deaths": 0, "arrests": 0}
        for record in combat_sessions:
            began = float(record.get("tracking_started_at") or record.get("started_at") or 0)
            if start <= began <= end:
                for key in combat:
                    combat[key] += int(record.get(key, 0) or 0)
        playtime = float(season.get("playtime", 0.0))
        fixed = season.get("fixed") or {}
        if fixed:   # a season from before the statistics: its numbers were taken from the sentence and the old logs
            total = int(fixed.get("total", total))
            for key in combat:
                combat[key] = int(fixed.get(key, combat[key]))
        wipe_at = start + WIPE_SECONDS
        return {
            "counts": counts, "packages": packages, "salary": salary, "shifts": int(season.get("shifts", 0)),
            "total": total, "per_hour": int(total / (playtime / 3600)) if playtime >= 600 else None,
            "playtime": playtime, **combat,
            "roles": {role: float((season.get("roles") or {}).get(role, 0.0)) for role in ROLES},
            "kd": round(combat["knocks"] / combat["deaths"], 1) if combat["deaths"] else (float(combat["knocks"]) if combat["knocks"] else None),
            "kills": season.get("kills"), "years": season.get("years"),
            "wipe_at": wipe_at, "left": max(0.0, wipe_at - now) if not season.get("ended_at") else 0.0,
            "progress": min(1.0, max(0.0, (min(end, wipe_at) - start) / WIPE_SECONDS)),
        }
