import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.loadout_vision import class_button, find_confirm_buttons  # noqa: E402

SCREEN = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "loadout_screen.jpg")), cv2.COLOR_BGR2RGB)
LIME = [True, True, True, False, False]   # Патрульный, Сержант I, Сержант II available; Лейтенант I, Капитан not
CENTRES = [(235, 802), (543, 802), (851, 802), (1159, 802), (1467, 802)]   # on the screen as it was captured


def stretched(width: int, height: int) -> tuple[np.ndarray, float, float, float, float]:
    """The whole screen stretched to the window."""
    return cv2.resize(SCREEN, (width, height), interpolation=cv2.INTER_AREA), width / SCREEN.shape[1], height / SCREEN.shape[0], 0, 0


def letterboxed(width: int, height: int) -> tuple[np.ndarray, float, float, float, float]:
    """The screen scaled to fit and centred, black bars around."""
    scale = min(width / SCREEN.shape[1], height / SCREEN.shape[0])
    inner = cv2.resize(SCREEN, (round(SCREEN.shape[1] * scale), round(SCREEN.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((height, width, 3), np.uint8)
    left, top = (width - inner.shape[1]) // 2, (height - inner.shape[0]) // 2
    canvas[top:top + inner.shape[0], left:left + inner.shape[1]] = inner
    return canvas, scale, scale, left, top


class LoadoutScreenTests(unittest.TestCase):
    def check(self, frame, sx, sy, dx, dy) -> None:
        row = find_confirm_buttons(frame)
        self.assertEqual([b.active for b in row], LIME)
        for index, (cx, cy) in enumerate(CENTRES):
            button = class_button(frame, index)
            self.assertIsNotNone(button)
            x, y = button.center
            self.assertLessEqual(abs(x - (cx * sx + dx)), 0.01 * frame.shape[1] + 2)
            self.assertLessEqual(abs(y - (cy * sy + dy)), 0.01 * frame.shape[0] + 2)

    def test_the_screen_as_captured(self) -> None:
        self.check(SCREEN, 1.0, 1.0, 0, 0)

    def test_every_window_size_and_aspect(self) -> None:
        for name, make, size in [("1920x1080 stretched", stretched, (1920, 1080)), ("1280x1024 stretched", stretched, (1280, 1024)),
                                 ("1080x1080 stretched", stretched, (1080, 1080)), ("1280x720 stretched", stretched, (1280, 720)),
                                 ("1080x1080 letterboxed", letterboxed, (1080, 1080)), ("1280x1024 letterboxed", letterboxed, (1280, 1024)),
                                 ("2560x1440 stretched", stretched, (2560, 1440)), ("960x540 stretched", stretched, (960, 540))]:
            with self.subTest(name):
                self.check(*make(*size))

    def test_other_screens_have_no_such_row(self) -> None:
        plain = np.full((1024, 1280, 3), 45, np.uint8)
        self.assertEqual(find_confirm_buttons(plain), [])
        self.assertIsNone(class_button(plain, 0))
        bars = np.zeros((1080, 1920, 3), np.uint8)   # a loading screen: black everywhere
        self.assertEqual(find_confirm_buttons(bars), [])
        game = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "live_on_porch.jpg")), cv2.COLOR_BGR2RGB)
        self.assertEqual(find_confirm_buttons(game), [])

    def test_all_classes_unavailable_or_all_available_are_still_five_buttons(self) -> None:
        for lime_count in (0, 5):
            frame = SCREEN.copy()
            for index, (cx, cy) in enumerate(CENTRES):
                lime = index < lime_count
                frame[781:823, cx - 136:cx + 137] = (198, 251, 51) if lime else (0, 0, 0)
            with self.subTest(lime=lime_count):
                row = find_confirm_buttons(frame)
                self.assertEqual(len(row), 5)
                self.assertEqual([b.active for b in row], [i < lime_count for i in range(5)])


class ReaderUsesTheButtons(unittest.TestCase):
    def test_the_reader_confirms_the_selected_class_on_any_size(self) -> None:
        from harvest_assistant.autocop import AutocopScreenReader

        reader = AutocopScreenReader()
        with mock.patch.object(AutocopScreenReader, "_ocr", new=lambda self, data: None), mock.patch("asyncio.run", return_value=[]):
            for size in [(1920, 1080), (1280, 1024), (1080, 1080)]:
                frame, sx, sy, _dx, _dy = stretched(*size)
                with self.subTest(size=size):
                    wanted = reader.read(frame, "Сержант II")
                    self.assertTrue(wanted.class_screen and wanted.confirm_active)
                    left, top, w, h = wanted.confirm_bounds
                    self.assertAlmostEqual(left + w / 2, 851 * sx, delta=0.01 * size[0])
                    self.assertAlmostEqual(top + h / 2, 802 * sy, delta=0.01 * size[1])
                    unavailable = reader.read(frame, "Капитан")
                    self.assertTrue(unavailable.class_screen)
                    self.assertFalse(unavailable.confirm_active)   # a black button: not pressed



class RunnerLoop(unittest.TestCase):
    """E the moment the service button is lit, the confirm button the moment the class screen is up, round and round
    until the game says the player is a cop (the message is not known yet: here a stand-in)."""

    def setUp(self) -> None:
        self.feeds: list[dict] = []

    def run_script(self, script: list[str], markers: tuple = ()):
        from harvest_assistant import autocop
        from harvest_assistant.autocop import AutocopRunner, ServiceView

        blank = np.full((1024, 1280, 3), 45, np.uint8)
        frames = {"class": stretched(1280, 1024)[0]}
        log: list = []
        steps = iter(script + ["stop"])
        current = {"kind": "idle"}

        class Screen:
            background_control = True

            def capture(self_inner):
                kind = next(steps)
                current["kind"] = kind
                if kind == "stop":
                    runner.stop_event.set()
                return frames.get(kind, blank)

        class Driver:
            def __init__(self_inner, *_a): pass
            def play_key(self_inner, key, hold): log.append(("key", key))
            def click_at(self_inner, x, y, button): log.append(("click", x, y))
            def release_all(self_inner): pass
            def stop(self_inner): pass

        def view(_frame, _cls):
            kind = current["kind"]
            return ServiceView("Вы успешно стали копом" if kind == "done" else "", kind == "service", kind == "service",
                               False, False, None, kind == "full", False)

        with mock.patch.object(autocop, "InputDriver", Driver), mock.patch.object(autocop, "SUCCESS_MARKERS", markers), \
                mock.patch.object(autocop.AutocopScreenReader, "read_tokens", lambda self, frame, button: "19/1"):
            finished: list = []
            runner = AutocopRunner(Screen(), "Сержант II", lambda _t: None, finished.append, on_feed=self.feeds.append, hotkey="F7")
            runner.TICK, runner.PRESS_GAP, runner.CLICK_GAP = 0.0, 0.0, 0.0
            runner.reader.read = view
            runner._run()
        return log, finished

    def test_e_then_confirm_at_once_and_again_until_stopped(self) -> None:
        import time

        log, finished = self.run_script(["idle", "service", "class", "class", "service", "class"])
        kinds = [entry[0] for entry in log]
        self.assertEqual(kinds[:2], ["key", "click"])            # E on the lit button, the confirm button right after
        self.assertTrue(finished and "остановлен" in finished[0])   # no success message known yet: runs until stopped
        x, y = log[1][1:]
        self.assertAlmostEqual(x, 851 * 1280 / 1908, delta=14)
        self.assertAlmostEqual(y, 802 * 1024 / 1057, delta=10)

    def test_it_goes_round_again_when_the_confirm_did_not_take(self) -> None:
        import time

        slow = ["service"] + ["class"] * 30 + ["service"]        # the class screen stays: clicks repeat, then E again
        log, _finished = self.run_script(slow)
        kinds = [entry[0] for entry in log]
        self.assertEqual(kinds[0], "key")
        self.assertIn("click", kinds)
        self.assertEqual(kinds[-1], "key")

    def test_it_stops_by_itself_when_the_success_message_shows(self) -> None:
        log, finished = self.run_script(["service", "class", "done", "done", "service"], markers=("успешностали",))
        self.assertEqual([e[0] for e in log], ["key", "click"])
        self.assertTrue(finished and finished[0].startswith("Готово"))


    def test_the_overlay_gets_attempts_refusals_the_step_and_the_result(self) -> None:
        script = ["service", "full", "full", "idle", "service", "class", "done", "done"]
        self.run_script(script, markers=("успешностали",))
        last = self.feeds[-1]
        self.assertEqual(last["state"], "done")
        self.assertEqual(last["step"], "done")
        self.assertEqual(last["tries"], 2)                 # two E presses
        self.assertEqual(last["refusals"], 1)              # «слишком много копов» on two looks in a row is one refusal
        self.assertEqual(last["class"], "Сержант II")
        self.assertEqual(last["hotkey"], "F7")
        self.assertIsNotNone(last["ended_at"])
        self.assertTrue(last["active"])
        self.assertIn("refusal", [f["message_kind"] for f in self.feeds])
        self.assertIn("confirm", [f["step"] for f in self.feeds])


    def test_started_while_already_on_duty_it_presses_nothing_and_counts_the_time_until_stopped(self) -> None:
        import threading

        from harvest_assistant import autocop

        stopper: list = []
        original = autocop.AutocopRunner._publish

        def publish(runner, force=False):
            original(runner, force)
            if runner.state == "duty" and not stopper:
                stopper.append(threading.Timer(0.3, runner.stop_event.set))
                stopper[0].start()

        with mock.patch.object(autocop.AutocopRunner, "_publish", publish):
            log, finished = self.run_script(["done", "done", "service", "class"], markers=("успешностали",))
        self.assertEqual(log, [])                                  # no E, no click
        self.assertEqual(self.feeds[-1]["state"], "duty")
        self.assertIsNone(self.feeds[-1]["ended_at"])              # the time keeps running
        self.assertTrue(finished and "остановлен" in finished[0])


    def test_the_blue_police_counter_alone_means_on_duty(self) -> None:
        import threading

        from harvest_assistant import autocop

        cop = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "cop_started.jpg")), cv2.COLOR_BGR2RGB)
        stopper: list = []
        original = autocop.AutocopRunner._publish

        def publish(runner, force=False):
            original(runner, force)
            if runner.state == "duty" and not stopper:
                stopper.append(threading.Timer(0.3, runner.stop_event.set))
                stopper[0].start()

        real = autocop.duty_badge
        with mock.patch.object(autocop.AutocopRunner, "_publish", publish),                 mock.patch.object(autocop, "duty_badge", lambda frame: real(cop)):   # the counter is on every frame
            log, _finished = self.run_script(["idle", "idle", "service"])            # no menu in front of the bot
        self.assertEqual(log, [])
        self.assertEqual(self.feeds[-1]["state"], "duty")


class PanelClock(unittest.TestCase):
    def test_hours_only_once_there_are_any(self) -> None:
        sys.path.insert(0, str(ROOT / "overlay"))
        import autocop_panel

        self.assertEqual(autocop_panel.clock_parts(24 * 60 + 19), [("24", "м"), ("19", "с")])
        self.assertEqual(autocop_panel.clock_parts(3725), [("1", "ч"), ("02", "м"), ("05", "с")])
        self.assertEqual(autocop_panel.clock_parts(5), [("0", "м"), ("05", "с")])
        self.assertEqual(autocop_panel.elapsed({"started_at": 100.0, "ended_at": 160.0}), 60)   # frozen when done


class Tokens(unittest.TestCase):
    def test_what_the_class_costs_is_read_over_its_button_on_any_size(self) -> None:
        from harvest_assistant.autocop import AutocopScreenReader

        reader = AutocopScreenReader()
        for size in [(1908, 1057), (1920, 1080), (1280, 1024)]:
            frame = stretched(*size)[0]
            with self.subTest(size=size):
                try:
                    first = reader.read_tokens(frame, class_button(frame, 0))
                except Exception as exc:   # no Windows text recognition here
                    self.skipTest(str(exc))
                self.assertEqual(first, "бесплатно")
                self.assertEqual(reader.read_tokens(frame, class_button(frame, 1)), "19/1")


class DutyBadge(unittest.TestCase):
    def test_the_counter_of_a_cop_is_found_on_any_size_and_nowhere_else(self) -> None:
        from harvest_assistant.loadout_vision import duty_badge

        cop = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "cop_started.jpg")), cv2.COLOR_BGR2RGB)
        for size in [(1920, 1080), (1280, 1024), (1080, 1080), (1280, 720), (2560, 1440)]:
            with self.subTest(size=size):
                self.assertTrue(duty_badge(cv2.resize(cop, size, interpolation=cv2.INTER_AREA)))
        for name in ("loadout_screen.jpg", "live_on_porch.jpg", "live_at_house_door.jpg", "live_inside_gate.jpg"):
            with self.subTest(name=name):
                frame = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / name)), cv2.COLOR_BGR2RGB)
                self.assertFalse(duty_badge(frame))


class SuccessMessage(unittest.TestCase):
    def test_the_counter_menu_of_a_cop_is_recognised_in_the_real_screenshot(self) -> None:
        from harvest_assistant.autocop import AutocopScreenReader, SUCCESS_MARKERS

        frame = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "cop_started.jpg")), cv2.COLOR_BGR2RGB)
        try:
            view = AutocopScreenReader().read(frame, "Сержант II")
        except Exception as exc:   # no Windows text recognition here
            self.skipTest(str(exc))
        text = AutocopScreenReader._normalize(view.text)
        self.assertTrue(any(marker in text for marker in SUCCESS_MARKERS))
        self.assertFalse(view.service_visible)      # «Начать службу» is gone: it reads «Завершить службу» now


if __name__ == "__main__":
    unittest.main()


class Memory(unittest.TestCase):
    def test_costs_and_the_last_result_survive_a_restart(self) -> None:
        import tempfile

        from harvest_assistant.autocop import load_memory, save_memory

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "autocop.json"
            self.assertEqual(load_memory(path), {})
            save_memory(path, {"tokens": {"Сержант II": "19/1"}, "last": {"class": "Сержант II", "tries": 131, "seconds": 1482}})
            self.assertEqual(load_memory(path)["tokens"]["Сержант II"], "19/1")
            self.assertEqual(load_memory(path)["last"]["tries"], 131)


class DarkAfterClick(unittest.TestCase):
    def test_a_button_dark_for_a_moment_after_the_click_is_not_no_tokens(self) -> None:
        from harvest_assistant import autocop
        from harvest_assistant.autocop import AutocopRunner, ServiceView

        lit = stretched(1280, 1024)[0]
        dark = lit.copy()
        dark[740:790, 380:560] = (0, 0, 0)          # the Сержант II button of this size goes dark (pressed)
        frames = iter([lit, dark, dark, lit, None])
        told: list = []

        class Screen:
            background_control = True

            def capture(self_inner):
                frame = next(frames)
                if frame is None:
                    runner.stop_event.set()
                    return np.full((1024, 1280, 3), 45, np.uint8)
                return frame

        class Driver:
            def __init__(self_inner, *_a): pass
            def play_key(self_inner, *_a): pass
            def click_at(self_inner, *_a): pass
            def release_all(self_inner): pass
            def stop(self_inner): pass

        with mock.patch.object(autocop, "InputDriver", Driver), \
                mock.patch.object(autocop.AutocopScreenReader, "read_tokens", lambda self, frame, button: None):
            runner = AutocopRunner(Screen(), "Сержант II", lambda _t: None, lambda _t: None, on_tokens=told.append)
            runner.TICK = 0.0
            runner.reader.read = lambda frame, cls: ServiceView("", False, False, False, False, None, False, False)
            runner._run()
        self.assertNotIn({"Сержант II": "0"}, told)
        self.assertNotEqual(runner.tokens, "0")
