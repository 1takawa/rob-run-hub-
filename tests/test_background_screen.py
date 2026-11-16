"""Background capture and focus gating for the game window."""

from __future__ import annotations

import sys
import ctypes
import os
import unittest
from pathlib import Path
from unittest.mock import Mock
from ctypes import wintypes

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.screen import ScreenService  # noqa: E402


class BackgroundScreenTests(unittest.TestCase):
    @staticmethod
    def screen(*, background_control: bool, visible: bool = True, minimized: bool = False) -> ScreenService:
        screen = ScreenService.__new__(ScreenService)
        screen.background_control = background_control
        screen._user32 = Mock()
        screen._user32.IsWindowVisible.return_value = visible
        screen._user32.IsIconic.return_value = minimized
        screen.target_window_handle = Mock(return_value=0x1234)
        screen.is_target_foreground = Mock(return_value=False)
        return screen

    def test_background_window_is_automatable_while_another_app_has_focus(self) -> None:
        screen = self.screen(background_control=True)

        self.assertTrue(screen.is_automation_ready())
        screen.is_target_foreground.assert_not_called()

    def test_minimized_game_is_not_automatable_in_background_mode(self) -> None:
        screen = self.screen(background_control=True, minimized=True)

        self.assertFalse(screen.is_automation_ready())

    def test_ordinary_mode_still_requires_game_focus(self) -> None:
        screen = self.screen(background_control=False)

        self.assertFalse(screen.is_automation_ready())
        screen.is_target_foreground.assert_called_once_with()

    def test_background_capture_returns_target_window_frame(self) -> None:
        screen = self.screen(background_control=True)
        expected = np.zeros((12, 20, 3), dtype=np.uint8)
        screen.is_automation_ready = Mock(return_value=True)
        screen._capture_background_window = Mock(return_value=expected)

        actual = screen.capture()

        self.assertIs(actual, expected)
        screen._capture_background_window.assert_called_once_with()

    def test_window_search_excludes_the_helper_process_even_when_its_title_matches(self) -> None:
        class NativeFunction:
            def __init__(self, callback):
                self.callback = callback

            def __call__(self, *args):
                return self.callback(*args)

        handles = (101, 202, 303)
        titles = {101: "ROB RUN", 202: "ROB RUN", 303: "Hidden pool — rob run edition"}

        def enum_windows(callback, _lparam):
            for handle in handles:
                callback(handle, 0)
            return True

        def get_process_id(hwnd, output):
            handle = int(getattr(hwnd, "value", hwnd) or 0)
            process_id = os.getpid() if handle == 101 else os.getpid() + 1
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD)).contents.value = process_id
            return 1

        screen = ScreenService.__new__(ScreenService)
        screen.target_title_match = "rob run"
        screen._user32 = Mock()
        screen._user32.EnumWindows = NativeFunction(enum_windows)
        screen._user32.IsWindowVisible = NativeFunction(lambda _hwnd: True)
        screen._user32.GetWindowThreadProcessId = NativeFunction(get_process_id)

        with unittest.mock.patch.object(ScreenService, "_window_title", side_effect=lambda hwnd: titles[int(hwnd)]):
            windows = screen._visible_windows()

        self.assertEqual(windows, [(202, "ROB RUN")])


if __name__ == "__main__":
    unittest.main()
