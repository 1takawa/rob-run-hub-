"""The background driver must not move or type into the user's foreground app."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.input_driver import InputDriver  # noqa: E402
from harvest_assistant.models import RouteEvent  # noqa: E402


class FakeScreen:
    background_control = True

    def is_automation_ready(self) -> bool:
        return True

    def is_target_foreground(self) -> bool:
        return False

    def target_client_size(self) -> tuple[int, int]:
        return 100, 80

    def contains_game_point(self, x: int, y: int) -> bool:
        return 0 <= x < 100 and 0 <= y < 80


class FakeWindowInput:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def key_down(self, key: str) -> None:
        self.calls.append(("key_down", key))

    def key_up(self, key: str) -> None:
        self.calls.append(("key_up", key))

    def mouse_move(self, x: int, y: int, buttons: int = 0) -> None:
        self.calls.append(("mouse_move", x, y, buttons))

    def mouse_down(self, x: int, y: int, button: str) -> None:
        self.calls.append(("mouse_down", x, y, button))

    def mouse_up(self, x: int, y: int, button: str) -> None:
        self.calls.append(("mouse_up", x, y, button))


class BackgroundDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window_input = FakeWindowInput()
        self.driver = InputDriver(
            FakeScreen(),
            threading.Event(),
            window_input=self.window_input,
        )

    def test_route_keys_are_sent_to_the_game_window_when_it_is_not_foreground(self) -> None:
        with patch("harvest_assistant.input_driver.pydirectinput.keyDown") as global_key:
            self.driver.play_route_event(RouteEvent(0, "key_down", "w"))

        self.assertEqual(self.window_input.calls, [("key_down", "w")])
        global_key.assert_not_called()

    def test_relative_mouse_moves_only_the_window_message_cursor(self) -> None:
        with patch("harvest_assistant.input_driver.pydirectinput.moveRel") as global_move:
            self.driver.move_relative(5, -3)

        self.assertEqual(self.window_input.calls, [("mouse_move", 55, 37, 0)])
        global_move.assert_not_called()

    def test_click_targets_client_coordinates_without_moving_the_desktop_cursor(self) -> None:
        with patch("harvest_assistant.input_driver.pydirectinput.click") as global_click:
            self.driver.click_at(20, 15, "left")

        self.assertEqual(
            self.window_input.calls,
            [
                ("mouse_move", 20, 15, 0),
                ("mouse_down", 20, 15, "left"),
                ("mouse_up", 20, 15, "left"),
            ],
        )
        global_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
