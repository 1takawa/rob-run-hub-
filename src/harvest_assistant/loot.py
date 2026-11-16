"""Reads the «+N Пакет с урожаем» pick-up notifications shown at the right edge.

Count and name come from Windows' built-in OCR; the package kind is the colour
of the bar to the left of each notification row. No templates to calibrate.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass

import cv2
import numpy as np

# Where the pick-up list lives, as fractions of the game frame (left, top, right, bottom).
LOOT_REGION = (0.78, 0.42, 1.0, 0.82)
_ROW_RE = re.compile(r"^\s*\+\s*(\d+)\s+(.+?)\s*$")
# OCR sometimes garbles the "+" ("t 4", "± 4") or glues the digit to the word: accept those too,
# but only for lines that clearly name a package.
_LOOSE_ROW_RE = re.compile(r"^\D{0,3}(\d{1,3})\s*[xх×]?\s*(.*(?:акет|урожа).*)$", re.IGNORECASE)
_UPSCALE = 3


@dataclass(slots=True)
class LootItem:
    name: str
    count: int
    color: str
    rgb: tuple[int, int, int]

    @property
    def key(self) -> str:
        return f"{self.color}|{self.name}"


def color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    hsv = cv2.cvtColor(np.uint8([[[r, g, b]]]), cv2.COLOR_RGB2HSV)[0, 0]
    hue, sat, val = int(hsv[0]) * 2, int(hsv[1]), int(hsv[2])
    if sat < 60:
        return "белый" if val > 170 else "серый"
    for limit, name in (
        (15, "красный"), (30, "оранжевый"), (70, "жёлтый"), (165, "зелёный"),
        (200, "голубой"), (255, "синий"), (310, "фиолетовый"), (345, "розовый"),
    ):
        if hue < limit:
            return name
    return "красный"


def split_key(key: str) -> tuple[str, str]:
    color, _, name = key.partition("|")
    return color, name


class LootReader:
    """OCR + colour reader. Safe to call from any thread, one call at a time."""

    def __init__(self, region: tuple[float, float, float, float] = LOOT_REGION) -> None:
        self.region = region
        self._lock = threading.Lock()
        self._engine = None
        self._error: str | None = None
        self.last_crop: np.ndarray | None = None
        self.last_raw: list[str] = []

    @property
    def error(self) -> str | None:
        return self._error

    def read(self, frame: np.ndarray) -> list[LootItem]:
        height, width = frame.shape[:2]
        left, top, right, bottom = self.region
        crop = np.ascontiguousarray(frame[int(top * height) : int(bottom * height), int(left * width) : int(right * width)])
        self.last_crop = crop
        with self._lock:
            try:
                lines = asyncio.run(self._ocr_lines(crop))
            except Exception as exc:  # OCR must never break a harvest run
                self._error = f"{type(exc).__name__}: {exc}"
                return []
        self._error = None
        self.last_raw = [text for text, _box in lines]
        items: list[LootItem] = []
        for text, (x, y, w, h) in lines:
            match = _ROW_RE.match(text) or _LOOSE_ROW_RE.match(text)
            if not match:
                continue
            rgb = self._bar_color(crop, int(x / _UPSCALE), int(y / _UPSCALE), int(h / _UPSCALE))
            items.append(LootItem(match.group(2), int(match.group(1)), color_name(rgb), rgb))
        return items

    @staticmethod
    def _bar_color(crop: np.ndarray, text_x: int, row_y: int, row_h: int) -> tuple[int, int, int]:
        """Colour of the vertical bar that sits ~45 px left of the text (left of the bag icon).

        Only a narrow window around the bar is searched, so the game scene behind the list and
        the olive bag icon cannot be mistaken for it.
        """
        pad = max(3, row_h // 3)
        top, bottom = max(0, row_y - pad), row_y + row_h + pad
        left, right = max(0, text_x - 62), max(1, text_x - 32)
        band = crop[top:bottom, left:right]
        if band.size == 0:
            return (128, 128, 128)
        hsv = cv2.cvtColor(band, cv2.COLOR_RGB2HSV)
        for min_sat, min_val in ((110, 150), (80, 110), (55, 80)):  # strict first, then for faded bars
            mask = (hsv[..., 1] > min_sat) & (hsv[..., 2] > min_val)
            if mask.sum() >= 3:
                pixels = band[mask]
                saturation = hsv[..., 1][mask]
                strongest = pixels[saturation >= np.percentile(saturation, 50)]
                r, g, b = (int(v) for v in np.median(strongest, axis=0))
                return (r, g, b)
        return (128, 128, 128)

    async def _ocr_lines(self, crop: np.ndarray):
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        engine = self._get_engine()
        big = cv2.resize(crop, None, fx=_UPSCALE, fy=_UPSCALE, interpolation=cv2.INTER_CUBIC)
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(big, cv2.COLOR_RGB2BGR))
        if not ok:
            return []
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(encoded.tobytes())
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            x0 = min(word.bounding_rect.x for word in words)
            y0 = min(word.bounding_rect.y for word in words)
            x1 = max(word.bounding_rect.x + word.bounding_rect.width for word in words)
            y1 = max(word.bounding_rect.y + word.bounding_rect.height for word in words)
            lines.append((line.text, (x0, y0, x1 - x0, y1 - y0)))
        return lines

    def _get_engine(self):
        if self._engine is None:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            engine = OcrEngine.try_create_from_language(Language("ru"))
            if engine is None:
                engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                raise RuntimeError("В Windows не установлен язык распознавания текста (русский)")
            self._engine = engine
        return self._engine


def summarize(items: list[LootItem]) -> str:
    if not items:
        return "пакеты не найдены"
    return ", ".join(f"{item.count} × {item.name} ({item.color})" for item in items)


def watch_loot(reader, capture, wait, timeout: float, save_debug=None) -> list[LootItem]:
    """Watch the right edge after a harvest and count every «+N Пакет…» notification.

    Notifications can appear one after another (and fade), so frames are not compared as a
    whole: each row that newly appears counts once, a row that stays on screen across frames
    does not count twice. Stops 1.5 s after the last row was seen, or after `timeout`.
    `capture()` returns a frame, `wait(seconds)` sleeps (and may raise to stop the run),
    `save_debug(reason, crop=None)` optionally keeps the pick-up area of a doubtful read.
    """
    import logging
    import time

    logger = logging.getLogger(__name__)
    deadline = time.monotonic() + timeout
    if reader is None:
        wait(timeout)
        return []
    held: dict[tuple, int] = {}      # row key -> how many copies are on screen now
    missing: dict[tuple, int] = {}   # row key -> consecutive frames it was not seen
    events: dict[tuple, LootItem] = {}
    last_seen: float | None = None
    saved_debug = False
    frames: list = []
    seen_text: list[str] = []
    frame_index = 0
    while time.monotonic() < deadline:
        wait(0.2)
        items = reader.read(capture())
        for line in getattr(reader, "last_raw", []):
            if line not in seen_text and len(seen_text) < 8:
                seen_text.append(line)
        if frame_index in (1, 3, 6, 10, 18) and getattr(reader, "last_crop", None) is not None:
            frames.append(reader.last_crop.copy())
        frame_index += 1
        current: dict[tuple, int] = {}
        for item in items:
            key = (item.color, item.name, item.count)
            current[key] = current.get(key, 0) + 1
        for key, amount in current.items():
            new = amount - held.get(key, 0)
            if new > 0:
                sample = next(i for i in items if (i.color, i.name, i.count) == key)
                group = (sample.color, sample.name)
                if group in events:
                    events[group].count += sample.count * new
                else:
                    events[group] = LootItem(sample.name, sample.count * new, sample.color, sample.rgb)
            held[key] = max(amount, held.get(key, 0))
            missing[key] = 0
        for key in list(held):
            if key not in current:
                missing[key] = missing.get(key, 0) + 1
                if missing[key] >= 3:  # gone for ~0.6 s: a later identical row is a new pick-up
                    held.pop(key, None)
                    missing.pop(key, None)
        if items:
            last_seen = time.monotonic()
            if save_debug is not None and not saved_debug and any(item.color in ("серый", "белый") for item in items):
                saved_debug = bool(save_debug("unknown_colour"))
        if last_seen is not None and time.monotonic() - last_seen >= 1.5:
            break
    result = list(events.values())
    if not result:
        logger.warning("Nothing recognised after harvest; OCR saw: %s", seen_text or "no text")
        if save_debug is not None:
            for number, crop in enumerate(frames):
                save_debug(f"nothing_found_{number}", crop)
    logger.info("Loot read after harvest: %s", ", ".join(f"{i.count}x {i.name} ({i.color})" for i in result) or "nothing found")
    return result
