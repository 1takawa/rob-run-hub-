"""Behavior checks for the experimental screen-guided route."""

from __future__ import annotations

import sys
import math
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant.visual_route import (  # noqa: E402
    LandmarkMatcher,
    LandmarkMatch,
    PromptReader,
    VisualRouteError,
    VisualRouteTestRunner,
    choose_movement,
    normalize_prompt,
)


class LandmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assets = ROOT / "src" / "harvest_assistant" / "visual_assets"
        self.fixtures = ROOT / "tests" / "fixtures"

    def test_container_seen_from_two_distances(self) -> None:
        """Changing apparent size must not turn the container into a missing target."""
        matcher = LandmarkMatcher(self.assets)
        frame = np.asarray(Image.open(self.fixtures / "container_approach.png").convert("RGB"))
        result = matcher.detect(frame, "container_far")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result.inliers, 7)
        self.assertTrue(100 < result.center_x < 440)
        self.assertTrue(70 < result.center_y < 280)

    def test_unrelated_room_is_not_container(self) -> None:
        matcher = LandmarkMatcher(self.assets)
        frame = np.asarray(Image.open(self.fixtures / "home_spawn.png").convert("RGB"))
        self.assertIsNone(matcher.detect(frame, "container_far"))

    def test_container_is_found_in_full_game_frame(self) -> None:
        matcher = LandmarkMatcher(self.assets)
        frame = np.asarray(Image.open(self.fixtures / "container_approach_full.png").convert("RGB"))
        result = matcher.detect(frame, "container_far")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(280 < result.center_x < 620)

    def test_house_door_is_not_mistaken_for_container_door(self) -> None:
        matcher = LandmarkMatcher(self.assets)
        frame = np.asarray(Image.open(self.fixtures / "house_exit_full.png").convert("RGB"))
        self.assertIsNone(matcher.detect(frame, "container_door"))


class MovementTests(unittest.TestCase):
    def test_turns_toward_target_before_walking(self) -> None:
        self.assertEqual(choose_movement(170, 1200), "left")
        self.assertEqual(choose_movement(1030, 1200), "right")
        self.assertEqual(choose_movement(610, 1200), "forward")

    def test_prompts_accept_ocr_variants_but_not_wrong_action(self) -> None:
        self.assertEqual(normalize_prompt("Открьпь"), "open")
        self.assertEqual(normalize_prompt("Открыть"), "open")
        self.assertEqual(normalize_prompt("Закрыть"), "close")
        self.assertIsNone(normalize_prompt("Сбор урожая"))

    def test_window_prompt_is_not_a_door(self) -> None:
        """«ОКНО / Закрыть» has the same line as a door: the bot once pressed E at a window and stood still."""
        from harvest_assistant.visual_route import is_window_text
        self.assertTrue(is_window_text("ОКНО"))
        self.assertTrue(is_window_text("0КНО"))
        self.assertFalse(is_window_text("ДВЕРЬ"))
        self.assertFalse(is_window_text("Закрыть"))
        frame = np.asarray(Image.open(ROOT / "tests" / "fixtures" / "window_prompt_full.png").convert("RGB"))
        self.assertIsNone(PromptReader().read(frame))

    def test_door_is_seen_by_colour_and_shape(self) -> None:
        from harvest_assistant.door_vision import find_door
        fixtures = ROOT / "tests" / "fixtures"

        def load(name: str) -> np.ndarray:
            return np.asarray(Image.open(fixtures / name).convert("RGB"))

        near = find_door(load("house_door_closeup.png"), "house")
        self.assertIsNotNone(near)
        self.assertGreater(near.height, 0.6)
        edge = find_door(load("house_door_at_edge.jpg"), "house")  # the door at the right edge, a window in the middle
        self.assertIsNotNone(edge)
        self.assertGreater(edge.center_x, 0.8)
        self.assertTrue(edge.cut)
        self.assertIsNone(find_door(load("house_window_far.jpg"), "house"))   # only a window and a table
        self.assertIsNone(find_door(load("window_prompt_full.png"), "house"))
        self.assertIsNone(find_door(load("home_spawn.png"), "house"))
        self.assertIsNone(find_door(load("plantation_ready.jpg"), "house"))

    def test_door_in_the_shade_of_the_porch_is_seen(self) -> None:
        """From the porch the open door is dark: the bot looked straight at it and did not see it."""
        from harvest_assistant.door_vision import find_door
        fixtures = ROOT / "tests" / "fixtures"
        shade = np.asarray(Image.open(fixtures / "porch_door_shade.jpg").convert("RGB"))
        self.assertIsNone(find_door(shade, "house"))        # the light profile does not fit the shaded door
        seen = find_door(shade, "porch")
        self.assertIsNotNone(seen)
        self.assertTrue(0.7 < seen.center_x < 0.9)
        self.assertGreater(seen.height, 0.5)
        self.assertIsNone(find_door(np.asarray(Image.open(fixtures / "porch_no_door.jpg").convert("RGB")), "porch"))

    def test_prompt_counts_as_door_only_with_the_door_title(self) -> None:
        from harvest_assistant.visual_route import is_door_title
        self.assertTrue(is_door_title("ДВЕРЬ"))
        self.assertTrue(is_door_title("дверь"))
        self.assertFalse(is_door_title("ОКНО"))
        fixtures = ROOT / "tests" / "fixtures"
        strict = PromptReader(require_door=True)
        window = np.asarray(Image.open(fixtures / "window_prompt_full.png").convert("RGB"))
        door = np.asarray(Image.open(fixtures / "house_exit_full.png").convert("RGB"))
        gate = np.asarray(Image.open(fixtures / "container_door_full.png").convert("RGB"))
        self.assertIsNone(strict.read(window))
        self.assertEqual(strict.read(door), "close")
        self.assertEqual(strict.read(gate), "open")

    def test_reads_actual_container_door_prompts(self) -> None:
        reader = PromptReader()
        fixtures = ROOT / "tests" / "fixtures"
        open_crop = np.asarray(Image.open(fixtures / "container_door_prompt.png").convert("RGB"))
        close_crop = np.asarray(Image.open(fixtures / "container_close_prompt.png").convert("RGB"))
        self.assertEqual(reader.read_crop(open_crop), "open")
        self.assertEqual(reader.read_crop(close_crop), "close")


class FakeScreen:
    def capture(self) -> np.ndarray:
        return np.zeros((600, 800, 3), dtype=np.uint8)


class FakeDriver:
    def __init__(self) -> None:
        self.actions: list[tuple] = []

    def move_relative(self, dx: int, dy: int) -> None:
        self.actions.append(("turn", dx, dy))

    def play_key(self, key: str, duration: float) -> None:
        self.actions.append(("key", key, duration))

    def wait(self, _duration: float) -> None:
        pass


class FakeMatcher:
    def __init__(self, matches: list[LandmarkMatch | None]) -> None:
        self.matches = iter(matches)

    def detect(self, _frame: np.ndarray, _name: str) -> LandmarkMatch | None:
        return next(self.matches, None)


class FakePrompt:
    def __init__(self, prompts: list[str | None]) -> None:
        self.prompts = iter(prompts)

    def read(self, _frame: np.ndarray) -> str | None:
        return next(self.prompts, None)


class ApproachDoorTests(unittest.TestCase):
    def test_turns_to_the_door_and_walks_up_to_it_before_pressing_e(self) -> None:
        """The bot stood at a window, the door was at the right edge: it must turn right, walk, and press E only when
        the game shows the door prompt."""
        driver = FakeDriver()
        driver.play_route_event = lambda event: driver.actions.append(("event", event.kind, event.key))
        door_right = LandmarkMatch(700, 300, 130, 300, 30)   # 800 px wide picture: right of the middle
        door_front = LandmarkMatch(400, 300, 260, 500, 30)
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([door_right, door_front, door_front, door_front]),
            prompt_reader=FakePrompt([None, None, None, None, "open"]),
        )
        runner._hold = lambda key, seconds: driver.actions.append(("hold", key, seconds))
        self.assertTrue(runner._approach_door("open", "container"))
        kinds = [item[0] for item in driver.actions]
        self.assertEqual(kinds[0], "turn")
        self.assertGreater(driver.actions[0][1], 0)           # to the right
        self.assertIn("hold", kinds)
        self.assertEqual(driver.actions[-1][:2], ("key", "e"))


class RunnerSafetyTests(unittest.TestCase):
    def test_wrong_state_on_target_door_stops_before_walking(self) -> None:
        driver = FakeDriver()
        centered = LandmarkMatch(400, 300, 130, 300, 13)
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([centered]),
            prompt_reader=FakePrompt(["close"]),
        )
        with self.assertRaises(VisualRouteError):
            runner.seek("house_door", "open", max_steps=1)
        self.assertEqual(driver.actions, [])

    def test_wrong_door_prompt_cannot_press_e(self) -> None:
        driver = FakeDriver()
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, prompt_reader=FakePrompt(["close"]),
        )
        with self.assertRaises(VisualRouteError):
            runner.interact("open")
        self.assertEqual(driver.actions, [])

    def test_absent_landmark_stops_without_walking(self) -> None:
        driver = FakeDriver()
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([None, None, None]),
            prompt_reader=FakePrompt([None] * 4),
        )
        with self.assertRaises(VisualRouteError):
            runner.seek("house_door", "open", max_steps=3)
        self.assertEqual([item[0] for item in driver.actions], ["turn", "turn", "turn"])

    def test_route_rechecks_image_between_turn_and_forward_step(self) -> None:
        driver = FakeDriver()
        left = LandmarkMatch(100, 300, 100, 250, 12)
        centered = LandmarkMatch(400, 300, 130, 300, 13)
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([left, centered, centered]),
            prompt_reader=FakePrompt([None, None, "open"]),
        )
        runner.seek("house_door", "open", max_steps=3)
        self.assertEqual(driver.actions[0][0], "turn")
        self.assertLess(driver.actions[0][1], 0)
        self.assertEqual(driver.actions[1], ("key", "w", 0.18))


class FullRouteTests(unittest.TestCase):
    """The whole route on a scripted world: every landmark shows up the moment it is looked for."""

    class World(FakeMatcher):
        def __init__(self) -> None:
            self.seen: list[str] = []

        def detect(self, _frame: np.ndarray, name: str) -> LandmarkMatch | None:
            self.seen.append(name)
            # tall and centred: every stage is "reached" on its first look
            return LandmarkMatch(400, 300, 400, 500, 40)

    def test_route_follows_the_screenshots_in_order(self) -> None:
        driver = FakeDriver()
        world = self.World()
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=world, prompt_reader=FakePrompt(['open', 'open', 'close', 'close', 'open', 'open', 'close', 'close']),
        )
        runner.calibrate_turn = lambda: None  # no real camera here
        stages: list[str] = []
        original = runner._mark
        runner._mark = lambda name: (stages.append(name), original(name))[1]
        runner.run_route()
        self.assertEqual(stages, ["калибровка камеры", "выход из спавна", "дверь дома", "выход из дома", "ступеньки крыльца",
                                  "контейнер", "дверь контейнера", "спуск к столам"])
        # the first thing the route does after spawning is to walk forward, then turn right: no scanning on the spot
        first_keys = [action for action in driver.actions[:8]]
        self.assertTrue(all(action[:2] == ("key", "w") for action in first_keys[:7]))
        self.assertEqual(driver.actions[7][0], "turn")
        self.assertGreater(driver.actions[7][1], 0)
        # E is pressed exactly four times: open, close, open, close
        self.assertEqual(sum(1 for action in driver.actions if action[:2] == ("key", "e")), 4)
        self.assertIn("grow_room", world.seen)
        self.assertIn("container_near", world.seen)


class RecordedPathTests(unittest.TestCase):
    class Driver(FakeDriver):
        def play_route_event(self, event) -> None:
            self.actions.append(("event", event.kind, event.value))

    @staticmethod
    def route():
        from harvest_assistant.models import RecordedRoute, RouteEvent
        return RecordedRoute(events=[
            RouteEvent(0.00, "key_down", "w"), RouteEvent(0.05, "key_up", "w"),
            RouteEvent(0.06, "interact", "open"),
            RouteEvent(0.08, "mouse_delta", (40, 0)),
            RouteEvent(0.10, "interact", "close"),
        ], duration_seconds=0.15)

    def runner(self, driver, prompts):
        return VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt(prompts),
        )

    def test_replays_events_and_presses_e_only_with_the_expected_prompt(self) -> None:
        driver = self.Driver()
        self.runner(driver, ["open", "close"]).run_recorded(self.route())
        kinds = [action[:2] for action in driver.actions]
        self.assertEqual(kinds.count(("key", "e")), 2)
        self.assertEqual([a for a in driver.actions if a[0] == "event"][0], ("event", "key_down", "w"))

    def test_door_already_in_the_wanted_state_is_left_alone(self) -> None:
        driver = self.Driver()
        # the door is already open ("close" is offered): no E, the path simply continues
        self.runner(driver, ["close", "close"]).run_recorded(self.route())
        self.assertEqual(sum(1 for a in driver.actions if a[:2] == ("key", "e")), 1)

    def test_no_prompt_means_no_blind_e(self) -> None:
        driver = self.Driver()
        with self.assertRaises(VisualRouteError):
            self.runner(driver, [None] * 20).run_recorded(self.route())
        self.assertFalse(any(a[:2] == ("key", "e") for a in driver.actions))

    def test_recorder_keeps_keys_camera_and_the_prompt_seen_at_each_e(self) -> None:
        from pynput import keyboard
        from harvest_assistant.approach import ApproachRecorder

        class Screen:
            def is_target_foreground(self): return True
            def capture(self): return np.zeros((10, 10, 3), np.uint8)

        recorder = ApproachRecorder(Screen(), FakePrompt(["open"]))
        recorder._recording = True
        recorder._start = __import__("time").monotonic()
        recorder._on_press(keyboard.KeyCode.from_vk(0x57))
        recorder._on_mouse(30, 2)
        recorder._on_release(keyboard.KeyCode.from_vk(0x57))
        recorder._on_press(keyboard.KeyCode.from_vk(0x45))
        for thread in recorder._ocr_threads:
            thread.join(2)
        kinds = [(e.kind, e.value) for e in recorder._events]
        self.assertIn(("key_down", "w"), kinds)
        self.assertIn(("key_up", "w"), kinds)
        self.assertIn(("interact", "open"), kinds)


class TaughtRunTests(unittest.TestCase):
    """Teach and repeat on four real frames: the bot must walk the recorded pieces in order."""

    FRAMES = ["home_spawn.png", "house_exit_full.png", "container_approach_full.png", "container_door_full.png"]

    def make_world(self):
        import tempfile
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtStore, jpeg_frame
        fixtures = ROOT / "tests" / "fixtures"
        images = [np.asarray(Image.open(fixtures / name).convert("RGB")) for name in self.FRAMES]
        store = TaughtStore(Path(tempfile.mkdtemp()))
        events = [
            RouteEvent(0.0, "key_down", "w"), RouteEvent(0.5, "key_up", "w"),
            RouteEvent(0.7, "mouse_delta", (60, 0)),
            RouteEvent(1.0, "key_down", "w"), RouteEvent(1.4, "key_up", "w"),
            RouteEvent(1.9, "interact", "open"),
        ]
        run = store.add_run(events, [(0.6 * i, jpeg_frame(image)) for i, image in enumerate(images)], 2.4)
        return images, run

    def test_walks_the_recorded_pieces_in_order_and_finishes_at_the_last_frame(self) -> None:
        images, run = self.make_world()
        self.assertEqual(len(run.keyframes), len(images))

        class World:
            def __init__(self) -> None:
                self.state = 0
                self.moved = False

            def capture(self):
                if self.moved:
                    self.state = min(self.state + 1, len(images) - 1)
                    self.moved = False
                return images[self.state]

        world = World()

        class Driver(RecordedPathTests.Driver):
            def play_route_event(self, event) -> None:
                world.moved = True
                super().play_route_event(event)

        driver = Driver()
        runner = VisualRouteTestRunner(
            world, driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt(["open"] * 5), taught_runs=[run],
        )
        runner.calibrate_turn = lambda: None
        runner.run_taught([run])
        self.assertEqual(world.state, len(images) - 1)
        pressed = [a for a in driver.actions if a[:2] == ("key", "e")]
        self.assertEqual(len(pressed), 1)  # the single recorded door action, verified by the prompt

    def test_door_without_prompt_is_approached_in_small_steps(self) -> None:
        images, run = self.make_world()

        class World:
            state = 0

            def __init__(self) -> None:
                self.moved = False

            def capture(self):
                if self.moved:
                    World.state = min(World.state + 1, len(images) - 1)
                    self.moved = False
                return images[World.state]

        world = World()

        class Driver(RecordedPathTests.Driver):
            def play_route_event(self, event) -> None:
                world.moved = True
                super().play_route_event(event)

        driver = Driver()
        # the prompt shows up only after three look-ups: the bot has to step closer meanwhile
        runner = VisualRouteTestRunner(
            world, driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt([None, None, None, "open"]),
            taught_runs=[run],
        )
        runner.calibrate_turn = lambda: None
        seen = []
        runner.vision_sink = seen.append
        runner.run_taught([run])
        self.assertEqual(sum(1 for a in driver.actions if a[:2] == ("key", "e")), 1)
        self.assertTrue(any("Подхожу" in str(item.get("status", "")) for item in seen))
        self.assertTrue(all("ts" in item for item in seen))

    def test_camera_turn_is_smooth_and_lands_where_asked(self) -> None:
        driver = RecordedPathTests.Driver()
        moves = []
        driver.move_relative = lambda dx, dy: moves.append((dx, dy))
        runner = VisualRouteTestRunner(
            FakeScreen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt([]),
        )
        runner.px_per_degree = 50.0
        runner.LOOK_SPEED = 600.0  # keep the test quick
        runner._look(20.0, 0.0)
        total = sum(dx for dx, _dy in moves)
        self.assertAlmostEqual(total, 20.0 * runner.TURN_GAIN * 50.0, delta=2)
        self.assertGreater(len(moves), 4)
        self.assertLess(max(abs(dx) for dx, _dy in moves), total * 0.4)  # no single jump

    def test_after_a_door_it_walks_through_turns_back_and_closes_it_without_recognising_anything(self) -> None:
        """The doorway case: nothing on screen matches the recording, only the door prompt is a landmark."""
        import tempfile
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtStore, jpeg_frame
        images = [np.asarray(Image.open(ROOT / "tests" / "fixtures" / name).convert("RGB")) for name in self.FRAMES]
        store = TaughtStore(Path(tempfile.mkdtemp()))
        events = [
            RouteEvent(0.0, "key_down", "w"), RouteEvent(0.5, "key_up", "w"),
            RouteEvent(1.9, "interact", "open"),
            RouteEvent(2.2, "key_down", "w"), RouteEvent(2.5, "key_up", "w"),
            RouteEvent(3.0, "mouse_delta", (6000, 0)),          # turned round to look back at the door (~120 deg)
            RouteEvent(4.0, "interact", "close"),
        ]
        run = store.add_run(events, [(0.6 * i, jpeg_frame(image)) for i, image in enumerate(images)], 4.2)

        class World:
            state = 0
            opened = False
            walked_after_open = 0
            turned = 0.0       # mouse counts

            def capture(self):
                if self.opened:
                    return np.zeros_like(images[0])             # in the doorway nothing is recognisable
                self.state = min(self.state + self.moved, len(images) - 1)
                self.moved = 0
                return images[self.state]

            moved = 0

        world = World()

        class Driver(RecordedPathTests.Driver):
            def play_route_event(self, event) -> None:
                super().play_route_event(event)
                if event.kind == "key_down" and event.value == "w":
                    if world.opened:
                        world.walked_after_open += 1
                    else:
                        world.moved = 1

            def move_relative(self, dx, dy) -> None:
                super().move_relative(dx, dy)
                world.turned += dx

        class Prompt:
            def read(self_inner, _frame):
                if not world.opened:
                    return "open" if world.state == len(images) - 1 else None
                if world.walked_after_open == 0:
                    return "close"                               # standing at the open door
                if world.walked_after_open < 2:
                    return "close"                               # still in the frame after the first step
                degrees = world.turned / runner.px_per_degree
                return "close" if degrees > 60 else None         # the door is behind us until we have turned round

        driver = Driver()
        runner = VisualRouteTestRunner(
            world, driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=Prompt(), taught_runs=[run],
        )
        presses = []
        original_play_key = driver.play_key

        def play_key(key, duration):
            original_play_key(key, duration)
            if key == "e":
                presses.append(len([a for a in driver.actions if a[:2] == ("key", "w") or (a[0] == "event" and a[1] == "key_down" and a[2] == "w")]))
                if len(presses) == 1:
                    world.opened = True
                    world.turned = 0.0

        driver.play_key = play_key
        runner.run_taught([run], stage=0)
        self.assertEqual(len(presses), 2)                       # open, then close: nothing else
        self.assertGreaterEqual(world.walked_after_open, 2)     # walked through until the prompt went away
        self.assertGreater(world.turned, 0)                     # and turned round to find the door again

    def test_a_long_step_is_one_continuous_walk_with_steering_while_the_key_stays_down(self) -> None:
        import tempfile
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtStore, jpeg_frame
        images = [np.asarray(Image.open(ROOT / "tests" / "fixtures" / name).convert("RGB")) for name in self.FRAMES]
        store = TaughtStore(Path(tempfile.mkdtemp()))
        events = [RouteEvent(0.0, "key_down", "w"), RouteEvent(1.4, "key_up", "w"), RouteEvent(1.9, "interact", "open")]
        run = store.add_run(events, [(0.6 * i, jpeg_frame(image)) for i, image in enumerate(images)], 2.2)
        # the second picture is shifted sideways: the heading is off and must be corrected on the move
        drifted = cv2.warpAffine(images[1], np.float32([[1, 0, 50], [0, 1, 0]]), (images[1].shape[1], images[1].shape[0]),
                                 borderMode=cv2.BORDER_REPLICATE)
        sequence = [images[0], images[0], drifted, drifted, images[2], images[2], images[3]]

        class World:
            calls = 0

            def capture(self):
                image = sequence[min(self.calls, len(sequence) - 1)]
                self.calls += 1
                return image

        driver = RecordedPathTests.Driver()
        runner = VisualRouteTestRunner(
            World(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt(["open"]), taught_runs=[run],
        )
        runner.run_taught([run], stage=0)
        kinds = [(a[0], a[1] if a[0] == "event" else None) for a in driver.actions]
        downs = [i for i, a in enumerate(driver.actions) if a[:3] == ("event", "key_down", "w")]
        ups = [i for i, a in enumerate(driver.actions) if a[:3] == ("event", "key_up", "w")]
        self.assertEqual(len(downs), 1)                         # pressed once for the whole step ...
        self.assertEqual(len(ups), 1)                           # ... and released once
        turns_while_walking = [i for i, a in enumerate(driver.actions) if a[0] == "turn" and downs[0] < i < ups[0]]
        self.assertTrue(turns_while_walking, kinds)             # the heading was corrected without stopping

    def test_unknown_place_never_walks(self) -> None:
        images, run = self.make_world()
        blank = np.zeros_like(images[0])

        class Screen:
            def capture(self):
                return blank

        driver = RecordedPathTests.Driver()
        runner = VisualRouteTestRunner(
            Screen(), driver, ROOT / "src" / "harvest_assistant" / "visual_assets",
            lambda _snapshot: None, matcher=FakeMatcher([]), prompt_reader=FakePrompt([]), taught_runs=[run],
        )
        runner.calibrate_turn = lambda: None
        with self.assertRaises(VisualRouteError):
            runner.run_taught([run])
        # the camera only wiggles around the heading it had; it never keeps turning one way
        running, widest = 0, 0
        for action in driver.actions:
            if action[0] == "turn":
                running += action[1]
                widest = max(widest, abs(running))
        self.assertLess(widest, 130 * 60)                       # well under a third of a full turn (at ~49 counts per degree)
        # only a sweep around and a short step back: never a step forward into the unknown
        self.assertFalse(any(a[0] == "event" and a[1] == "key_down" and a[2] != "s" for a in driver.actions))


class RouteMapTests(unittest.TestCase):
    @staticmethod
    def l_shaped_map():
        """Two runs along an L: 8 units north, then 8 units east (the straight line would cut the corner)."""
        from harvest_assistant.routemap import Node, Placement, RouteMap, Track

        class Plan:
            steps: list = []
            harvest_times: list = []

        class Run:
            def __init__(self, n):
                self.keyframes = [{"t": float(i)} for i in range(n)]
                self.events = []
                self.run_id = "x"

        points = [(0.0, float(i)) for i in range(9)] + [(float(i), 8.0) for i in range(1, 9)]
        track = Track([float(i) for i in range(len(points))], [p[0] for p in points], [p[1] for p in points],
                      [0.0] * len(points), [0.0] * len(points), [0.0] * len(points))
        return RouteMap([Run(len(points))], [Plan()], [track], [Placement()])

    def test_the_route_follows_the_way_that_was_walked_not_the_straight_line(self) -> None:
        route_map = self.l_shaped_map()
        route = route_map.route((0.0, 0.0), (8.0, 8.0))
        self.assertGreater(len(route), 10)
        corner = min(route, key=lambda p: math.hypot(p[0], p[1] - 8))
        self.assertLess(math.hypot(corner[0], corner[1] - 8), 1.0)   # it goes through the corner of the L
        self.assertEqual(route_map.route((0.0, 0.0), (8.0, 8.0))[-1], (8.0, 8.0))

    def test_the_navigator_walks_the_learned_way_to_the_goal_in_a_simulated_world(self) -> None:
        from harvest_assistant.navigator import MapNavigator
        route_map = self.l_shaped_map()
        bot = {"x": 0.0, "y": 0.0, "h": 90.0}   # starts facing east: it has to turn north first

        def pose():
            return bot["x"], bot["y"], bot["h"]

        def look(degrees):
            bot["h"] += degrees * 0.9            # the camera under-turns a little, like the real one

        def walk(seconds):
            a = math.radians(bot["h"])
            bot["x"] += seconds * math.sin(a)
            bot["y"] += seconds * math.cos(a)

        navigator = MapNavigator(route_map, pose, look, walk)
        self.assertEqual(navigator.goto((8.0, 8.0)), "arrived")
        # it went round the corner of the L instead of straight across (the straight line passes (4,4))
        self.assertTrue(all(math.hypot(x - 4.0, y - 4.0) > 2.0 for x, y, _ in navigator.trace))

    def test_with_no_badge_in_view_the_pilot_walks_over_the_map_to_the_known_table(self) -> None:
        from types import SimpleNamespace
        route_map = self.l_shaped_map()
        route_map.plans[0].harvest_times = [16.0]          # the author pressed E at a table at the end of the L
        world = VisionCycleTests.world()
        world.phase = "far"
        runner, _profile, table, _got, _driver = VisionCycleTests().build(world)
        runner._nav = SimpleNamespace(map=route_map, width=1280)
        bot = {"x": 0.0, "y": 0.0, "h": 90.0}
        runner._pose_now = lambda frame=None: (bot["x"], bot["y"], bot["h"])
        runner._look = lambda degrees, pitch=0.0: bot.__setitem__("h", bot["h"] + degrees * 0.9)

        def hold(key, seconds, shift=False):
            a = math.radians(bot["h"])
            bot["x"] += seconds * math.sin(a)
            bot["y"] += seconds * math.cos(a)

        runner._hold = hold
        self.assertEqual(runner._table_goals(), {})           # which table of the profile is where: not learned yet
        self.assertTrue(runner._go_to_known_table(set()))     # so it goes to the place where the tables stand
        area = route_map.table_area()
        self.assertLess(math.hypot(bot["x"] - area[0], bot["y"] - area[1]), 1.2)
        self.assertFalse(runner._go_to_known_table(set()))   # the same place is not walked to twice in one round
        runner._learned_goals[table.table_id] = (8.0, 0.0)    # after a harvest the bot knows where THIS table is
        self.assertEqual(list(runner._table_goals()), [table.table_id])

    def test_the_zoomed_radar_shows_only_the_plantation_past_the_last_door(self) -> None:
        route_map = self.l_shaped_map()
        plan = route_map.plans[0]
        plan.stages = 1
        plan.stage_of = [0] * 9 + [1] * 8                      # the first leg of the L is "before the gate", the second past it
        full, zoom = route_map.view(), route_map.view(zoom=True)
        self.assertIsNone(full["bounds"])
        self.assertIsNotNone(zoom["bounds"])
        self.assertEqual(zoom["title"], "ПЛАНТАЦИЯ")
        left, low, right, high = zoom["bounds"]
        self.assertGreater(right - left, 6.9)                    # never zoomed in more than a few cells
        shown = [p for path in zoom["paths"] for p in path]
        self.assertTrue(shown and all(p[1] >= 7.5 for p in shown))   # only the second leg (y = 8) is drawn

    def test_no_pose_means_lost_and_an_unknown_goal_means_no_way(self) -> None:
        from harvest_assistant.navigator import MapNavigator
        route_map = self.l_shaped_map()
        navigator = MapNavigator(route_map, lambda: None, lambda d: None, lambda s: None)
        self.assertEqual(navigator.goto((8.0, 8.0)), "lost")

    def test_the_map_of_real_runs_places_them_over_each_other_and_is_cached(self) -> None:
        import tempfile
        from harvest_assistant.routemap import build, align_by_pictures
        from harvest_assistant.taught import TaughtPlan, TaughtStore, jpeg_frame
        from harvest_assistant.models import RouteEvent
        fixtures = ROOT / "tests" / "fixtures"
        images = [np.asarray(Image.open(fixtures / name).convert("RGB")) for name in TaughtRunTests.FRAMES]
        store = TaughtStore(Path(tempfile.mkdtemp()))
        events = [RouteEvent(0.0, "key_down", "w"), RouteEvent(1.0, "key_up", "w"), RouteEvent(1.9, "interact", "open")]
        frames = [(0.6 * i, jpeg_frame(image)) for i, image in enumerate(images)]
        runs = [store.add_run(events, frames, 2.2), store.add_run(events, frames, 2.2)]
        plans = [TaughtPlan(run, 1280, 88.0) for run in runs]
        cache = Path(tempfile.mkdtemp()) / "map.json"
        first = build(runs, plans, 1280, 88.0, align_by_pictures(runs, plans, 1280, 88.0), cache)
        self.assertTrue(cache.exists())
        again = build(runs, plans, 1280, 88.0, None, cache)           # no matching needed the second time
        self.assertEqual(len(first.nodes), len(again.nodes))
        self.assertAlmostEqual(first.placements[1].dx, again.placements[1].dx, places=6)
        self.assertEqual(len(first.doors()), 1)


class RecordingTests(unittest.TestCase):
    def test_e_at_a_table_is_recorded_as_a_harvest_and_is_not_replayed_as_a_door(self) -> None:
        import tempfile
        from pynput import keyboard
        from harvest_assistant.approach import ApproachRecorder
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtPlan, TaughtStore, jpeg_frame

        class Screen:
            def is_target_foreground(self): return True
            def capture(self): return np.zeros((10, 10, 3), np.uint8)

        class Prompt:
            def read(self, _frame): return "open"                # the table menu has an «Открыть» line too
            def harvest_visible(self, _frame): return True

        recorder = ApproachRecorder(Screen(), Prompt())
        recorder._recording = True
        recorder._start = __import__("time").monotonic()
        recorder._on_press(keyboard.KeyCode.from_vk(0x45))
        for thread in recorder._ocr_threads:
            thread.join(2)
        self.assertEqual([(e.kind, e.value) for e in recorder._events if e.kind == "interact"], [("interact", "harvest")])

        fixtures = ROOT / "tests" / "fixtures"
        images = [np.asarray(Image.open(fixtures / name).convert("RGB")) for name in TaughtRunTests.FRAMES]
        store = TaughtStore(Path(tempfile.mkdtemp()))
        events = [RouteEvent(0.0, "key_down", "w"), RouteEvent(0.5, "key_up", "w"), RouteEvent(1.9, "interact", "harvest")]
        run = store.add_run(events, [(0.6 * i, jpeg_frame(image)) for i, image in enumerate(images)], 2.2)
        plan = TaughtPlan(run, 1280, 88.0)
        self.assertEqual([s.kind for s in plan.steps], ["move"])  # the harvest press is not a step of the way
        self.assertEqual(plan.stages, 0)


class FloorPlanTests(unittest.TestCase):
    def test_the_ways_of_the_plan_keep_clear_of_tables_and_pillars(self) -> None:
        from harvest_assistant.floorplan import FloorPlan, SPOTS, STAIRS_END, table_rect
        plan = FloorPlan()
        for spot in SPOTS:
            self.assertTrue(plan.free_at(spot.x, spot.y), spot.name)          # every standing place is open floor
        route = plan.route(STAIRS_END, (SPOTS[1].x, SPOTS[1].y))              # up the aisle between the first two columns
        self.assertEqual([round(x, 1) for x, _ in route], [5.2, 5.2])
        way = plan.route((SPOTS[1].x, SPOTS[1].y), (SPOTS[2].x, SPOTS[2].y))  # to the next aisle: round the tables
        for (x1, y1), (x2, y2) in zip(way, way[1:]):
            for i in range(21):
                px, py = x1 + (x2 - x1) * i / 20, y1 + (y2 - y1) * i / 20
                for left, bottom, right, top in [table_rect(cx, cy) for cx in (3.49, 7.02, 10.55) for cy in (4.16, 7.99)]:
                    self.assertFalse(left < px < right and bottom < py < top)  # never through a table
        self.assertGreater(plan.length(way), 5.0)                              # and so longer than the straight 3.6 m

    def test_the_tour_visits_the_four_places_in_order_and_faces_all_six_tables(self) -> None:
        from types import SimpleNamespace
        world = VisionCycleTests.world()
        runner, _profile, _table, _got, _driver = VisionCycleTests().build(world)
        runner._plan_rot, runner._plan_mps = 98.5, 2.5
        sim = {"x": 5.2, "y": 0.85, "h": 0.0, "walked": 0.0}
        runner._plan_pose = (5.2, 0.85, 0.0)
        runner._look = lambda degrees, pitch=0.0: sim.__setitem__("h", (sim["h"] + degrees * 0.9) % 360.0)

        def hold(key, seconds, shift=False):
            if key == "w":
                a = math.radians(sim["h"])
                sim["x"] += seconds * 2.5 * math.sin(a)
                sim["y"] += seconds * 2.5 * math.cos(a)
                sim["walked"] += seconds * 2.5

        runner._hold = hold

        def walk_leg(seconds, start, end, heading):        # the walk, as the simulated world moves the bot
            hold("w", seconds)
            return 1.0

        runner._walk_leg = walk_leg

        def gyro(degrees, tolerance=3.0):                  # the mini-map measures the true turn
            start = sim["h"]
            for _ in range(4):
                done = (sim["h"] - start + 180) % 360 - 180
                if abs(degrees - done) <= tolerance:
                    break
                runner._look(degrees - done, 0.0)
            return (sim["h"] - start + 180) % 360 - 180

        runner._gyro_turn = gyro
        visits = []

        def tend(side, done, visited):
            visits.append((round(sim["x"], 1), round(sim["y"], 1), side.slot, round(sim["h"])))
            return "seen"

        runner._tend_side = tend
        runner._platform_tour(set(), set())
        self.assertEqual(sorted(slot for _x, _y, slot, _h in visits), [0, 1, 2, 3, 4, 5])   # all six tables
        for x, y, slot, heading in visits:
            self.assertTrue(min(abs(x - 5.2), abs(x - 8.8)) < 0.4, (x, y))                   # always in an aisle
        self.assertLess(abs(sim["walked"] - 16.5), 4.0)                                       # about the planned way

class PlatformMemoryTests(unittest.TestCase):
    @staticmethod
    def pictures():
        """Four different real pictures standing for four places of the room."""
        fixtures = ROOT / "tests" / "fixtures"
        return [np.asarray(Image.open(fixtures / name).convert("RGB")) for name in TaughtRunTests.FRAMES]

    def test_the_memory_finds_a_place_from_a_picture_only_after_enough_certain_pictures(self) -> None:
        import tempfile
        from harvest_assistant.platform_memory import MIN_VIEWS, PlatformMemory
        memory = PlatformMemory(Path(tempfile.mkdtemp()))
        images = self.pictures()
        places = [(5.2, 4.16, 270.0), (5.2, 8.0, 90.0), (8.8, 8.0, 90.0), (8.8, 4.16, 90.0)]
        self.assertIsNone(memory.locate(images[0]))                     # empty memory: no guess
        views = [(images[i % 4], places[i % 4]) for i in range(8)]       # one walk, 8 pictures
        self.assertEqual(memory.add_walk(views), 8)
        self.assertEqual(memory.walks, 1)
        self.assertFalse(memory.usable)                                  # 8 < MIN_VIEWS: still too thin to be believed
        self.assertIsNone(memory.locate(images[0]))
        memory.add_walk(views * 2)                                       # more walks: 24 more pictures
        self.assertGreaterEqual(memory.size, MIN_VIEWS)
        for image, place in zip(images, places):
            x, y, heading, inliers = memory.locate(image)
            self.assertAlmostEqual(x, place[0], delta=0.01)             # the place the picture was taken at
            self.assertAlmostEqual(y, place[1], delta=0.01)
            self.assertAlmostEqual((heading - place[2] + 180) % 360 - 180, 0.0, delta=3.0)
        self.assertIsNone(memory.locate(np.full_like(images[0], 40)))   # a blank wall matches nothing

    def test_pictures_of_a_stretch_are_kept_only_when_it_ends_at_a_certain_place(self) -> None:
        import tempfile
        from harvest_assistant.platform_memory import PlatformMemory
        world = VisionCycleTests.world()
        world.phase = "centre"                                           # a real picture with plenty to recognise
        runner, _p, _t, _g, _d = VisionCycleTests().build(world)
        runner._memory_of_platform = PlatformMemory(Path(tempfile.mkdtemp()))
        frame = world.capture()
        small = cv2.resize(frame, (640, 512))
        runner._leg_views = [(small, (5.2, 1.0 + i, 0.0)) for i in range(4)]
        runner._verify_views(5.2, 4.16, 270.0, frame)                    # at a table with its menu: certain
        self.assertEqual(len(runner._walk_views), 5)                     # the 4 pictures of the stretch + the spot
        self.assertEqual(runner._leg_views, [])
        runner._leg_views = [(small, (5.2, 5.0, 0.0))] * 3                # another stretch that ended without a table
        runner._leg_views = []                                            # (_tend_side drops it: not trusted)
        runner._keep_views()
        self.assertEqual(runner._plan_memory().walks, 1)                  # one walk saved, with only the certain pictures
        self.assertEqual(runner._plan_memory().size, 5)


class StairsTests(unittest.TestCase):
    @staticmethod
    def world_with_minimap():
        """A frame with a mini-map (cut from a real screenshot) that turns with the camera, like the game's."""
        from harvest_assistant.radar import MINIMAP_BOX
        base = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        base = cv2.resize(base, (1280, 1024))
        left, top, right, bottom = MINIMAP_BOX
        box = (int(top * 1024), int(bottom * 1024), int(left * 1280), int(right * 1280))

        class World:
            yaw = 0.0  # the true camera heading, degrees

            def capture(self_inner):
                frame = base.copy()
                crop = frame[box[0]:box[1], box[2]:box[3]]
                centre = (crop.shape[1] / 2, crop.shape[0] / 2)
                matrix = cv2.getRotationMatrix2D(centre, self_inner.yaw, 1.0)   # the map turns with the camera
                frame[box[0]:box[1], box[2]:box[3]] = cv2.warpAffine(crop, matrix, (crop.shape[1], crop.shape[0]),
                                                                       borderMode=cv2.BORDER_REPLICATE)
                return frame

            def is_target_foreground(self_inner):
                return True

        return World()

    def test_the_gyro_measures_a_turn_on_the_mini_map_and_corrects_a_camera_that_under_turns(self) -> None:
        world = self.world_with_minimap()
        runner, _p, _t, _g, driver = VisionCycleTests().build(world)
        runner.px_per_degree = 50.0
        driver.move_relative = lambda dx, dy: setattr(world, "yaw", world.yaw + dx / 50.0 * 0.7)  # the real turn is 70% of asked
        turned = runner._gyro_turn(90.0)
        self.assertAlmostEqual(world.yaw, 90.0, delta=4.0)      # it kept turning until the mini-map said 90
        self.assertAlmostEqual(turned, 90.0, delta=4.0)

    def test_after_the_last_gate_it_turns_to_the_stairs_walks_their_length_and_turns_left(self) -> None:
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtPlan

        class Run:
            run_id = "r"
            duration = 20.0
            features: list = []
            radar: list = []
            keyframes = [{"t": float(t)} for t in range(0, 20)]
            events = [RouteEvent(10.0, "interact", "close"), RouteEvent(10.5, "mouse_delta", (8000, 0)),  # ~160 deg to the right
                      RouteEvent(11.0, "key_down", "w"), RouteEvent(13.0, "key_up", "w")]
        plan = TaughtPlan(Run(), 1280, 88.0)
        door = [s for s in plan.steps if s.kind == "interact"][-1]
        world = VisionCycleTests.world()
        runner, _p, _t, _g, driver = VisionCycleTests().build(world)
        turns, walks = [], []
        runner._gyro_turn = lambda degrees, tolerance=3.0: turns.append(degrees) or degrees
        runner._walk_straight = lambda seconds: walks.append(seconds)
        runner.metres_per_second = 2.5
        runner._take_stairs(plan, door)
        self.assertEqual(len(turns), 2)
        self.assertGreater(abs(turns[0]), 100.0)                  # round to the stairs, as the author turned
        self.assertEqual(turns[1], -90.0)                          # and LEFT at the end of the stairs
        self.assertAlmostEqual(walks[0], 6.4 / 2.5, places=3)     # the length of the stairs, no more
        self.assertTrue(runner.at_platform_entry and runner.stairs_taken)


class TableCountTests(unittest.TestCase):
    @staticmethod
    def plan_with(events):
        from harvest_assistant.taught import TaughtPlan

        class Run:
            run_id = "r"
            keyframes: list = []
            duration = 100.0
            features: list = []
            radar: list = []
        run = Run()
        run.events = events
        return TaughtPlan(run, 1280, 88.0)

    def test_a_table_pressed_twice_is_one_mark_and_every_pass_over_the_rows_counts(self) -> None:
        from harvest_assistant.models import RouteEvent
        events = [RouteEvent(0.0, "key_down", "w"), RouteEvent(1.0, "key_up", "w"),
                  RouteEvent(10.0, "interact", "harvest"), RouteEvent(11.0, "interact", "harvest")]  # F6 and E: one table
        t = 12.0
        for _ in range(8):                                             # then eight more tables, a step between each
            events += [RouteEvent(t, "key_down", "w"), RouteEvent(t + 0.3, "key_up", "w"), RouteEvent(t + 1.0, "interact", "harvest")]
            t += 2.0
        plan = self.plan_with(events)
        self.assertEqual(plan.harvest_times[:2], [10.0, 13.0])         # the second press at the first table is not a table
        self.assertEqual(len(plan.harvest_times), 9)                   # every other press counts: passes over the rows are clustered later

    def test_after_an_open_door_only_the_last_seconds_before_the_close_may_be_matched(self) -> None:
        """The porch case: the bot walks through the door itself, so key frames of the author's own walk through must not
        pull it back to an earlier moment (it would walk the porch again, into the fence)."""
        from harvest_assistant.models import RouteEvent
        from harvest_assistant.taught import TaughtPlan

        class Run:
            run_id = "r"
            duration = 20.0
            features: list = []
            radar: list = []
            keyframes = [{"t": float(t)} for t in range(0, 20)]
            events = [RouteEvent(11.9, "interact", "open"), RouteEvent(12.3, "key_down", "w"), RouteEvent(13.1, "key_up", "w"),
                      RouteEvent(15.2, "key_down", "w"), RouteEvent(15.6, "key_up", "w"), RouteEvent(17.0, "interact", "close")]
        plan = TaughtPlan(Run(), 1280, 88.0)
        self.assertFalse(plan.after_open_ok(13, stage=1))     # t = 13: the author walking out: skip
        self.assertTrue(plan.after_open_ok(15, stage=1))      # t = 15: the little step and the turn before the close
        self.assertTrue(plan.after_open_ok(13, stage=2))      # past the last door of the plan nothing is excluded

    def test_a_ripe_table_the_profile_does_not_know_is_added_while_there_is_room(self) -> None:
        from harvest_assistant.models import TableProfile
        world = VisionCycleTests.world()
        runner, profile, table, _got, _driver = VisionCycleTests().build(world)
        added = []
        runner.on_new_table = lambda: added.append(TableProfile("table_2", "Стол 2", 1)) or added[-1]
        self.assertIs(runner._pick_table(None, {table.table_id}), added[0])   # every known table is done: a new one
        runner.on_new_table = lambda: None                                      # the room is full (six): nothing to add
        self.assertIsNone(runner._pick_table(None, {table.table_id}))


class TableMarkTests(unittest.TestCase):
    def test_a_key_marks_a_table_while_recording_without_pressing_e(self) -> None:
        from harvest_assistant.approach import ApproachRecorder

        class Screen:
            def is_target_foreground(self): return True
            def capture(self): return np.zeros((10, 10, 3), np.uint8)

        recorder = ApproachRecorder(Screen(), FakePrompt([]))
        self.assertFalse(recorder.mark_table())                   # not recording: nothing happens
        recorder._recording = True
        recorder._start = __import__("time").monotonic()
        self.assertTrue(recorder.mark_table())
        self.assertEqual([(e.kind, e.value) for e in recorder._events], [("interact", "harvest")])


class ZoneTests(unittest.TestCase):
    @staticmethod
    def fixture(name):
        return cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / name)), cv2.COLOR_BGR2RGB)

    def test_zones_are_told_by_light_and_by_doors_passed(self) -> None:
        from harvest_assistant.zones import is_platform, zone_for
        self.assertEqual(zone_for(0, self.fixture("home_spawn.png")), "house")
        self.assertEqual(zone_for(1, self.fixture("house_exit_full.png")), "yard")
        self.assertEqual(zone_for(2, self.fixture("container_approach_full.png")), "yard")
        self.assertEqual(zone_for(3, np.full((1024, 1280, 3), 60, np.uint8)), "gate")      # dark inside, third door
        self.assertEqual(zone_for(4, np.full((1024, 1280, 3), 60, np.uint8)), "stairs")
        platform = self.fixture("plantation_ready.jpg")
        self.assertTrue(is_platform(platform))
        self.assertEqual(zone_for(0, platform), "platform")                                  # the light wins over the count
        self.assertFalse(is_platform(self.fixture("home_spawn.png")))

    def test_after_the_stairs_it_turns_left_until_the_purple_light_of_the_platform_shows(self) -> None:
        world = VisionCycleTests.world()
        landing = np.full((1024, 1280, 3), 60, np.uint8)       # the landing: no purple yet
        seen = {"turns": 0}
        purple = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)

        class Scene:
            phase = "landing"
            walked = 0

            def capture(self_inner):
                return purple if seen["turns"] >= 2 else landing

            def is_target_foreground(self_inner):
                return True

        runner, _profile, _table, _got, driver = VisionCycleTests().build(Scene())
        original = driver.move_relative

        def move(dx, dy):
            original(dx, dy)
            if dx < 0:
                seen["turns"] += 1

        driver.move_relative = move
        runner._enter_platform()
        self.assertEqual(seen["turns"] >= 2, True)
        left = [a for a in driver.actions if a[0] == "turn" and a[1] < 0]
        self.assertTrue(left)                                    # it turned LEFT, and stopped once the light was purple

    def test_the_stairs_are_walked_straight_without_touching_the_camera_until_the_purple_light(self) -> None:
        import time as _time
        dark = np.full((1024, 1280, 3), 60, np.uint8)
        purple = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        started = _time.monotonic()

        class Scene:
            phase = "landing"
            walked = 0

            def capture(self_inner):
                # the picture keeps changing on the way down (steps going by), then the purple light shows
                if _time.monotonic() - started > 0.4:
                    return purple
                noise = np.random.RandomState(int(_time.monotonic() * 1000) % 1000).randint(0, 40, dark.shape, dtype=np.uint8)
                return (dark + noise).astype(np.uint8)

            def is_target_foreground(self_inner):
                return True

        runner, _profile, _table, _got, driver = VisionCycleTests().build(Scene())
        runner.DESCENT_EXTRA = 0.2
        runner._descend_stairs()
        self.assertTrue(runner._came_down)
        downs = [a for a in driver.actions if a[:3] == ("event", "key_down", "w")]
        self.assertEqual(len(downs), 1)                          # one continuous walk
        self.assertEqual([a for a in driver.actions if a[0] == "turn"], [])   # the camera was not touched

        driver.actions.clear()
        runner.px_per_degree = 50.0
        runner._enter_platform()                                 # now: a quarter turn LEFT, because the stairs are behind
        turns = [a[1] for a in driver.actions if a[0] == "turn"]
        self.assertTrue(turns and sum(turns) < 0)
        self.assertAlmostEqual(sum(turns), -90.0 * runner.TURN_GAIN * 50.0, delta=60)
        self.assertFalse(runner._came_down)

    def test_the_purple_light_alone_is_enough_to_know_we_are_on_the_platform_without_any_recorded_way(self) -> None:
        world = VisionCycleTests.world()
        world.phase = "far"
        purple = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        purple = purple.copy()
        purple[200:400, 400:800] = (20, 20, 20)                  # hide the badge: only the light is left to go by
        world.capture = lambda: purple
        runner, _profile, _table, _got, _driver = VisionCycleTests().build(world)
        runner._ensure_in_plantation()                           # no taught run exists: it must not raise
        self.assertTrue(runner._in_room)


class TableVisionTests(unittest.TestCase):
    """The badge above a table: leaf + clock = growing, leaf + green check = ready."""

    @staticmethod
    def scene(name: str, x: int = 400, y: int = 300) -> np.ndarray:
        badge = cv2.imread(str(ROOT / "src" / "harvest_assistant" / "visual_assets" / name))
        canvas = np.full((1024, 1280, 3), (70, 40, 80), np.uint8)
        canvas[y:y + badge.shape[0], x:x + badge.shape[1]] = badge
        return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)

    def test_growing_and_ready_badges_are_told_apart(self) -> None:
        from harvest_assistant.table_vision import TableVision
        vision = TableVision()
        growing = vision.scan(self.scene("table_badge.png"))
        ready = vision.scan(self.scene("table_badge_ready.png", 800, 200))
        self.assertEqual([b.state for b in growing], ["growing"])
        self.assertEqual([b.state for b in ready], ["ready"])
        self.assertAlmostEqual(ready[0].x, 800, delta=6)

    def test_remembered_table_is_recognised_from_another_spot_and_others_are_not(self) -> None:
        import tempfile
        from harvest_assistant.table_vision import TableMemory, TableVision
        img = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        vision, memory = TableVision(), TableMemory(Path(tempfile.mkdtemp()))
        self.assertTrue(memory.remember("table_2", img, vision.scan(img)))
        # the camera moved: smaller picture, shifted
        live = cv2.warpAffine(img, np.float32([[0.92, 0, 70], [0, 0.92, 40]]), (1280, 1024), borderMode=cv2.BORDER_REPLICATE)
        badge = cv2.cvtColor(cv2.imread(str(ROOT / "src" / "harvest_assistant" / "visual_assets" / "table_badge_ready.png")), cv2.COLOR_BGR2RGB)
        live[600:600 + badge.shape[0], 900:900 + badge.shape[1]] = badge  # another table's badge
        badges = vision.scan(live)
        self.assertEqual(len(badges), 2)
        names = memory.identify(live, badges)
        known = [index for index, table in names.items() if table == "table_2"]
        self.assertEqual(len(known), 1)
        self.assertAlmostEqual(badges[known[0]].x, 662, delta=8)  # the one that moved with the scene
        self.assertEqual(len(names), 1)                          # the foreign badge stays unnamed

    def test_words_over_a_table_combine_picture_and_timer(self) -> None:
        from harvest_assistant.table_vision import TableBadge, TableWatcher
        growing = TableBadge(0, 0, 60, 60, "growing", 0.9)
        ready = TableBadge(0, 0, 60, 60, "ready", 0.9)
        self.assertEqual(TableWatcher.describe(growing, ("Стол 2", 754)), "Стол 2 · 12:34")
        self.assertEqual(TableWatcher.describe(growing, ("Стол 2", 0)), "Стол 2 · по таймеру готов")
        self.assertEqual(TableWatcher.describe(ready, ("Стол 2", 0)), "Стол 2 · готов")
        self.assertIn("таймер: 10:00", TableWatcher.describe(ready, ("Стол 2", 600)))
        self.assertEqual(TableWatcher.describe(ready, None), "Стол · готов")

    def test_empty_scene_has_no_badges(self) -> None:
        from harvest_assistant.table_vision import TableVision
        self.assertEqual(TableVision().scan(np.full((1024, 1280, 3), 90, np.uint8)), [])


class VisionCycleTests(unittest.TestCase):
    """The pilot in the grow room on stand-in pictures cut from a real screenshot of a ready table."""

    @staticmethod
    def world():
        ready = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        shifted = cv2.warpAffine(ready, np.float32([[1, 0, 150], [0, 1, 0]]), (1280, 1024), borderMode=cv2.BORDER_REPLICATE)
        frames = {"far": np.full((1024, 1280, 3), 30, np.uint8), "aside": shifted, "centre": ready, "at_table": ready}

        class World:
            phase = "far"
            walked = 0

            def capture(self):
                return frames[self.phase]

            def is_target_foreground(self):
                return True

        return World()

    def build(self, world, *, taught=None, auto=False):
        from harvest_assistant.models import Profile, TableProfile
        from harvest_assistant.loot import LootItem
        from harvest_assistant.vision_cycle import VisionCycleRunner

        class Driver(RecordedPathTests.Driver):
            stop_event = __import__("threading").Event()
            pause_event = __import__("threading").Event()

            def play_route_event(self, event) -> None:
                super().play_route_event(event)
                if event.kind == "key_down" and event.value == "w":
                    world.walked += 1
                    if world.phase == "far" and world.walked >= 2:
                        world.phase = "aside"
                    elif world.phase == "centre":
                        world.phase = "at_table"

            def move_relative(self, dx, dy) -> None:
                super().move_relative(dx, dy)
                if world.phase == "aside" and dx > 0:
                    world.phase = "centre"

        class Prompt(FakePrompt):
            def harvest_visible(self, _frame):
                return world.phase == "at_table"

        class Loot:
            last_raw: list = []
            last_crop = None
            calls = 0

            def read(self, _frame):
                Loot.calls += 1
                return [LootItem("Пакет с урожаем", 2, "фиолетовый", (176, 108, 240))] if Loot.calls <= 2 else []

        table = TableProfile("table_1", "Стол 1", 0)
        profile = Profile(tables=[table], auto_collect=auto)
        got = {"loot": [], "views": [], "cycles": 0}
        runner = VisionCycleRunner(
            world, Driver(), ROOT / "src" / "harvest_assistant" / "visual_assets", lambda _snapshot: None,
            matcher=FakeMatcher([]), prompt_reader=Prompt([]), taught_runs=taught or [],
            profile=profile, profile_lock=__import__("threading").RLock(), loot_reader=Loot(),
            on_loot=lambda tab, items: got["loot"].append((tab.table_id, [i.count for i in items])),
            on_cycle=lambda: got.__setitem__("cycles", got["cycles"] + 1),
            on_harvest_view=lambda tab, frame: got["views"].append(tab.table_id),
        )
        runner.vision_sink = lambda payload: got.setdefault("shown", []).append(payload)
        return runner, profile, table, got, runner.driver

    def test_finds_the_table_turns_to_it_walks_up_and_harvests_by_sight(self) -> None:
        world = self.world()
        runner, profile, table, got, driver = self.build(world)
        harvested = runner._harvest_pass()
        self.assertEqual(harvested, 1)
        self.assertEqual(got["loot"], [("table_1", [2])])
        self.assertEqual(got["views"], ["table_1"])
        keys = [a for a in driver.actions if a[:2] == ("key", "e")]
        self.assertEqual(len(keys), 1)
        turned_right = [a for a in driver.actions if a[0] == "turn" and a[1] > 0]
        self.assertTrue(turned_right)                       # it aimed at the badge that was off to the right
        self.assertIsNotNone(table.due_at_utc)              # the timer of THIS table was restarted
        self.assertTrue(any("Собираю" in str(item.get("status", "")) for item in got["shown"]))

    def test_a_table_that_is_not_ripe_up_close_gets_no_press_and_no_timer_and_a_recheck_in_five_minutes(self) -> None:
        world = self.world()
        badge = cv2.cvtColor(cv2.imread(str(ROOT / "src" / "harvest_assistant" / "visual_assets" / "table_badge.png")), cv2.COLOR_BGR2RGB)
        scene = np.full((1024, 1280, 3), (70, 40, 80), np.uint8)
        scene[250:250 + badge.shape[0], 610:610 + badge.shape[1]] = badge   # the clock badge, in the middle of the screen
        world.phase = "growing"
        original = world.capture
        world.capture = lambda: scene if world.phase == "growing" else original()
        runner, _profile, table, got, driver = self.build(world)
        runner.prompt_reader.harvest_visible = lambda _frame: True          # standing at the table, its menu is up
        harvested = runner._harvest_pass()
        self.assertEqual(harvested, 0)
        self.assertEqual([a for a in driver.actions if a[:2] == ("key", "e")], [])   # E is not pressed on an unripe table
        self.assertIsNone(table.due_at_utc)                                          # and no timer is started for it
        self.assertTrue(runner._unripe_seen)
        remaining = runner._recheck_at - __import__("time").monotonic()
        self.assertTrue(290 < remaining <= 300)                                       # looked at again in 5 minutes

    def test_a_blocked_walk_is_reported_as_stuck_and_the_recheck_time_ends_the_wait(self) -> None:
        import time as _time
        world = self.world()
        world.phase = "centre"                                                       # a ready badge ahead that never gets bigger
        runner, _profile, _table, _got, _driver = self.build(world)
        runner.WALK_MAX = 4.0
        runner.prompt_reader.harvest_visible = lambda _frame: False                  # the menu never comes: something blocks
        target = runner._badges.scan(world.capture())[0]
        self.assertEqual(runner._walk_to_table(target), "stuck")
        runner._recheck_at = _time.monotonic() - 1
        runner._wait_for_tables()                                                    # returns at once: the re-check is due

    def test_walking_into_a_wall_is_noticed_and_a_bare_wall_scores_lower_than_the_room(self) -> None:
        from harvest_assistant.vision_cycle import VisionCycleRunner
        world = self.world()
        world.phase = "far"                                    # a dark, unchanging picture: a wall right in front
        runner, _profile, _table, _got, _driver = self.build(world)
        runner.EXPLORE_WALK = 3.0
        self.assertEqual(runner._explore_walk(), "blocked")
        wall = np.full((1024, 1280, 3), 30, np.uint8)
        scene = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        self.assertLess(VisionCycleRunner._texture(wall), VisionCycleRunner._texture(scene) / 5)

    def test_far_badges_are_found_and_nothing_is_invented(self) -> None:
        from harvest_assistant.table_vision import TableVision
        badge = cv2.cvtColor(cv2.imread(str(ROOT / "src" / "harvest_assistant" / "visual_assets" / "table_badge_ready.png")), cv2.COLOR_BGR2RGB)
        small = cv2.resize(badge, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA)   # a table about 9 m away
        scene = np.full((1024, 1280, 3), (70, 40, 80), np.uint8)
        scene[300:300 + small.shape[0], 500:500 + small.shape[1]] = small
        found = TableVision().scan(scene)
        self.assertEqual([b.state for b in found], ["ready"])

    def test_an_unnamed_table_takes_the_name_of_the_table_it_turns_out_to_be(self) -> None:
        import tempfile
        from harvest_assistant.table_vision import TableMemory, TableVision
        img = cv2.cvtColor(cv2.imread(str(ROOT / "tests" / "fixtures" / "plantation_ready.jpg")), cv2.COLOR_BGR2RGB)
        memory = TableMemory(Path(tempfile.mkdtemp()))
        vision = TableVision()
        memory.remember("anon_1", img, vision.scan(img))
        self.assertEqual(memory.known_tables, {"anon_1"})
        memory.rename("anon_1", "table_2")
        self.assertEqual(memory.known_tables, {"table_2"})
        self.assertEqual(list(memory.identify(img, vision.scan(img)).values()), ["table_2"])

    def test_one_round_when_auto_is_off_and_three_failures_stop_the_cycle_with_an_alarm(self) -> None:
        from unittest import mock
        world = self.world()
        world.phase = "aside"                               # a ready badge is already in view: no taught way is needed
        runner, _profile, _table, got, _driver = self.build(world, auto=False)
        runner.run_cycle()
        self.assertEqual(got["cycles"], 1)

        blind = self.world()
        blind.phase = "far"
        runner, _profile, _table, _got, _driver = self.build(blind)  # no badge, no taught way: it cannot even start
        with mock.patch("harvest_assistant.vision_cycle.alert") as alarm:
            with self.assertRaises(VisualRouteError):
                runner.run_cycle()
        alarm.assert_called_once()

if __name__ == "__main__":
    unittest.main()
