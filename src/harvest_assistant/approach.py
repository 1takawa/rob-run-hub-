"""Recorded path to the grow room, with door prompts verified on playback.

The user walks the way once (WASD, Shift, camera) and presses E at every door. Each E is saved as an
``interact`` event that remembers what the game offered at that moment ("open" / "close"). On playback
the path is replayed, but E is only pressed when the screen shows the same prompt.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard

from .models import RecordedRoute, RouteEvent
from .raw_mouse import RawMouseReader
from .taught import jpeg_frame

KEYS = {0x57: "w", 0x41: "a", 0x53: "s", 0x44: "d"}
INTERACT_VK = 0x45  # E


class ApproachRecorder:
    def __init__(self, screen: object, prompt_reader: object, on_done: Callable[[RouteEvent], None] | None = None) -> None:
        self.screen = screen
        self.prompt_reader = prompt_reader
        self._lock = threading.RLock()
        self._events: list[RouteEvent] = []
        self._held: set[str] = set()
        self._start = 0.0
        self._recording = False
        self._pending = [0, 0]
        self._last_mouse = 0.0
        self._keyboard: keyboard.Listener | None = None
        self._mouse: RawMouseReader | None = None
        self._ocr_threads: list[threading.Thread] = []
        self.frames: list[tuple[float, bytes]] = []  # (time, jpeg) key frames for teaching
        self._frame_thread: threading.Thread | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def interactions(self) -> int:
        return sum(1 for event in self._events if event.kind == "interact")

    def _elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._start)

    def start(self) -> None:
        if self._recording:
            raise RuntimeError("Запись пути уже идёт")
        if not self.screen.is_target_foreground():
            raise RuntimeError("Выйди в игру и встань на спавн в доме")
        with self._lock:
            self._events, self._held = [], set()
            self._pending, self._last_mouse = [0, 0], 0.0
            self._start = time.monotonic()
            self._recording = True
        self.frames = []
        self._frame_thread = threading.Thread(target=self._frame_loop, daemon=True)
        self._frame_thread.start()
        self._keyboard = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._mouse = RawMouseReader(self._on_mouse)
        self._keyboard.start()
        try:
            self._mouse.start()
        except Exception:
            self.cancel()
            raise

    # ---- listeners ----------------------------------------------------------------------

    @staticmethod
    def _name(key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if isinstance(key, keyboard.KeyCode):
            if key.vk == INTERACT_VK:
                return "e"
            return KEYS.get(key.vk)
        name = getattr(key, "name", "") or ""
        return "shift" if name.startswith("shift") else None

    def _on_press(self, key) -> None:
        if not self._recording or not self.screen.is_target_foreground():
            return
        name = self._name(key)
        if name is None:
            return
        with self._lock:
            if name == "e":
                threading.Thread(target=self._snap, daemon=True).start()  # what the door looked like
                event = RouteEvent(self._elapsed(), "interact", "")
                self._events.append(event)
                thread = threading.Thread(target=self._remember_prompt, args=(event,), daemon=True)
                self._ocr_threads.append(thread)
                thread.start()
            elif name not in self._held:
                self._held.add(name)
                self._events.append(RouteEvent(self._elapsed(), "key_down", name))

    def mark_table(self) -> bool:
        """The author stands at a table and says so with a key instead of pressing E (a table that is not ripe has
        nothing to harvest, and pressing E there is not needed). Saved like an E at the table menu."""
        if not self._recording:
            return False
        with self._lock:
            self._events.append(RouteEvent(self._elapsed(), "interact", "harvest"))
        threading.Thread(target=self._snap, daemon=True).start()
        return True

    FRAME_EVERY = 0.6
    MAX_FRAMES = 500

    def _snap(self) -> None:
        if not self._recording or len(self.frames) >= self.MAX_FRAMES or not self.screen.is_target_foreground():
            return
        offset = self._elapsed()
        try:
            data = jpeg_frame(self.screen.capture())
        except Exception:
            return
        if data:
            with self._lock:
                self.frames.append((offset, data))

    def _frame_loop(self) -> None:
        while self._recording:
            self._snap()
            time.sleep(self.FRAME_EVERY)

    def _remember_prompt(self, event: RouteEvent) -> None:
        """What the game offers right now ("open"/"close"): read the screen without blocking input."""
        try:
            frame = self.screen.capture()
            # the menu of a table also has an «Открыть» line: tell a table from a door before reading the prompt
            harvest = getattr(self.prompt_reader, "harvest_visible", None)
            prompt = "harvest" if harvest is not None and harvest(frame) else self.prompt_reader.read(frame)
        except Exception:
            prompt = None
        event.value = prompt or ""

    def _on_release(self, key) -> None:
        if not self._recording:
            return
        name = self._name(key)
        with self._lock:
            if name in self._held:
                self._held.discard(name)
                self._events.append(RouteEvent(self._elapsed(), "key_up", name))

    def _on_mouse(self, dx: int, dy: int) -> None:
        if not self._recording or not self.screen.is_target_foreground():
            return
        with self._lock:
            self._pending[0] += dx
            self._pending[1] += dy
            now = self._elapsed()
            if now - self._last_mouse >= 0.016:
                self._flush_mouse(now)

    def _flush_mouse(self, offset: float) -> None:
        dx, dy = self._pending
        self._pending, self._last_mouse = [0, 0], offset
        if dx or dy:
            self._events.append(RouteEvent(offset, "mouse_delta", (dx, dy)))

    # ---- end ----------------------------------------------------------------------------

    def _stop(self) -> None:
        self._recording = False
        if self._keyboard is not None:
            self._keyboard.stop()
            self._keyboard = None
        if self._mouse is not None:
            self._mouse.stop()
            self._mouse = None

    def cancel(self) -> None:
        self._stop()

    def finish(self) -> RecordedRoute:
        if not self._recording:
            raise RuntimeError("Запись пути не идёт")
        duration = self._elapsed()
        with self._lock:
            self._flush_mouse(duration)
            for name in sorted(self._held):
                self._events.append(RouteEvent(duration, "key_up", name))
            self._held.clear()
        self._stop()
        if self._frame_thread is not None:
            self._frame_thread.join(timeout=2.0)
        for thread in self._ocr_threads:
            thread.join(timeout=3.0)
        with self._lock:
            self.frames.sort(key=lambda item: item[0])
        with self._lock:
            events = sorted(self._events, key=lambda event: event.elapsed_offset)
        return RecordedRoute(events=events, duration_seconds=duration)
