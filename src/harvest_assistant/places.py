"""What each place of the game is called.

On every entry into a place the game writes to Player.log the role of the player there (``Init role: hubber``) and
the kind of server (``...-shooter-...`` or ``...-battle-...``). The role is the code of the place: no screen is
needed to know where the player is. Only the name of a code has to be told once (Settings -> Discord); the
player's own plot is known from the start."""

from __future__ import annotations

import json
import threading
from pathlib import Path

# the names one can give a place (key -> label); the Discord card uses the same keys
CHOICES = {
    "looty_bay": "Лути-Бей",
    "motel_lenny": "Мотель Лени",
    "events_center": "Центр событий",
    "city": "Город",
    "prison": "Тюрьма",
    "hidden": "Не показывать",
}
# known from the game itself:
#   «settler» — the role on a settlement (a plot): hidden, whose and where is nobody's business;
#   «citizen» — the civilian role exists only in Looty Bay (on «battle» and «shooter» servers alike);
#   «hubber»  — Motel Lenny
DEFAULTS = {"settler": "hidden", "citizen": "looty_bay", "hubber": "motel_lenny"}


def place_code(role: str | None, server: str | None = None) -> str | None:
    """The place is told by the role alone: the same role came on «battle» and on «shooter» servers (the server
    is only which machine hosts it)."""
    return role or None


class PlaceNames:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.names: dict[str, str] = dict(DEFAULTS)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.names.update({str(k): str(v) for k, v in data.items() if str(v) in CHOICES})
        except (OSError, ValueError, AttributeError):
            pass

    def get(self, code: str | None) -> str | None:
        with self._lock:
            return self.names.get(code) if code else None

    def set(self, code: str, key: str) -> None:
        if key not in CHOICES:
            raise ValueError(key)
        with self._lock:
            self.names[code] = key
            try:
                self.path.write_text(json.dumps(self.names, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
