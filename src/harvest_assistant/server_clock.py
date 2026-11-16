"""«Уточнить старт сервера»: how long the server has lived, read in the main menu, gives the start of the wipe.

The steps, each told on the screen of the app:
  1. the game is not running: start it (the path is in the game's own Player.log) and wait for the main menu;
  2. the game is running: bring its window up;
  3. where the player is comes from Player.log: in a match -> Esc, «Выйти», «В главное меню» (the buttons are found by
     their text), and wait until the log says the main menu is up;
  4. in the main menu: read «ВРЕМЯ СУЩЕСТВОВАНИЯ СЕРВЕРА 22Ч 49М» over RUN (several readings must agree);
  5. the start of the current state = now - that time; then RUN takes the player back into the game.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .game_watch import _Ocr, read_server_age
from .reconnect import PLAYER_LOG, PlayerLog, find_buttons

logger = logging.getLogger(__name__)


def game_exe(log_path: Path = PLAYER_LOG) -> Path | None:
    """The game's .exe, from a path Player.log mentions (…/ROB RUN_Data/…)."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return None
    match = re.search(r"([A-Za-z]:[/\\][^\n\r'\"]*?)[/\\]([^/\\\n\r]+)_Data[/\\]", text)
    if not match:
        return None
    exe = Path(match.group(1)) / f"{match.group(2)}.exe"
    return exe if exe.is_file() else None


class ServerClock(threading.Thread):
    def __init__(self, get_screen: Callable[[], object | None], make_driver: Callable[[], object], seasons,
                 on_status: Callable[[str], None], on_done: Callable[[bool, str], None], *, back_to_game: bool = True) -> None:
        super().__init__(name="server-clock", daemon=True)
        self.get_screen, self.make_driver, self.seasons = get_screen, make_driver, seasons
        self.on_status, self.on_done = on_status, on_done
        self.back_to_game = back_to_game
        self.ocr = _Ocr()
        self.log = PlayerLog()
        self.stop_event = threading.Event()

    # ---- helpers ------------------------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        logger.info("server clock: %s", text)
        self.on_status(text)

    def _wait(self, seconds: float) -> bool:
        return not self.stop_event.wait(seconds)

    def _frame(self) -> np.ndarray | None:
        screen = self.get_screen()
        try:
            return np.asarray(screen.capture()) if screen is not None else None
        except Exception:
            return None

    def _find_text(self, frame: np.ndarray, words: tuple[str, ...]) -> tuple[int, int] | None:
        """The centre of the first line of text on the screen that contains one of these words."""
        from .autocop import AutocopScreenReader

        import asyncio
        import cv2

        reader = AutocopScreenReader()
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        if not ok:
            return None
        lines = asyncio.run(reader._ocr(encoded.tobytes()))
        for text, (left, top, width, height) in lines:
            norm = AutocopScreenReader._normalize(text)
            if any(word in norm for word in words):
                return int(left + width / 2), int(top + height / 2)
        return None

    def _click_text(self, driver, words: tuple[str, ...], tries: int = 6) -> bool:
        for _ in range(tries):
            frame = self._frame()
            spot = self._find_text(frame, words) if frame is not None else None
            if spot is not None:
                driver.click_at(spot[0], spot[1], "left")
                return True
            if not self._wait(0.7):
                return False
        return False

    def _wait_for_state(self, wanted: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.log.last_state() == wanted:
                return True
            if not self._wait(1.0):
                return False
        return False

    # ---- the steps ------------------------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            ok, text = self._run()
        except Exception as exc:
            logger.exception("server clock failed")
            ok, text = False, f"Не получилось: {exc}"
        self.on_done(ok, text)

    def _run(self) -> tuple[bool, str]:
        screen = self.get_screen()
        if screen is None:
            return False, "Сначала привяжи окно игры (Ctrl+F10 в игре)."
        if not screen.is_target_open():
            exe = game_exe()
            if exe is None:
                return False, "Игра не запущена, а путь к ней неизвестен: запусти её один раз сам."
            self._say("Запускаю игру…")
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
            deadline = time.monotonic() + 180
            while not screen.is_target_open():
                if time.monotonic() > deadline or not self._wait(2.0):
                    return False, "Игра не открылась за 3 минуты."
            self._say("Жду главное меню…")
            if not self._wait_for_state("lobby", 150):
                return False, "Игра открылась, но главное меню не дождался."
        self._say("Разворачиваю игру…")
        screen.focus_target(settle_seconds=0.6)
        driver = self.make_driver()
        state = self.log.last_state()
        if state == "battle":
            self._say("Ты в игре: выхожу в главное меню (Esc → Выйти → В главное меню)…")
            driver.play_key("esc", 0.08)
            if not self._wait(1.2):
                return False, "Остановлено."
            if not self._click_text(driver, ("выйти",)):
                return False, "Не нашёл кнопку «Выйти» в меню паузы."
            if not self._wait(1.0):
                return False, "Остановлено."
            if not self._click_text(driver, ("главноеменю", "вглавное")):
                return False, "Не нашёл кнопку «В главное меню»."
            self._wait(1.0)
            self._click_text(driver, ("подтвердить", "да", "выйти"), tries=2)   # a confirmation, if the game asks
            if not self._wait_for_state("lobby", 90):
                return False, "Не дождался главного меню."
        self._say("Читаю время жизни сервера над кнопкой RUN…")
        self._wait(4.0)   # the menu finishes loading
        ages: list[float] = []
        for _ in range(12):
            frame = self._frame()
            age = read_server_age(frame, self.ocr) if frame is not None else None
            if age is not None:
                ages.append(age)
                if len(ages) >= 2 and abs(ages[-1] - ages[-2]) <= 180:
                    break
            if not self._wait(1.5):
                return False, "Остановлено."
        if not ages:
            return False, "Не смог прочитать «Время существования сервера» в главном меню."
        age = ages[-1]
        started = time.time() - age
        season = self.seasons.current()
        if season is not None:
            self.seasons.update(season["id"], started_at=started, auto_start=True, clock_checked=True)
        self.seasons.data["last_server_start"] = started
        self.seasons.save()
        hours, minutes = int(age // 3600), int(age % 3600 // 60)
        text = f"Сервер живёт {hours} ч {minutes:02d} м: старт {time.strftime('%d.%m %H:%M', time.localtime(started))}."
        if season is None:
            text += " Штат не заведён — время запомнено для нового штата."
        if self.back_to_game:
            for _ in range(5):
                frame = self._frame()
                run = next((b for b in find_buttons(frame) if b.kind == "run"), None) if frame is not None else None
                if run is not None:
                    driver.click_at(run.x, run.y, "left")
                    text += " Возвращаюсь в игру."
                    break
                if not self._wait(1.0):
                    break
        return True, text
