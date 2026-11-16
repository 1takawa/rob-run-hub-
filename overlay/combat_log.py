"""Read new ROB RUN combat events from the Unity player log."""

from __future__ import annotations

import os
import re
from pathlib import Path


PLAYER_LOG = Path.home() / "AppData" / "LocalLow" / "Kefir" / "ROB RUN" / "Player.log"
READ_LIMIT = 64 * 1024
STATE_CHANGE = re.compile(rb"\[Info\] Change state: (\w+) -> (\w+)")
COMBAT_STATES = {
    b"KnockedOut": "knocks",
    b"Dead": "deaths",
    b"ArrestFinished": "arrests",
}


def combat_event(line: bytes) -> str | None:
    match = STATE_CHANGE.fullmatch(line.rstrip(b"\r"))
    if match is None:
        return None
    previous, current = match.groups()
    if previous == current:
        return None
    return COMBAT_STATES.get(current)


class CombatLog:
    def __init__(self, path: Path = PLAYER_LOG) -> None:
        self.path = path
        self._file_id: tuple[int, int] | None = None
        self._offset = 0
        self._skip_line = False

    def read_new_events(self) -> list[str]:
        with self.path.open("rb") as log:
            stat = os.fstat(log.fileno())
            file_id = (stat.st_dev, stat.st_ino)
            if self._file_id is None:
                # Older entries may already be in the session's saved counters.
                self._file_id = file_id
                self._offset = stat.st_size
                if self._offset:
                    log.seek(self._offset - 1)
                    self._skip_line = log.read(1) != b"\n"
                return []
            if file_id != self._file_id or stat.st_size < self._offset:
                self._file_id = file_id
                self._offset = 0
                self._skip_line = False

            log.seek(self._offset)
            chunk = log.read(READ_LIMIT)

        complete, newline, _ = chunk.rpartition(b"\n")
        if not newline:
            if len(chunk) == READ_LIMIT:
                self._offset += len(chunk)
                self._skip_line = True
            return []

        # Leave a partially written line for the next poll.
        self._offset += len(complete) + 1
        lines = complete.split(b"\n")
        if self._skip_line:
            lines = lines[1:]
            self._skip_line = False
        return [event for line in lines if (event := combat_event(line)) is not None]
