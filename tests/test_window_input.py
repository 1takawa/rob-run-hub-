"""Behavior checks for input routed to a game window without changing focus."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.window_input import (  # noqa: E402
    WM_KEYDOWN,
    WM_KEYUP,
    WM_MOUSEMOVE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WindowMessageInput,
    pack_key_lparam,
    pack_mouse_lparam,
)


class WindowInputTests(unittest.TestCase):
    def test_key_messages_keep_scan_code_and_mark_release_bits(self) -> None:
        self.assertEqual(pack_key_lparam(0x11, key_down=True), 0x00110001)
        self.assertEqual(pack_key_lparam(0x11, key_down=False), 0xC0110001)

    def test_mouse_coordinates_are_packed_as_client_coordinates(self) -> None:
        self.assertEqual(pack_mouse_lparam(48, 32), 0x00200030)

    def test_key_and_click_messages_are_posted_to_the_bound_window(self) -> None:
        posted: list[tuple[int, int, int, int]] = []
        sender = WindowMessageInput(
            get_window_handle=lambda: 0x1234,
            post_message=lambda hwnd, message, wparam, lparam: posted.append(
                (hwnd, message, wparam, lparam)
            ),
            scan_code=lambda _vk: 0x11,
        )

        sender.key_down("w")
        sender.key_up("w")
        sender.click(48, 32, "left")

        self.assertEqual(
            posted,
            [
                (0x1234, WM_KEYDOWN, ord("W"), 0x00110001),
                (0x1234, WM_KEYUP, ord("W"), 0xC0110001),
                (0x1234, WM_MOUSEMOVE, 0, 0x00200030),
                (0x1234, WM_LBUTTONDOWN, 1, 0x00200030),
                (0x1234, WM_LBUTTONUP, 0, 0x00200030),
            ],
        )


if __name__ == "__main__":
    unittest.main()
