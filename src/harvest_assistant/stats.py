"""Farm statistics: the current session, finished sessions and a timestamped event log.

Everything is kept in ``profiles/stats.json`` so the timers/profile stay independent of it.
The event log is what the Statistics tab filters by time (last session, 1/2/3 days, whole wipe).
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

KINDS = ("yellow", "purple", "green")
PRICES = {"green": 4000, "purple": 6000, "yellow": 8000}
WIPE_DAYS = 7  # a server lives seven days, then everything is reset


def money(counts: dict[str, int]) -> int:
    return sum(PRICES[kind] * int(counts.get(kind, 0)) for kind in KINDS)


def _empty_loot() -> dict[str, int]:
    return {kind: 0 for kind in KINDS}


class StatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data: dict = {"version": 1, "wipe_start": None, "session": self._new_session(), "sessions": [], "events": []}
        self._load()

    # ---- persistence --------------------------------------------------------------------

    @staticmethod
    def _new_session() -> dict:
        return {"id": None, "start": None, "active": False, "cycles": 0, "loot": _empty_loot()}

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            for key in ("wipe_start", "session", "sessions", "events"):
                if key in saved:
                    self.data[key] = saved[key]
        session = self.data["session"]
        session["loot"] = {**_empty_loot(), **{k: int(v) for k, v in session.get("loot", {}).items() if k in KINDS}}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

    # ---- session ------------------------------------------------------------------------

    @property
    def session(self) -> dict:
        return self.data["session"]

    def session_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self.session["loot"])

    def session_cycles(self) -> int:
        with self._lock:
            return int(self.session["cycles"])

    def session_active(self) -> bool:
        with self._lock:
            return bool(self.session["active"])

    def _ensure_session_id(self) -> None:
        session = self.session
        if session["id"] is None:
            session["id"] = int(time.time())
            session["start"] = time.time()

    def begin_session(self) -> None:
        """Start (or continue) the session. Counters are never reset by Start."""
        with self._lock:
            self._ensure_session_id()
            self.session["active"] = True
            self._save()

    def end_session(self) -> None:
        """Stop: archive the session and reset the live counters."""
        with self._lock:
            session = self.session
            if session["id"] is not None and (session["cycles"] or any(session["loot"].values())):
                self.data["sessions"].append({**session, "active": False, "end": time.time(), "loot": dict(session["loot"])})
            self.data["session"] = self._new_session()
            self._save()

    def add_cycle(self) -> None:
        with self._lock:
            self._ensure_session_id()
            self.session["cycles"] += 1
            self.data["events"].append({"t": time.time(), "k": "cycle", "s": self.session["id"]})
            self._save()

    def add_reconnect(self) -> None:
        with self._lock:
            self._ensure_session_id()
            self.data["events"].append({"t": time.time(), "k": "reconnect", "s": self.session["id"]})
            self._save()

    def add_loot(self, kind: str, count: int, table: str = "", source: str = "auto") -> None:
        if kind not in KINDS or count <= 0:
            return
        with self._lock:
            self._ensure_session_id()
            self.session["loot"][kind] += int(count)
            self.data["events"].append(
                {"t": time.time(), "k": "loot", "kind": kind, "n": int(count), "table": table, "src": source, "s": self.session["id"]}
            )
            self._save()

    # ---- wipe ---------------------------------------------------------------------------

    @property
    def wipe_start(self) -> float | None:
        return self.data.get("wipe_start")

    def set_wipe_start(self, timestamp: float | None) -> None:
        with self._lock:
            self.data["wipe_start"] = timestamp
            self._save()

    @property
    def wipe_end(self) -> float | None:
        start = self.wipe_start
        return None if start is None else start + WIPE_DAYS * 86400

    @staticmethod
    def parse_date(text: str) -> float | None:
        text = text.strip()
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        return None

    # ---- queries ------------------------------------------------------------------------

    def last_session_id(self) -> int | None:
        with self._lock:
            session = self.session
            if session["id"] is not None:
                return session["id"]
            return self.data["sessions"][-1]["id"] if self.data["sessions"] else None

    def summary(self, since: float | None = None, session_id: int | None = None) -> dict:
        """Totals for events at/after `since`, or for one session."""
        with self._lock:
            cycles, loot, reconnects = 0, _empty_loot(), 0
            for event in self.data["events"]:
                if session_id is not None and event.get("s") != session_id:
                    continue
                if since is not None and event["t"] < since:
                    continue
                if event["k"] == "cycle":
                    cycles += 1
                elif event["k"] == "reconnect":
                    reconnects += 1
                elif event.get("kind") in KINDS:
                    loot[event["kind"]] += int(event["n"])
            return {"cycles": cycles, "loot": loot, "money": money(loot), "reconnects": reconnects}

    def by_day(self, since: float | None = None, session_id: int | None = None) -> list[dict]:
        with self._lock:
            days: dict[str, dict] = defaultdict(lambda: {"cycles": 0, "loot": _empty_loot()})
            for event in self.data["events"]:
                if session_id is not None and event.get("s") != session_id:
                    continue
                if since is not None and event["t"] < since:
                    continue
                day = time.strftime("%Y-%m-%d", time.localtime(event["t"]))
                if event["k"] == "cycle":
                    days[day]["cycles"] += 1
                elif event.get("kind") in KINDS:
                    days[day]["loot"][event["kind"]] += int(event["n"])
            return [{"day": day, "cycles": row["cycles"], "loot": row["loot"], "money": money(row["loot"])}
                    for day, row in sorted(days.items(), reverse=True)]
