"""Watches the game in the background for the season statistics.

* Time in the game: while the game window exists and Player.log says the player is in a match (not in the main menu).
  The log is the game's own word, so this works with the game in the background too.
* Cop salary: on duty a counter with a blue shield shows top right (loadout_vision.duty_badge_box). The number left of
  the shield is read while it is there; when the shield has been gone for a while (the shift was ended at the counter,
  or the game threw the player out to the map because others want to play cop, or the game was left) the last number
  read is banked as salary. A number that comes back no smaller continues the same shift: only the growth is banked.
* Server age: the main menu says «ВРЕМЯ СУЩЕСТВОВАНИЯ СЕРВЕРА 22Ч 49М»: a season whose start was not typed by hand
  follows it (the wipe is seven days after the start).

The game's narrow font is hard for the Windows text recognition, so every reading is taken several ways and only a
number that agrees is used; a number that cannot be read is simply not used.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import Counter
from typing import Callable

import cv2
import numpy as np

from .loadout_vision import duty_badge_box
from .reconnect import PlayerLog
from .seasons import SeasonStore

logger = logging.getLogger(__name__)

TICK = 2.0             # seconds between looks
GONE_LOOKS = 4         # looks without the shield before a shift counts as ended
MENU_EVERY = 30.0      # seconds between readings of the server age in the main menu
PRESENCE_HUD_EVERY = 8.0  # role and party are read from the small, fixed HUD labels


class _Ocr:
    def __init__(self) -> None:
        self._engine = None

    def lines(self, image: np.ndarray) -> list[str]:
        """Text lines of a picture (BGR or grey)."""
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        if self._engine is None:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            self._engine = OcrEngine.try_create_from_language(Language("ru")) or OcrEngine.try_create_from_user_profile_languages()
        ok, encoded = cv2.imencode(".png", image)
        if not ok or self._engine is None:
            return []

        async def run() -> list[str]:
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(encoded.tobytes())
            await writer.store_async()
            await writer.flush_async()
            stream.seek(0)
            bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
            result = await self._engine.recognize_async(bitmap)
            return [line.text for line in result.lines]

        return asyncio.run(run())


def _variants(grey: np.ndarray) -> list[np.ndarray]:
    """The same text, stretched and thresholded in a few ways (the game font is narrow)."""
    found = []
    for stretch, scale in ((1.4, 3), (1.8, 3), (1.8, 4), (2.2, 2)):
        big = cv2.resize(grey, None, fx=scale * stretch, fy=scale, interpolation=cv2.INTER_CUBIC)
        found.append(cv2.copyMakeBorder(big, 30, 30, 30, 30, cv2.BORDER_REPLICATE))
        found.append(cv2.copyMakeBorder(np.where(big > 140, 0, 255).astype(np.uint8), 30, 30, 30, 30,
                                        cv2.BORDER_CONSTANT, value=255))
    return found


def parse_server_age(texts: list[str]) -> float | None:
    """Seconds the server has existed, from readings like «… СЕРВЕРА 22Ч 49М» (days «1Д» too). Each part is taken
    only when at least two readings agree on it; minutes that do not agree count as zero (an hour of error is fine
    for a seven-day timer)."""
    days, hours, minutes = Counter(), Counter(), Counter()
    for text in texts:
        upper = text.upper().replace("O", "0")
        if (match := re.search(r"(\d{1,2})\s*[ДD]", upper)):
            days[int(match.group(1))] += 1
        if (match := re.search(r"(\d{1,2})\s*[ЧH4](?![А-ЯA-Z])", upper)):
            hours[int(match.group(1))] += 1
        if (match := re.search(r"[ЧH]\s*(\d{1,2})\s*[МM]", upper)):
            minutes[int(match.group(1))] += 1

    def agreed(counter: Counter, top: int) -> int | None:
        if not counter:
            return None
        value, votes = counter.most_common(1)[0]
        return value if votes >= 2 and value < top else None

    h = agreed(hours, 24)
    if h is None:
        return None
    d = agreed(days, 8) or 0
    m = agreed(minutes, 60) or 0
    return float(d * 86400 + h * 3600 + m * 60)


def read_server_age(frame_rgb: np.ndarray, ocr: _Ocr) -> float | None:
    height, width = frame_rgb.shape[:2]
    line = frame_rgb[int(height * 0.79):int(height * 0.835), int(width * 0.80):int(width * 0.995)].max(axis=2)
    texts = []
    for image in _variants(line):
        texts += ocr.lines(image)
    return parse_server_age([t for t in texts if "СЕРВЕР" in t.upper() or re.search(r"\d", t)])


def read_duty_counter(frame_rgb: np.ndarray, ocr: _Ocr) -> tuple[bool, int | None]:
    """(is the shield there, the number left of it or None when it does not read)."""
    box = duty_badge_box(frame_rgb)
    if box is None:
        return False, None
    x, y, w, h = box
    left = max(0, x - int(7 * h))
    digits = frame_rgb[max(0, y - h // 2):y + h + h // 2, left:max(left + 1, x - 2)].max(axis=2)
    votes: Counter = Counter()
    for image in _variants(digits):
        for text in ocr.lines(image):
            number = re.sub(r"[^\d]", "", text.replace("O", "0").replace("О", "0").replace("о", "0"))
            if number:
                votes[int(number)] += 1
    if not votes:
        return True, None
    value, count = votes.most_common(1)[0]
    return True, value if count >= 2 else None


def read_presence_hud(frame_rgb: np.ndarray, ocr: _Ocr) -> tuple[str | None, bool | None]:
    """Return only role/party values backed by explicit HUD labels; unknown stays unknown."""
    height, width = frame_rgb.shape[:2]
    party_crop = frame_rgb[int(height * 0.91):int(height * 0.985), int(width * 0.15):int(width * 0.30)]
    role_crop = frame_rgb[int(height * 0.89):int(height * 0.985), int(width * 0.38):int(width * 0.66)]
    party_text = _read_hud_text(party_crop, ocr)
    role_text = _read_hud_text(role_crop, ocr)
    if any(marker in party_text for marker in ("SOLO", "СОЛО")):
        in_party: bool | None = False
    elif any(marker in party_text for marker in ("DUO", "TRIO", "PARTY", "ПАТИ", "ДУО", "ТРИО")):
        in_party = True
    else:
        in_party = None

    if "СТАТЬПРЕСТУПНИКОМ" in role_text:
        role = "civilian"
    elif "СТАТЬПОЛИЦЕЙСКИМ" in role_text:
        role = "criminal"
    else:
        role = None
    return role, in_party


def read_named_location(frame_rgb: np.ndarray, ocr: _Ocr) -> str | None:
    """Read only explicit public-place names in the world view; everything else stays private/unknown."""
    height, width = frame_rgb.shape[:2]
    map_panel = frame_rgb[int(height * 0.90):int(height * 0.985), int(width * 0.32):int(width * 0.68)]
    map_text = _read_hud_text(map_panel, ocr)
    map_labels = ("МОТЕЛЬЛЕНИ", "ЛУТИБЕЙ", "ЦЕНТРСОБЫТИЙ")
    if sum(marker in map_text for marker in map_labels) >= 2:
        return "map"

    world = frame_rgb[int(height * 0.06):int(height * 0.76), int(width * 0.14):int(width * 0.86)]
    text = _read_hud_text(world, ocr)
    if any(marker in text for marker in ("LOOTYBAY", "ЛУТИБЕЙ", "ЛУТИ-БЕЙ")):
        return "looty_bay"
    if "MOTEL" in text or "МОТЕЛЬЛЕНИ" in text:
        return "motel_lenny"
    if any(marker in text for marker in ("CORRECTIONAL", "PRISON", "ТЮРЬМ")):
        return "prison"
    return None


def _read_hud_text(crop: np.ndarray, ocr: _Ocr) -> str:
    if not crop.size:
        return ""
    grey = crop.max(axis=2)
    enlarged = cv2.resize(grey, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, thresholded = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lines = ocr.lines(enlarged) + ocr.lines(thresholded)
    return "".join("".join(line.upper().split()) for line in lines)


class GameWatcher(threading.Thread):
    def __init__(self, get_screen: Callable[[], object | None], seasons: SeasonStore,
                 on_change: Callable[[], None] | None = None, log: PlayerLog | None = None) -> None:
        super().__init__(name="game-watch", daemon=True)
        self.get_screen = get_screen
        self.seasons = seasons
        self.on_change = on_change
        self.log = log or PlayerLog()
        self.stop_event = threading.Event()
        self.ocr = _Ocr()
        self.in_match = self.log.last_state() == "battle"
        self.on_duty: bool | None = None
        self.role: str | None = None
        self.in_party: bool | None = None
        self._presence_read_at = 0.0
        self.location_key: str | None = None
        self._location_candidate: str | None = None
        self._location_hits = 0
        self._location_read_at = 0.0
        self.log.read_new()          # from now on only new lines
        # the cop shift
        self.shift_value: int | None = None    # the last number read on the counter
        self.shift_banked = 0                  # how much of this shift is in the season already
        self._candidate: int | None = None
        self._gone = 0
        self._menu_at = 0.0

    # ---- the shift -----------------------------------------------------------------------------------------------

    def see_counter(self, visible: bool, value: int | None) -> None:
        """One look at the top right corner (also used by the tests)."""
        if visible:
            self._gone = 0
            if value is None:
                return
            if value != self._candidate:      # a number counts once two looks in a row read it the same
                self._candidate = value
                return
            if self.shift_value is not None and value < self.shift_value:
                self._bank()                  # a smaller number: a new shift began
                self.shift_value, self.shift_banked = value, 0
            elif self.shift_value is None:
                self.shift_value = value
            else:
                self.shift_value = value
            return
        self._gone += 1
        if self._gone == GONE_LOOKS:
            self._bank()

    def _bank(self) -> None:
        if self.shift_value is None:
            return
        gain = self.shift_value - self.shift_banked
        if gain > 0:
            self.seasons.add_salary(gain, new_shift=self.shift_banked == 0)
            logger.info("cop salary: +%d (shift %d, banked before %d)", gain, self.shift_value, self.shift_banked)
            self.shift_banked = self.shift_value
            if self.on_change:
                self.on_change()

    # ---- the loop ------------------------------------------------------------------------------------------------

    def run(self) -> None:
        last = time.monotonic()
        while not self.stop_event.wait(TICK):
            now = time.monotonic()
            elapsed, last = now - last, now
            try:
                self._tick(elapsed)
            except Exception:
                logger.debug("game watch tick failed", exc_info=True)

    def _tick(self, elapsed: float) -> None:
        for event in self.log.read_new():
            if event == "battle":
                self.in_match = True
            elif event == "lobby":
                self.in_match = False
        screen = self.get_screen()
        open_ = bool(screen is not None and screen.is_target_open())
        if not open_:
            self.in_match = False
        self.seasons.roll_over()
        if open_ and self.in_match:
            self.seasons.add_playtime(min(elapsed, 3 * TICK))
        frame = None
        if open_:
            try:
                frame = np.asarray(screen.capture())
            except Exception:
                frame = None   # the game is in the background: nothing to see now
        if not self.in_match:
            self.on_duty = None
            self.role = None
            self.in_party = None
            self.location_key = None
            self._location_candidate = None
            self._location_hits = 0
            self.see_counter(False, None)      # in the menu or the game is gone: no shift goes on
            if frame is not None and time.monotonic() - self._menu_at > MENU_EVERY:
                self._menu_at = time.monotonic()
                age = read_server_age(frame, self.ocr)
                if age is not None:
                    started = time.time() - age
                    self.seasons.data["last_server_start"] = started
                    if self.seasons.correct_start(started) and self.on_change:
                        self.on_change()
            return
        if frame is not None:
            visible, value = read_duty_counter(frame, self.ocr)
            self.on_duty = visible
            self.see_counter(visible, value)
            if time.monotonic() - self._presence_read_at >= PRESENCE_HUD_EVERY:
                self._presence_read_at = time.monotonic()
                self.role, self.in_party = read_presence_hud(frame, self.ocr)
            if visible:
                self.role = "police"
            if time.monotonic() - self._location_read_at >= PRESENCE_HUD_EVERY:
                self._location_read_at = time.monotonic()
                found = read_named_location(frame, self.ocr)
                if found is None:
                    self._location_candidate = None
                    self._location_hits = 0
                    self.location_key = None
                elif found == self._location_candidate:
                    self._location_hits += 1
                    if self._location_hits >= 2:
                        self.location_key = found
                else:
                    self._location_candidate = found
                    self._location_hits = 1
        else:
            self.on_duty = None
            self.role = None
            self.in_party = None
            self.location_key = None

    def stop(self) -> None:
        self.stop_event.set()
        self._bank()
        self.seasons.save()
