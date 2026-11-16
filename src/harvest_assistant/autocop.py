"""Screen-guided police-service signup for ROB RUN."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from .input_driver import InputDriver
from .loadout_vision import class_button, duty_badge, find_confirm_buttons
from .screen import ScreenService, TargetWindowNotForeground


SERVICE_CLASSES = (
    "Патрульный",
    "Сержант I",
    "Сержант II",
    "Лейтенант I",
    "Капитан",
)
# What the game shows once the player is a cop: in the counter menu the button under E turns from «Начать службу» into
# «Завершить службу» (lines of text, letters and digits only, lower case). The red «Жетон ... потрачен» message and the
# blue money counter appear too, but they are brief or ambiguous: the menu button stays.
SUCCESS_MARKERS: tuple[str, ...] = ("завершитьслужбу",)

CLASS_BUTTON_X = (0.121, 0.313, 0.505, 0.697, 0.889)
CLASS_CONFIRM_Y = 0.835


@dataclass(frozen=True, slots=True)
class ServiceView:
    text: str
    service_visible: bool
    service_active: bool
    class_screen: bool
    confirm_active: bool
    confirm_bounds: tuple[float, float, float, float] | None
    too_many_cops: bool
    cooldown: bool


class AutocopScreenReader:
    """Read the service menu and the five loadout confirmation buttons."""

    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()

    def read(self, frame: np.ndarray, selected_class: str) -> ServiceView:
        height, width = frame.shape[:2]
        scale = 1.5 if max(width, height) < 1600 else 1.0
        image = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else frame
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        if not ok:
            raise RuntimeError("Не удалось подготовить изображение для распознавания.")
        with self._lock:
            lines = asyncio.run(self._ocr(encoded.tobytes()))

        rendered = " ".join(text for text, _bounds in lines)
        normalized_lines = [self._normalize(text) for text, _bounds in lines]
        service_line = next(
            (bounds for text, bounds in lines if "начатьслужбу" in self._normalize(text)),
            None,
        )
        confirm_row = self._confirm_row_lines(lines, width, height, scale)
        confirm_bounds = self._selected_confirm_bounds(
            confirm_row, selected_class, width, height, scale
        )
        active_buttons = [
            self._class_confirm_is_light(
                frame,
                self._selected_confirm_bounds(confirm_row, class_name, width, height, scale),
            )
            for class_name in SERVICE_CLASSES
        ]
        # The game's condensed font is often unreadable to OCR. Button row
        # geometry and its lime enabled state provide a reliable visual fallback.
        class_screen = len(confirm_row) >= 3 or any(active_buttons)
        # The buttons themselves are found by colour and shape (lime = available, black = not): that holds for any
        # window size or aspect, where the fractions above only fit the screen they were measured on.
        seen = find_confirm_buttons(frame)
        button = class_button(frame, SERVICE_CLASSES.index(selected_class)) if selected_class in SERVICE_CLASSES else None
        if len(seen) >= 3:
            class_screen = True
        too_many = any("слишкоммногокоп" in line for line in normalized_lines)
        cooldown = any("нужноподождать" in line or "немногоподождать" in line for line in normalized_lines)
        return ServiceView(
            text=rendered,
            service_visible=service_line is not None,
            service_active=service_line is not None and self._service_button_is_light(frame, service_line, scale),
            class_screen=class_screen,
            confirm_active=button.active if button is not None else class_screen and self._class_confirm_is_light(frame, confirm_bounds),
            confirm_bounds=(float(button.x), float(button.y), float(button.width), float(button.height)) if button is not None else confirm_bounds,
            too_many_cops=too_many,
            cooldown=cooldown,
        )

    @staticmethod
    def class_screen(frame: np.ndarray, selected_class: str):
        """The loadout screen without any text recognition (a few milliseconds): (is it up, the button of the class)."""
        row = find_confirm_buttons(frame)
        if len(row) < 3:
            return False, None
        index = SERVICE_CLASSES.index(selected_class) if selected_class in SERVICE_CLASSES else -1
        return True, class_button(frame, index) if index >= 0 else None

    def read_tokens(self, frame: np.ndarray, button) -> str | None:
        """What the class costs, written over its confirm button: «бесплатно» or «19/1» (tokens you have / it takes)."""
        import re

        y0, y1 = max(0, int(button.y - 1.25 * button.height)), int(button.y - 0.1 * button.height)
        if y1 - y0 < 4:
            return None

        def ocr(image: np.ndarray) -> str:
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                return ""
            with self._lock:
                return " ".join(text for text, _bounds in asyncio.run(self._ocr(encoded.tobytes())))

        whole = frame[y0:y1, button.x:button.x + button.width]
        k = 110 / max(1, whole.shape[0])
        text = self._normalize(ocr(cv2.copyMakeBorder(cv2.resize(cv2.cvtColor(whole, cv2.COLOR_RGB2BGR), None, fx=k, fy=k,
                                                                  interpolation=cv2.INTER_CUBIC), 40, 40, 60, 60, cv2.BORDER_REPLICATE)))
        if "беспл" in text or "платно" in text:
            return "бесплатно"
        # the number sits right of the token picture; white on dark reads best as plain brightness
        part = frame[y0:y1, button.x + int(0.42 * button.width):button.x + int(0.85 * button.width)].max(axis=2)
        part = cv2.resize(part, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        found = re.search(r"(\d+)\s*/\s*(\d+)", ocr(cv2.copyMakeBorder(part, 40, 40, 60, 60, cv2.BORDER_REPLICATE)))
        return f"{found.group(1)}/{found.group(2)}" if found else None

    async def _ocr(self, image_bytes: bytes) -> list[tuple[str, tuple[float, float, float, float]]]:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        engine = self._get_engine()
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(image_bytes)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        lines: list[tuple[str, tuple[float, float, float, float]]] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            left = min(word.bounding_rect.x for word in words)
            top = min(word.bounding_rect.y for word in words)
            right = max(word.bounding_rect.x + word.bounding_rect.width for word in words)
            bottom = max(word.bounding_rect.y + word.bounding_rect.height for word in words)
            lines.append((line.text, (left, top, right - left, bottom - top)))
        return lines

    def _get_engine(self):
        if self._engine is None:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            self._engine = OcrEngine.try_create_from_language(Language("ru"))
            if self._engine is None:
                self._engine = OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                raise RuntimeError("В Windows не установлен русский язык распознавания текста.")
        return self._engine

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(char for char in text.casefold() if char.isalnum())

    @staticmethod
    def _service_button_is_light(
        frame: np.ndarray,
        bounds: tuple[float, float, float, float],
        scale: float,
    ) -> bool:
        left, top, box_width, box_height = (int(value / scale) for value in bounds)
        height, width = frame.shape[:2]
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(width, left + box_width), min(height, top + box_height)
        text = frame[y0:y1, x0:x1]
        if not text.size:
            return False
        gray = cv2.cvtColor(text, cv2.COLOR_RGB2GRAY)
        # The button background and its green focus border are similar in both states;
        # the active label is white while an unavailable label is grey.
        return float(np.percentile(gray, 90)) >= 175 and float((gray >= 185).mean()) >= 0.05

    @staticmethod
    def _confirm_row_lines(
        lines: list[tuple[str, tuple[float, float, float, float]]],
        frame_width: int,
        frame_height: int,
        scale: float,
    ) -> list[tuple[float, float, float, float]]:
        scaled_width = frame_width * scale
        scaled_height = frame_height * scale
        row: list[tuple[float, float, float, float]] = []
        for _text, bounds in lines:
            _left, top, box_width, box_height = bounds
            center_y = top + box_height / 2
            if (
                scaled_height * 0.80 <= center_y <= scaled_height * 0.87
                and scaled_width * 0.035 <= box_width <= scaled_width * 0.10
            ):
                row.append(bounds)
        return row

    @staticmethod
    def _selected_confirm_bounds(
        confirm_row: list[tuple[float, float, float, float]],
        selected_class: str,
        frame_width: int,
        frame_height: int,
        scale: float,
    ) -> tuple[float, float, float, float] | None:
        try:
            index = SERVICE_CLASSES.index(selected_class)
        except ValueError:
            return None

        center_x = CLASS_BUTTON_X[index] * frame_width
        center_y = CLASS_CONFIRM_Y * frame_height
        if confirm_row:
            row_center_y = float(
                np.median(
                    [top + box_height / 2 for _left, top, _width, box_height in confirm_row]
                )
            )
            center_y = row_center_y / scale
            target_x = center_x * scale
            nearest = min(
                confirm_row,
                key=lambda bounds: abs(bounds[0] + bounds[2] / 2 - target_x),
            )
            nearest_x = nearest[0] + nearest[2] / 2
            if abs(nearest_x - target_x) <= frame_width * scale * 0.06:
                center_x = nearest_x / scale

        button_width = frame_width * 0.17
        button_height = frame_height * 0.038
        return (
            center_x - button_width / 2,
            center_y - button_height / 2,
            button_width,
            button_height,
        )

    @staticmethod
    def _class_confirm_is_light(
        frame: np.ndarray,
        bounds: tuple[float, float, float, float] | None,
    ) -> bool:
        if bounds is None:
            return False
        height, width = frame.shape[:2]
        left, top, box_width, box_height = (int(value) for value in bounds)
        x0, y0 = max(0, left), max(0, top)
        x1 = min(width, left + box_width)
        y1 = min(height, top + box_height)
        sample = frame[y0:y1, x0:x1]
        if not sample.size:
            return False
        gray = cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY)
        return float(gray.mean()) >= 185 and float((gray >= 205).mean()) >= 0.35


def load_memory(path) -> dict:
    """What the autocop remembers between runs: {"tokens": {class: cost}, "last": {class, tries, seconds, at}}."""
    import json
    from pathlib import Path

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_memory(path, memory: dict) -> None:
    import json
    import os
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class AutocopRunner:
    """Wait for active game prompts, join the selected class, and retry service limits."""

    def __init__(
        self,
        screen: ScreenService,
        selected_class: str,
        on_status: Callable[[str], None],
        on_finished: Callable[[str], None],
        on_feed: Callable[[dict], None] | None = None,
        hotkey: str = "",
        on_tokens: Callable[[dict], None] | None = None,
    ) -> None:
        if selected_class not in SERVICE_CLASSES:
            raise ValueError("Выбери один из пяти классов службы.")
        self.screen = screen
        self.selected_class = selected_class
        self.on_status = on_status
        self.on_finished = on_finished
        self.stop_event = threading.Event()
        self.driver = InputDriver(screen, self.stop_event)
        self.reader = AutocopScreenReader()
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)
        # what the overlay shows: attempts (E presses), refusals («слишком много копов»), the step, the last answer
        self.on_feed = on_feed
        self.hotkey = hotkey
        self.tries = 0
        self.refusals = 0
        self.tokens: str | None = None
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.state = "run"          # run: going round; wait: held back; done: joined; duty: was on duty already
        self.step = "service"       # service -> confirm -> done
        self.message = ""
        self.message_kind = ""      # refusal (red), wait (yellow), done (blue)
        self.on_tokens = on_tokens
        self.all_tokens: dict[str, str] = {}
        self._all_read = False
        self._feed_sent: tuple | None = None
        self._feed_at = 0.0
        self._tokens_at = 0.0

    def feed(self, active: bool = True) -> dict:
        return {
            "active": active, "ts": time.time(), "state": self.state, "step": self.step,
            "class": self.selected_class, "tries": self.tries, "refusals": self.refusals, "tokens": self.tokens,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "message": self.message, "message_kind": self.message_kind, "hotkey": self.hotkey,
        }

    def _publish(self, force: bool = False) -> None:
        """To the overlay: on every change, and once a second so it knows the bot is alive."""
        if self.on_feed is None:
            return
        key = (self.state, self.step, self.tries, self.refusals, self.tokens, self.message, self.ended_at)
        now = time.monotonic()
        if force or key != self._feed_sent or now - self._feed_at >= 1.0:
            self._feed_sent, self._feed_at = key, now
            try:
                self.on_feed(self.feed())
            except Exception:
                self._logger.debug("autocop feed failed", exc_info=True)

    def _trace(self, frame: np.ndarray, button) -> None:
        """A picture of what the bot saw when it could not press the class (at most every 30 s), for a look later."""
        now = time.monotonic()
        if now - getattr(self, "_traced_at", 0.0) < 30.0:
            return
        self._traced_at = now
        try:
            from pathlib import Path

            from .loadout_vision import find_confirm_buttons

            row = find_confirm_buttons(frame)
            self._logger.info("autocop: class screen, %d buttons seen %s; chosen %s -> %s", len(row),
                              [(b.x, b.y, b.width, b.active) for b in row], self.selected_class,
                              None if button is None else (button.x, button.width, button.active))
            folder = Path(__file__).resolve().parents[2] / "profiles" / "logs" / "autocop_trace"
            folder.mkdir(parents=True, exist_ok=True)
            name = folder / time.strftime("%H%M%S.jpg")
            small = cv2.resize(frame, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tofile(str(name))
            for old in sorted(folder.glob("*.jpg"))[:-20]:
                old.unlink(missing_ok=True)
        except Exception:
            self._logger.debug("autocop trace failed", exc_info=True)

    def _read_all_tokens(self, frame: np.ndarray) -> None:
        found: dict[str, str] = {}
        for index, name in enumerate(SERVICE_CLASSES):
            button = class_button(frame, index)
            if button is None:
                continue
            if not button.active:
                if name != self.selected_class:   # the chosen one was just clicked: dark for a moment, not «no tokens»
                    found[name] = "0"   # a black button: no token for it (its red «0/1» does not read)
                continue
            try:
                value = self.reader.read_tokens(frame, button)
            except Exception:
                self._logger.debug("token read failed", exc_info=True)
                value = None
            if value:
                found[name] = value
        if self.selected_class in found:
            self.tokens = found[self.selected_class]
        self._tell_tokens(found)

    def _tell_tokens(self, found: dict) -> None:
        found = {k: v for k, v in found.items() if v}
        if not found or all(self.all_tokens.get(k) == v for k, v in found.items()):
            return
        self.all_tokens.update(found)
        if self.on_tokens is not None:
            try:
                self.on_tokens(dict(found))
            except Exception:
                self._logger.debug("autocop tokens callback failed", exc_info=True)

    def _show(self, state: str, step: str, message: str = "", kind: str = "") -> None:
        self.state, self.step, self.message, self.message_kind = state, step, message, kind
        self._publish()

    @property
    def worker_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.worker_alive:
            return
        self._thread = threading.Thread(target=self._run, name="autocop", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> bool:
        self.driver.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.0, timeout))
        return not self.worker_alive

    TICK = 0.05          # seconds between looks: the buttons are pressed the moment they are there
    PRESS_GAP = 0.45     # E again no sooner than this (the class screen needs a moment to open)
    CLICK_GAP = 0.6      # the confirm button again no sooner than this (the answer of the game needs a moment)

    def _run(self) -> None:
        last_press = last_click = 0.0
        dark_since: float | None = None   # since when the chosen class's button is dark
        proofs = 0          # looks in a row that showed the success
        refused = False     # «слишком много копов» is on the screen now (a refusal counts once)
        self.started_at = time.time()
        status = "Жду активную кнопку «Начать службу» у стойки полиции."
        self.on_status(status)
        self._show("run", "service")
        try:
            while not self.stop_event.is_set():
                try:
                    frame, origin = self._capture_game_client()
                    # 1) the class screen is up: confirm at once (no text recognition, it only takes the colours)
                    up, button = self.reader.class_screen(frame, self.selected_class)
                    now = time.monotonic()
                    if up:
                        if button is not None and button.active:
                            dark_since = None
                            if self.tokens == "0":   # it is lit: there are tokens after all, read how many soon
                                self.tokens, self._tokens_at = None, 0.0
                                self._tell_tokens({self.selected_class: "есть"})
                            if now - last_click >= self.CLICK_GAP:
                                x, y = button.center
                                self.driver.click_at(x + origin[0], y + origin[1], "left")
                                last_click = time.monotonic()
                                status = f"Подтверждаю класс «{self.selected_class}»."
                                self._status(status)
                            elif not self._all_read:
                                # once per run, right after a click (the game needs a moment to answer anyway):
                                # what every class costs, for the cards of the app
                                self._all_read = True
                                self._tokens_at = now
                                self._read_all_tokens(frame)
                            elif now - self._tokens_at > 8.0:
                                # between two clicks there is time to read what the class costs
                                self._tokens_at = now
                                self.tokens = self.reader.read_tokens(frame, button) or self.tokens
                                self._tell_tokens({self.selected_class: self.tokens})
                            self._show("run", "confirm")
                        else:
                            # right after a click the button is dark for a moment: that is not «no tokens».
                            # Only a button that stays dark, with no click of ours, means the class is not available.
                            dark_since = dark_since or now
                            lasting = now - dark_since > 2.5 and now - last_click > 2.5
                            if button is None:
                                # the class screen is up but not all five buttons are seen: something covers the
                                # game (another window) or the picture is unusual. Never guess which one to press.
                                self._status(f"Не вижу все пять кнопок классов: окно игры чем-то закрыто? "
                                             f"Жду кнопку «{self.selected_class}».")
                                self._show("wait", "confirm", "Не вижу кнопку класса: игра закрыта окном?" if lasting
                                           else "Жду ответа игры", "wait")
                            else:
                                if lasting:
                                    self.tokens = "0"
                                    self._tell_tokens({self.selected_class: "0"})
                                self._status(f"Жду светлую кнопку «Подтвердить» у класса «{self.selected_class}».")
                                self._show("wait", "confirm", "Класс недоступен: нет жетона" if lasting else "Жду ответа игры",
                                           "wait")
                            if lasting:
                                self._trace(frame, button)
                        self.stop_event.wait(self.TICK)
                        continue
                    # 2) the service prompt of the counter
                    view = self.reader.read(frame, self.selected_class)
                except TargetWindowNotForeground:
                    status = "Переключись в игру; бот продолжит после возврата."
                    self._status(status)
                    self._show("wait", self.step, "Переключись в игру", "wait")
                    self.stop_event.wait(0.8)
                    continue

                # on duty: «Завершить службу» in the counter menu, or (anywhere) the blue police counter top right
                on_duty = self._succeeded(view) or duty_badge(frame)
                proofs = proofs + 1 if on_duty else 0
                if proofs >= 2 and self.tries == 0 and last_click == 0.0:
                    # started while already on duty: nothing to press. The panel says so and counts the time
                    # from the start until the autocop is stopped by hand.
                    self._status("Ты уже на службе: ничего не нажимаю, считаю время службы.")
                    self.state, self.step = "duty", "done"
                    self.message, self.message_kind = "Служба уже идёт", "done"
                    self._publish(force=True)
                    while not self.stop_event.is_set():
                        self._publish()
                        self.stop_event.wait(0.5)
                    status = "Автокоп остановлен."
                    return
                if proofs >= 2:   # twice in a row: not a misread of one picture
                    status = f"Готово: служба начата, класс «{self.selected_class}»."
                    self._status(status)
                    self.ended_at = time.time()
                    cost = "жетон списан" if self.tokens not in (None, "бесплатно") else "бесплатно"
                    self.state, self.step = "done", "done"
                    self.message, self.message_kind = f"{self.selected_class} · {cost}", "done"
                    self._publish(force=True)
                    return
                if view.too_many_cops and not refused:
                    self.refusals += 1
                refused = view.too_many_cops
                if view.too_many_cops:
                    self._status("Сейчас слишком много копов. Жду и повторю, когда служба снова станет доступна.")
                    self._show("wait", "service", f"Слишком много копов · {self.refusals} раз", "refusal")
                elif view.cooldown:
                    self._status("Нужно немного подождать. Жду, пока сообщение исчезнет.")
                    self._show("wait", "service", "Нужно подождать", "wait")
                elif view.service_active:
                    if now - last_press >= self.PRESS_GAP:
                        self.driver.play_key("e", 0.12)
                        last_press = time.monotonic()
                        self.tries += 1
                        self._status("Кнопка активна: нажимаю E и сразу выбираю класс.")
                    self._show("run", "service", f"Слишком много копов · {self.refusals} раз" if self.refusals else "",
                               "refusal" if self.refusals else "")
                elif view.service_visible:
                    self._status("Кнопка «Начать службу» пока неактивна; жду светлый сигнал.")
                    self._show("wait", "service", "Кнопка неактивна: жду светлый сигнал", "wait")
                else:
                    self._status("Жду меню полицейской стойки. Встань у NPC и запусти автокоп.")
                    self._show("wait", "service", "Встань у стойки полиции", "wait")
                self.stop_event.wait(self.TICK)
        except Exception as exc:
            self._logger.exception("Autocop stopped after an error")
            status = f"Автокоп остановлен: {exc}"
        else:
            if self.stop_event.is_set():
                status = "Автокоп остановлен."
        finally:
            self.driver.release_all()
            self.on_finished(status)

    @staticmethod
    def _succeeded(view: ServiceView) -> bool:
        text = AutocopScreenReader._normalize(view.text)
        return any(marker in text for marker in SUCCESS_MARKERS)

    def _capture_game_client(self) -> tuple[np.ndarray, tuple[int, int]]:
        frame = self.screen.capture()
        if self.screen.background_control:
            return frame, (0, 0)
        left, top, right, bottom = self.screen.target_client_screen_rect()
        height, width = frame.shape[:2]
        if left >= 0 and top >= 0 and right <= width and bottom <= height:
            crop = frame[top:bottom, left:right]
            if crop.size:
                return crop, (left, top)
        return frame, (0, 0)

    def _status(self, text: str) -> None:
        if text != getattr(self, "_last_status", None):
            self._last_status = text
            self._logger.info("autocop: %s", text)   # in the log too: what it saw can be read afterwards
            self.on_status(text)
