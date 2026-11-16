"""Auto-reconnect: notice being thrown into the lobby from Player.log, click back into the game.

What the game does when it throws the player out (see the user's screenshots):
  1. a «Ранний доступ» pop-up with a lime «ПОДТВЕРДИТЬ» button,
  2. the main menu with a big lime «RUN» button bottom right,
  3. a loading screen, then the player spawns at the base.

The log tells *when* (``NetworkStep ... WaitStartMainMenuInput`` = lobby, ``... InBattle`` = in a
match, ``Init state:`` = spawned). The buttons are located by their colour, so no templates or
screen calibration are needed.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pydirectinput

from .window_input import WindowMessageInput

PLAYER_LOG = Path.home() / "AppData" / "LocalLow" / "Kefir" / "ROB RUN" / "Player.log"
LOBBY = b'NetworkStep - Changed on "WaitStartMainMenuInput"'
BATTLE = b'NetworkStep - Changed on "InBattle"'
SPAWNED = b"[Info] Init state:"

MAX_ATTEMPTS = 5            # tries per kick
MAX_PER_HOUR = 10           # reconnects per hour before giving up (a loop of kicks is not normal)
MENU_SETTLE_SECONDS = 4.0   # let the menu finish loading before the first click
BUTTON_WAIT_SECONDS = 60.0  # how long to look for a lime button after the lobby appeared
BATTLE_WAIT_SECONDS = 150.0 # loading into the match after pressing RUN
SPAWN_WAIT_SECONDS = 60.0
SETTLE_AFTER_SPAWN = 6.0


class PlayerLog:
    """Follows new lines of Player.log and reports lobby / battle / spawn transitions."""

    def __init__(self, path: Path = PLAYER_LOG) -> None:
        self.path = path
        self._file_id: tuple[int, int] | None = None
        self._offset = 0

    def last_state(self) -> str | None:
        """'lobby' or 'battle' according to the newest marker in the log tail (None if unknown)."""
        try:
            with self.path.open("rb") as log:
                size = os.fstat(log.fileno()).st_size
                log.seek(max(0, size - 700_000))
                tail = log.read()
        except OSError:
            return None
        lobby, battle = tail.rfind(LOBBY), tail.rfind(BATTLE)
        if lobby < 0 and battle < 0:
            return None
        return "lobby" if lobby > battle else "battle"

    def read_new(self) -> list[str]:
        try:
            with self.path.open("rb") as log:
                stat = os.fstat(log.fileno())
                file_id = (stat.st_dev, stat.st_ino)
                if self._file_id is None:  # first call: start at the end, old events are history
                    self._file_id, self._offset = file_id, stat.st_size
                    return []
                if file_id != self._file_id or stat.st_size < self._offset:  # the game restarted: new log
                    self._file_id, self._offset = file_id, 0
                log.seek(self._offset)
                chunk = log.read(512 * 1024)
        except OSError:
            return []
        complete, newline, _rest = chunk.rpartition(b"\n")
        if not newline:
            return []
        self._offset += len(complete) + 1
        events = []
        for line in complete.split(b"\n"):
            if LOBBY in line:
                events.append("lobby")
            elif BATTLE in line:
                events.append("battle")
            elif line.startswith(SPAWNED):
                events.append("spawned")
        return events


@dataclass(frozen=True)
class Button:
    kind: str  # "confirm" (pop-up) or "run" (main menu)
    x: int
    y: int


def find_buttons(frame: np.ndarray) -> list[Button]:
    """Large lime rectangles: the pop-up's «ПОДТВЕРДИТЬ» and the menu's «RUN».

    Thin lime lines (the loading bar) and small HUD marks are ignored by the size filter.
    """
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    mask = ((hsv[..., 0] >= 28) & (hsv[..., 0] <= 45) & (hsv[..., 1] > 140) & (hsv[..., 2] > 190)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, _labels, stats, _centers = cv2.connectedComponentsWithStats(mask)
    scale = width / 1280  # the reference screenshots are 1280 wide
    found: list[Button] = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if not (24 * scale <= h <= 110 * scale and 120 * scale <= w <= 420 * scale and area >= 2500 * scale * scale):
            continue
        cx, cy = int(x + w / 2), int(y + h / 2)
        if cx > 0.7 * width and cy > 0.8 * height:
            found.append(Button("run", cx, cy))
        elif 0.35 * width < cx < 0.8 * width and 0.45 * height < cy < 0.7 * height:
            found.append(Button("confirm", cx, cy))
    return found


def click(x: int, y: int) -> None:
    pydirectinput.moveTo(int(x), int(y))
    time.sleep(0.25)
    pydirectinput.mouseDown()
    time.sleep(0.09)
    pydirectinput.mouseUp()


class Reconnector(threading.Thread):
    """Background watcher. ``on_event(kind, text)`` kinds: lobby, status, reconnected, failed."""

    def __init__(self, get_screen: Callable[[], object | None], is_enabled: Callable[[], bool],
                 on_event: Callable[[str, str], None], log: PlayerLog | None = None) -> None:
        super().__init__(name="auto-reconnect", daemon=True)
        self._get_screen = get_screen
        self._is_enabled = is_enabled
        self._on_event = on_event
        self._log = log or PlayerLog()
        self.stop_event = threading.Event()
        self._recent: list[float] = []
        self._pending: list[str] = []

    # ---- helpers ------------------------------------------------------------------------

    def _emit(self, kind: str, text: str = "") -> None:
        try:
            self._on_event(kind, text)
        except Exception:
            pass

    def _poll(self) -> list[str]:
        events = self._log.read_new()
        self._pending.extend(events)
        return events

    def _wait_for(self, wanted: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self.stop_event.is_set():
            self._poll()
            while self._pending:
                event = self._pending.pop(0)
                if event == wanted:
                    return True
                if event == "lobby" and wanted != "lobby":
                    self._pending.insert(0, "lobby")  # thrown out again: the caller must start over
                    return False
            self.stop_event.wait(1.0)
        return False

    def _capture(self):
        screen = self._get_screen()
        if screen is None:
            raise RuntimeError("окно игры не привязано")
        if not screen.background_control:
            screen.focus_target()
        return screen.capture()

    def _click(self, x: int, y: int) -> None:
        screen = self._get_screen()
        if screen is not None and screen.background_control:
            WindowMessageInput(screen.target_window_handle).click(int(x), int(y), "left")
        else:
            click(x, y)

    # ---- one attempt --------------------------------------------------------------------

    def _try_once(self) -> bool:
        self.stop_event.wait(MENU_SETTLE_SECONDS)
        deadline = time.monotonic() + BUTTON_WAIT_SECONDS
        pressed_run = False
        while time.monotonic() < deadline and not self.stop_event.is_set() and self._is_enabled():
            self._poll()
            if "battle" in self._pending:  # got in on its own (e.g. the user pressed RUN)
                self._pending.remove("battle")
                return self._after_battle()
            try:
                buttons = find_buttons(self._capture())
            except Exception as exc:
                self._emit("status", f"Реконнект: жду окно игры ({exc})")
                self.stop_event.wait(2.0)
                continue
            run = next((b for b in buttons if b.kind == "run"), None)
            confirm = next((b for b in buttons if b.kind == "confirm"), None)
            if confirm is not None:
                self._emit("status", "Реконнект: подтверждаю окно «Ранний доступ»")
                self._click(confirm.x, confirm.y)
                self.stop_event.wait(1.8)
                continue
            if run is not None:
                self._emit("status", "Реконнект: нажимаю RUN")
                self._click(run.x, run.y)
                pressed_run = True
                break
            self.stop_event.wait(1.5)
        if not pressed_run:
            return False
        self._emit("status", "Реконнект: загрузка в игру…")
        if not self._wait_for("battle", BATTLE_WAIT_SECONDS):
            return False
        return self._after_battle()

    def _after_battle(self) -> bool:
        self._emit("status", "Реконнект: вхожу в игру, жду появления персонажа…")
        self._wait_for("spawned", SPAWN_WAIT_SECONDS)
        self.stop_event.wait(SETTLE_AFTER_SPAWN)
        return not self.stop_event.is_set()

    def _reconnect(self) -> None:
        now = time.time()
        self._recent = [t for t in self._recent if now - t < 3600]
        if len(self._recent) >= MAX_PER_HOUR:
            self._emit("failed", f"Реконнект остановлен: уже {MAX_PER_HOUR} раз за час. Проверь, что происходит.")
            return
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.stop_event.is_set() or not self._is_enabled():
                return
            self._emit("status", f"Реконнект: попытка {attempt} из {MAX_ATTEMPTS}")
            if self._try_once():
                self._recent.append(time.time())
                self._emit("reconnected", "Вернулся в игру.")
                return
            if "lobby" not in self._pending:
                self.stop_event.wait(20.0)
        self._emit("failed", f"Не удалось вернуться за {MAX_ATTEMPTS} попыток. Зайди в игру вручную.")

    # ---- main loop ----------------------------------------------------------------------

    def run(self) -> None:
        in_lobby = self._log.last_state() == "lobby"
        handled = False
        if in_lobby:
            self._emit("lobby")  # already in the lobby when the app started: the route must not run
        while not self.stop_event.is_set():
            for event in self._poll():
                if event == "lobby":
                    in_lobby, handled = True, False
                    self._emit("lobby")
                elif event in ("battle", "spawned"):
                    in_lobby = False
            self._pending = [e for e in self._pending if e == "lobby"]
            if in_lobby and not handled and self._is_enabled():
                handled = True
                self._pending.clear()
                self._reconnect()
                in_lobby = self._log.last_state() == "lobby" and "battle" not in self._pending
            self.stop_event.wait(1.0)

    def stop(self) -> None:
        self.stop_event.set()


def describe_screen(frame: np.ndarray) -> str:
    """For the «check on screen» button: what the reconnect would click right now (nothing is clicked)."""
    buttons = find_buttons(frame)
    names = {"confirm": "окно «Ранний доступ» (кнопка «Подтвердить»)", "run": "главное меню (кнопка RUN)"}
    if not buttons:
        return "Кнопок входа на экране нет: игра сейчас не в лобби."
    return "Вижу: " + ", ".join(f"{names[b.kind]} в точке {b.x}, {b.y}" for b in buttons)
