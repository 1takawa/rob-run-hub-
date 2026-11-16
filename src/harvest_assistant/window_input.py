"""Post keyboard and mouse messages directly to a selected game window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

_KEYS = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
}
_EXTENDED_KEYS = {0x11, 0x12, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
_MOUSE_BUTTONS = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}


def virtual_key_code(key: str) -> int:
    normalized = key.strip().lower()
    if normalized in _KEYS:
        return _KEYS[normalized]
    if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
        return ord(normalized.upper())
    if normalized.startswith("f") and normalized[1:].isdigit():
        number = int(normalized[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    raise ValueError(f"Unsupported background key: {key!r}")


def pack_key_lparam(scan_code: int, *, key_down: bool, extended: bool = False) -> int:
    value = 1 | ((scan_code & 0xFF) << 16)
    if extended:
        value |= 1 << 24
    if not key_down:
        value |= (1 << 30) | (1 << 31)
    return value


def pack_mouse_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _native_post_message(hwnd: int, message: int, wparam: int, lparam: int) -> bool:
    if not hasattr(ctypes, "windll"):
        raise OSError("Window messages are only available on Windows")
    post = ctypes.windll.user32.PostMessageW
    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    post.restype = wintypes.BOOL
    return bool(post(wintypes.HWND(hwnd), message, wparam, lparam))


def _native_scan_code(vk: int) -> int:
    if not hasattr(ctypes, "windll"):
        raise OSError("Window messages are only available on Windows")
    mapper = ctypes.windll.user32.MapVirtualKeyW
    mapper.argtypes = [wintypes.UINT, wintypes.UINT]
    mapper.restype = wintypes.UINT
    return int(mapper(vk, 0))


class WindowMessageInput:
    def __init__(
        self,
        get_window_handle: Callable[[], int],
        post_message: Callable[[int, int, int, int], bool | None] | None = None,
        scan_code: Callable[[int], int] | None = None,
    ) -> None:
        self.get_window_handle = get_window_handle
        self.post_message = post_message or _native_post_message
        self.scan_code = scan_code or _native_scan_code

    def _post(self, message: int, wparam: int, lparam: int) -> None:
        hwnd = int(self.get_window_handle() or 0)
        if not hwnd:
            raise RuntimeError("The game window is no longer available")
        result = self.post_message(hwnd, message, wparam, lparam)
        if result is False:
            raise OSError(f"Could not post Windows message 0x{message:04X} to the game window")

    def _key(self, key: str, *, down: bool) -> None:
        vk = virtual_key_code(key)
        scan_code = self.scan_code(vk)
        extended = vk in _EXTENDED_KEYS
        self._post(
            WM_KEYDOWN if down else WM_KEYUP,
            vk,
            pack_key_lparam(scan_code, key_down=down, extended=extended),
        )

    def key_down(self, key: str) -> None:
        self._key(key, down=True)

    def key_up(self, key: str) -> None:
        self._key(key, down=False)

    def mouse_move(self, x: int, y: int, buttons: int = 0) -> None:
        self._post(WM_MOUSEMOVE, buttons, pack_mouse_lparam(x, y))

    def mouse_down(self, x: int, y: int, button: str) -> None:
        try:
            message, _release, flag = _MOUSE_BUTTONS[button.lower()]
        except KeyError as exc:
            raise ValueError(f"Unsupported mouse button: {button!r}") from exc
        self._post(message, flag, pack_mouse_lparam(x, y))

    def mouse_up(self, x: int, y: int, button: str) -> None:
        try:
            _press, message, _flag = _MOUSE_BUTTONS[button.lower()]
        except KeyError as exc:
            raise ValueError(f"Unsupported mouse button: {button!r}") from exc
        self._post(message, 0, pack_mouse_lparam(x, y))

    def click(self, x: int, y: int, button: str) -> None:
        self.mouse_move(x, y)
        self.mouse_down(x, y, button)
        self.mouse_up(x, y, button)
