"""Windows desktop capture and target-window checks."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

import numpy as np
import pyautogui
from PIL import ImageGrab


class TargetWindowNotFound(RuntimeError):
    pass


class TargetWindowNotForeground(RuntimeError):
    pass


class ScreenService:
    def __init__(self, target_title_match: str, *, background_control: bool = False) -> None:
        self.target_title_match = target_title_match.strip().casefold()
        self.background_control = bool(background_control)
        if not self.target_title_match:
            raise ValueError("Set part of the game window title before calibration")
        if not hasattr(ctypes, "windll"):
            raise OSError("Harvest Assistant currently supports Windows only")
        self._user32 = ctypes.windll.user32
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = [wintypes.HWND]
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._user32.GetClientRect.restype = wintypes.BOOL
        self._user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self._user32.ClientToScreen.restype = wintypes.BOOL
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    def foreground_window(self) -> tuple[int, str]:
        return self.active_window_details()

    @staticmethod
    def active_window_details() -> tuple[int, str]:
        if not hasattr(ctypes, "windll"):
            return 0, ""
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        raw_handle = user32.GetForegroundWindow()
        handle = int(getattr(raw_handle, "value", raw_handle) or 0)
        return handle, ScreenService._window_title(handle)

    def is_target_foreground(self) -> bool:
        handle, title = self.foreground_window()
        return bool(handle and self._matches(title))

    def is_automation_ready(self) -> bool:
        """Whether automation may read/control the target without stealing focus."""
        if not self.background_control:
            return self.is_target_foreground()
        try:
            hwnd = wintypes.HWND(self.target_window_handle())
        except TargetWindowNotFound:
            return False
        return bool(self._user32.IsWindowVisible(hwnd) and not self._user32.IsIconic(hwnd))

    def is_target_open(self) -> bool:
        return any(self._matches(title) for _, title in self._visible_windows())

    def contains_game_point(self, x: int, y: int) -> bool:
        if self.background_control:
            width, height = self.target_client_size()
            return 0 <= x < width and 0 <= y < height
        handle, title = self.foreground_window()
        if not handle or not self._matches(title):
            return False
        rect = wintypes.RECT()
        origin = wintypes.POINT()
        if not self._user32.GetClientRect(wintypes.HWND(handle), ctypes.byref(rect)):
            return False
        if not self._user32.ClientToScreen(wintypes.HWND(handle), ctypes.byref(origin)):
            return False
        return origin.x <= x < origin.x + rect.right and origin.y <= y < origin.y + rect.bottom

    def target_window_handle(self) -> int:
        matches = [(handle, title) for handle, title in self._visible_windows() if self._matches(title)]
        if not matches:
            raise TargetWindowNotFound(f"No visible window matches '{self.target_title_match}'")
        # Prefer the foreground window when several titles match.
        foreground_handle, _ = self.foreground_window()
        for handle, _ in matches:
            if handle == foreground_handle:
                return handle
        return matches[0][0]

    def target_client_screen_rect(self) -> tuple[int, int, int, int]:
        """Return the visible client area in virtual-desktop coordinates."""
        hwnd = wintypes.HWND(self.target_window_handle())
        rect = wintypes.RECT()
        origin = wintypes.POINT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise TargetWindowNotFound("Could not read the game client area")
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise TargetWindowNotFound("Could not locate the game window on the desktop")
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def target_client_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.target_client_screen_rect()
        return right - left, bottom - top

    def _capture_background_window(self) -> np.ndarray:
        bbox = self.target_client_screen_rect()
        image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        return np.asarray(image)

    def focus_target(self, settle_seconds: float = 0.3) -> None:
        handle = self.target_window_handle()
        if self.is_target_foreground():
            return
        hwnd = wintypes.HWND(handle)
        SW_RESTORE = 9
        if self._user32.IsIconic(hwnd):
            self._user32.ShowWindow(hwnd, SW_RESTORE)
        for attempt in range(3):
            if attempt:
                # Windows refuses SetForegroundWindow unless our process recently
                # received input; a synthetic Alt tap lifts that restriction.
                KEYEVENTF_KEYUP = 0x0002
                VK_MENU = 0x12
                self._user32.keybd_event(VK_MENU, 0, 0, 0)
                self._user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            self._user32.SetForegroundWindow(hwnd)
            time.sleep(max(0.0, settle_seconds))
            if self.is_target_foreground():
                return
        raise TargetWindowNotForeground("Windows did not focus the configured game window")

    def capture(self, require_target_foreground: bool = True) -> np.ndarray:
        if self.background_control:
            if not self.is_automation_ready():
                raise TargetWindowNotForeground("Keep the game window visible and not minimized")
            return self._capture_background_window()
        if require_target_foreground and not self.is_target_foreground():
            raise TargetWindowNotForeground("Focus the game window before screen recognition")
        image = pyautogui.screenshot().convert("RGB")
        return np.asarray(image)

    def capture_for_calibration(self, settle_seconds: float = 0.35) -> np.ndarray:
        """Focus the game and capture it after a short settle delay."""
        if self.background_control:
            return self.capture(require_target_foreground=False)
        self.focus_target(settle_seconds=settle_seconds)
        return self.capture(require_target_foreground=True)

    def _matches(self, title: str) -> bool:
        return self.target_title_match in title.casefold()

    @staticmethod
    def _window_title(handle: int) -> str:
        if not handle:
            return ""
        user32 = ctypes.windll.user32
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        length = int(user32.GetWindowTextLengthW(wintypes.HWND(handle)))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(handle), buffer, length + 1)
        return buffer.value

    def _visible_windows(self) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        enum_callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self._user32.EnumWindows.argtypes = [enum_callback_type, wintypes.LPARAM]
        self._user32.EnumWindows.restype = wintypes.BOOL

        @enum_callback_type
        def callback(handle: int, _lparam: int) -> bool:
            handle_value = int(getattr(handle, "value", handle) or 0)
            if self._user32.IsWindowVisible(wintypes.HWND(handle_value)) and self._is_external_window(handle_value):
                title = self._window_title(handle_value)
                if title and not self._is_helper_title(title):
                    result.append((handle_value, title))
            return True

        self._user32.EnumWindows(callback, 0)
        return result

    def _is_external_window(self, handle: int) -> bool:
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(process_id))
        return bool(process_id.value and process_id.value != os.getpid())

    @staticmethod
    def _is_helper_title(title: str) -> bool:
        lowered = title.casefold()
        return "hidden pool" in lowered or "harvest assistant" in lowered
