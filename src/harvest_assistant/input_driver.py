"""Input playback with foreground and opt-in window-message backends."""

from __future__ import annotations

import ctypes
import threading
import time

import pydirectinput

from .models import RouteEvent
from .screen import ScreenService
from .window_input import MK_LBUTTON, WindowMessageInput


class InputInterrupted(RuntimeError):
    pass


class InputStopped(InputInterrupted):
    pass


class InputPaused(InputInterrupted):
    pass


class InputFocusLost(InputInterrupted):
    pass


class InputDriver:
    def __init__(
        self,
        screen: ScreenService,
        stop_event: threading.Event,
        *,
        window_input: WindowMessageInput | None = None,
    ) -> None:
        self.screen = screen
        self.stop_event = stop_event
        self.pause_event = threading.Event()
        self.window_input = None
        if screen.background_control:
            self.window_input = window_input or WindowMessageInput(screen.target_window_handle)
        self._background_cursor: tuple[int, int] | None = None
        if hasattr(pydirectinput, "PAUSE"):
            pydirectinput.PAUSE = 0.0
        self._pressed_keys: set[str] = set()
        self._dragging = False
        self._lock = threading.RLock()

    def play_key(self, key: str, duration: float = 0.12) -> None:
        key = key.strip().lower()
        if not key:
            raise ValueError("key must not be empty")
        with self._lock:
            self._guard()
            self._key_down(key)
        try:
            self.wait(duration)
        finally:
            self._release_key(key)

    def move_relative(self, dx: int, dy: int) -> None:
        with self._lock:
            self._guard()
            if dx == 0 and dy == 0:
                return
            if self.window_input is not None:
                width, height = self.screen.target_client_size()
                x, y = self._background_cursor or (width // 2, height // 2)
                x = max(0, min(width - 1, x + int(dx)))
                y = max(0, min(height - 1, y + int(dy)))
                self._background_cursor = (x, y)
                self.window_input.mouse_move(x, y, MK_LBUTTON if self._dragging else 0)
                return
            # relative=True sends raw relative motion; the default moves the cursor to an
            # absolute position, which a cursor-locked game ignores (camera only twitches).
            pydirectinput.moveRel(int(dx), int(dy), relative=True)
            self._guard()

    def _move_to(self, x: int, y: int) -> None:
        self._guard()
        if self.window_input is not None:
            width, height = self.screen.target_client_size()
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"Координаты ({x}, {y}) вне окна игры ({width}×{height}).")
            self._background_cursor = (x, y)
            self.window_input.mouse_move(x, y, MK_LBUTTON if self._dragging else 0)
            return
        width, height = pydirectinput.size()
        if not (0 <= x < width and 0 <= y < height and self.screen.contains_game_point(x, y)):
            raise ValueError(f"Координаты лаборатории ({x}, {y}) вне окна игры на основном экране.")
        pydirectinput.moveTo(x, y)
        self._guard()

    def click_at(self, x: int, y: int, button: str) -> None:
        with self._lock:
            self._move_to(x, y)
            if self.window_input is not None:
                self.window_input.mouse_down(x, y, button)
                try:
                    self.wait(0.12)
                finally:
                    self.window_input.mouse_up(x, y, button)
                return
            pydirectinput.click(button=button)

    def drag_to(self, x: int, y: int, end_x: int, end_y: int) -> None:
        with self._lock:
            self._move_to(x, y)
            if self.window_input is not None:
                self.window_input.mouse_down(x, y, "left")
            else:
                pydirectinput.mouseDown(button="left")
            self._dragging = True
        try:
            self.wait(0.12)
            with self._lock:
                self._move_to(end_x, end_y)
            self.wait(0.12)
        finally:
            with self._lock:
                self._release_drag_locked()

    def play_route_event(self, event: RouteEvent) -> None:
        if event.kind == "key_down":
            key = str(event.value or "").lower()
            if not key:
                raise ValueError("key_down event has no key")
            with self._lock:
                self._guard()
                self._key_down(key)
        elif event.kind == "key_up":
            key = str(event.value or "").lower()
            with self._lock:
                self._guard()
                self._release_key(key)
        elif event.kind == "mouse_delta":
            if not isinstance(event.value, tuple) or len(event.value) != 2:
                raise ValueError("mouse_delta event must contain an (x, y) tuple")
            self.move_relative(event.value[0], event.value[1])
        elif event.kind == "delay":
            self.wait(float(event.value or 0.0))
        else:
            raise ValueError(f"Unsupported route event: {event.kind}")

    def wait(self, duration: float, interval: float = 0.05) -> None:
        deadline = time.monotonic() + max(0.0, duration)
        while time.monotonic() < deadline:
            self._guard()
            self.stop_event.wait(min(interval, max(0.0, deadline - time.monotonic())))
        self._guard()

    def pause(self) -> None:
        with self._lock:
            self.pause_event.set()
            self._release_all_locked()

    def resume(self) -> None:
        with self._lock:
            if self.stop_event.is_set():
                raise InputStopped("Stopped input cannot be resumed")
            self.pause_event.clear()

    def stop(self) -> None:
        with self._lock:
            self.stop_event.set()
            self._release_all_locked()

    def release_all(self) -> None:
        with self._lock:
            self._release_all_locked()

    def _key_down(self, key: str) -> None:
        if self.window_input is not None:
            self.window_input.key_down(key)
        else:
            pydirectinput.keyDown(key)
        self._pressed_keys.add(key)

    def _release_all_locked(self) -> None:
        self._release_drag_locked()
        keys = tuple(self._pressed_keys)
        self._pressed_keys.clear()
        for key in keys:
            try:
                if self.window_input is not None:
                    self.window_input.key_up(key)
                else:
                    pydirectinput.keyUp(key)
            except Exception:
                # Continue releasing the rest of the held keys.
                pass

    def _release_drag_locked(self) -> None:
        if self._dragging:
            self._dragging = False
            if self.window_input is not None:
                x, y = self._background_cursor or (0, 0)
                self.window_input.mouse_up(x, y, "left")
            else:
                # Release even when the mouse reaches the PyDirectInput fail-safe corner.
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _release_key(self, key: str) -> None:
        with self._lock:
            if key not in self._pressed_keys:
                return
            self._pressed_keys.discard(key)
            try:
                if self.window_input is not None:
                    self.window_input.key_up(key)
                else:
                    pydirectinput.keyUp(key)
            except Exception:
                self._pressed_keys.add(key)
                raise

    def _guard(self) -> None:
        if self.stop_event.is_set():
            self.release_all()
            raise InputStopped("Emergency stop is active")
        if self.pause_event.is_set():
            self.release_all()
            raise InputPaused("Route is paused")
        if not self.screen.is_automation_ready():
            self.release_all()
            reason = (
                "Game window is hidden or minimized"
                if self.screen.background_control
                else "Game window lost focus; input was stopped"
            )
            raise InputFocusLost(reason)
