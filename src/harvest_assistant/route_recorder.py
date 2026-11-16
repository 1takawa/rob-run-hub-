"""Timed keyboard/mouse route capture with table waypoint markers."""

from __future__ import annotations

import threading
import time

from pynput import keyboard

from .models import Profile, RecordedRoute, RouteEvent, ScreenMarker, Waypoint
from .raw_mouse import RawMouseReader
from .screen import ScreenService
from .vision import VisionService


class RouteRecorder:
    CONTROL_KEYS = {keyboard.Key.f5, keyboard.Key.f6, keyboard.Key.f8, keyboard.Key.f9, keyboard.Key.esc}
    MOVEMENT_VIRTUAL_KEYS = {
        0x57: "w",
        0x41: "a",
        0x53: "s",
        0x44: "d",
    }

    def __init__(self, screen: ScreenService, vision: VisionService, profile: Profile) -> None:
        self.screen = screen
        self.vision = vision
        self.profile = profile
        self._lock = threading.RLock()
        self._events: list[RouteEvent] = []
        self._waypoints: list[Waypoint] = []
        self.lab_offset: float | None = None
        self._start_time: float | None = None
        self._mouse_motion_count = 0
        self._mouse_total_distance = 0
        self._held_keys: set[str] = set()
        self._recording = False
        self._keyboard_listener: keyboard.Listener | None = None
        self._mouse_reader: RawMouseReader | None = None
        self._pending_mouse = [0, 0]
        self._last_mouse_emit = 0.0
        self._start_anchor = ""
        self._end_anchor = ""
        self._capture_tag = ""
        self._start_region: tuple[int, int, int, int] | None = None
        self._end_region: tuple[int, int, int, int] | None = None

    @property
    def mouse_trace_usable(self) -> bool:
        return self._mouse_motion_count >= 3 and self._mouse_total_distance >= 24

    @property
    def marked_table_ids(self) -> set[str]:
        with self._lock:
            return {waypoint.table_id for waypoint in self._waypoints}

    def start(self, shared_marker: ScreenMarker | None = None) -> None:
        if self._recording:
            raise RuntimeError("Route recording is already active")
        if not self.screen.is_target_foreground():
            raise RuntimeError("Focus the game and stand at the route start landmark")
        frame = self.screen.capture()
        if shared_marker and not self.vision.match_template(
            frame, shared_marker.template, shared_marker.region, self.profile.interaction.anchor_threshold,
        ).found:
            raise RuntimeError("Общий старт не распознан. Встань на старт и наведи камеру как при настройке.")
        height, width = frame.shape[:2]
        self._start_region = (0, 0, width, height)
        self._capture_tag = str(time.time_ns())
        self._start_anchor = self._save_raw_anchor(frame, f"route_start_raw_{self._capture_tag}")

        with self._lock:
            self._events = []
            self._waypoints = []
            self.lab_offset = None
            self._start_time = time.monotonic()
            self._pending_mouse = [0, 0]
            self._last_mouse_emit = 0.0
            self._mouse_motion_count = 0
            self._mouse_total_distance = 0
            self._held_keys.clear()
            self._recording = True

        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_reader = RawMouseReader(self._on_raw_mouse)
        self._keyboard_listener.start()
        try:
            self._mouse_reader.start()
        except Exception:
            self._stop_listeners()
            self._recording = False
            raise

    def mark_waypoint(self, table_id: str) -> Waypoint:
        if not self._recording or not self.screen.is_target_foreground():
            raise RuntimeError("Waypoints can only be marked while recording in the game window")
        frame = self.screen.capture()
        height, width = frame.shape[:2]
        raw_name = self._save_raw_anchor(frame, f"table_{table_id}_raw_{self._capture_tag}")
        offset = self._elapsed()
        waypoint = Waypoint(
            table_id=table_id,
            event_offset=offset,
            anchor_template=raw_name,
            ready_icon_template="",
            growing_icon_template="",
            screen_region=(0, 0, width, height),
            capture_template=raw_name,
        )
        with self._lock:
            self._waypoints.append(waypoint)
        return waypoint

    def mark_laboratory(self) -> None:
        if not self._recording or not self.screen.is_target_foreground():
            raise RuntimeError("Поставь отметку лаборатории во время записи в игре.")
        with self._lock:
            if self._held_keys:
                raise RuntimeError("Отпусти клавиши движения перед отметкой лаборатории.")
            offset = self._elapsed()
            self._flush_mouse_locked(offset)
            self.lab_offset = offset

    def finish(self) -> RecordedRoute:
        if not self._recording:
            raise RuntimeError("Route recording is not active")
        if not self.screen.is_target_foreground():
            self._stop_listeners()
            self._recording = False
            raise RuntimeError("Return to the route start landmark in the game before finishing")
        frame = self.screen.capture()
        height, width = frame.shape[:2]
        self._end_region = (0, 0, width, height)
        self._end_anchor = self._save_raw_anchor(frame, f"route_end_raw_{self._capture_tag}")
        duration = self._elapsed()
        with self._lock:
            self._flush_mouse_locked(duration)
        self._release_recorded_keys()
        self._stop_listeners()
        self._recording = False

        with self._lock:
            events = sorted(self._events, key=lambda event: event.elapsed_offset)
            waypoints = sorted(self._waypoints, key=lambda waypoint: waypoint.event_offset)
        return RecordedRoute(
            events=events,
            waypoints=waypoints,
            start_anchor_template=self._start_anchor,
            end_anchor_template=self._end_anchor,
            duration_seconds=duration,
            start_screen_region=self._start_region,
            end_screen_region=self._end_region,
            start_capture_template=self._start_anchor,
            end_capture_template=self._end_anchor,
            lab_offset=self.lab_offset,
        )

    def cancel(self) -> None:
        self._stop_listeners()
        self._recording = False

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not self._recording:
            return
        if not self.screen.is_target_foreground():
            self._release_recorded_keys()
            return
        if key in self.CONTROL_KEYS:
            return
        value = self._key_value(key)
        if value:
            with self._lock:
                if value in self._held_keys:
                    return
                self._held_keys.add(value)
                self._events.append(RouteEvent(self._elapsed(), "key_down", value))

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not self._recording:
            return
        if not self.screen.is_target_foreground():
            self._release_recorded_keys()
            return
        if key in self.CONTROL_KEYS:
            return
        value = self._key_value(key)
        if value:
            with self._lock:
                if value in self._held_keys:
                    self._held_keys.remove(value)
                    self._events.append(RouteEvent(self._elapsed(), "key_up", value))

    def _on_raw_mouse(self, dx: int, dy: int) -> None:
        """Real relative camera motion, merged into ~16 ms steps to keep replay light."""
        if not self._recording:
            return
        if not self.screen.is_target_foreground():
            self._release_recorded_keys()
            return
        with self._lock:
            self._pending_mouse[0] += dx
            self._pending_mouse[1] += dy
            now = self._elapsed()
            if now - self._last_mouse_emit >= 0.016:
                self._flush_mouse_locked(now)

    def _flush_mouse_locked(self, offset: float) -> None:
        dx, dy = self._pending_mouse
        self._pending_mouse = [0, 0]
        self._last_mouse_emit = offset
        if dx == 0 and dy == 0:
            return
        self._events.append(RouteEvent(offset, "mouse_delta", (dx, dy)))
        self._mouse_motion_count += 1
        self._mouse_total_distance += abs(dx) + abs(dy)

    def _elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        return max(0.0, time.monotonic() - self._start_time)

    def _save_raw_anchor(self, frame, stem: str) -> str:
        height, width = frame.shape[:2]
        name = f"{stem}.png"
        self.vision.save_template(frame, (0, 0, width, height), name)
        return name

    def _stop_listeners(self) -> None:
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener.join(timeout=1.0)
        if self._mouse_reader is not None:
            self._mouse_reader.stop()
        self._keyboard_listener = None
        self._mouse_reader = None

    def _release_recorded_keys(self) -> None:
        with self._lock:
            held = tuple(self._held_keys)
            self._held_keys.clear()
            offset = self._elapsed()
            self._events.extend(RouteEvent(offset, "key_up", key) for key in held)

    @staticmethod
    def _key_value(key: keyboard.Key | keyboard.KeyCode | None) -> str | None:
        if key is None:
            return None
        if isinstance(key, keyboard.KeyCode):
            # Use physical virtual-key codes so Russian/other keyboard layouts
            # still record the W/A/S/D movement keys the game receives.
            return RouteRecorder.MOVEMENT_VIRTUAL_KEYS.get(key.vk)
        name = getattr(key, "name", "")
        if name == "shift":
            return name
        return None
