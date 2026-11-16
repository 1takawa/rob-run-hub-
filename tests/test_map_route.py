"""The way by the mini-map: the locator on real pictures, and the pilot in a simulated world."""

from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harvest_assistant import map_route  # noqa: E402
from harvest_assistant.map_route import RouteData, load_or_build  # noqa: E402
from harvest_assistant.minimap_pose import Pose, pose_from, wrap  # noqa: E402
from harvest_assistant.visual_route import PromptReader, VisualRouteError, VisualRouteTestRunner  # noqa: E402

TAUGHT = ROOT / "profiles" / "taught"
FIXTURES = ROOT / "tests" / "fixtures"


def route() -> RouteData:
    """The points of the real place (as the five recorded runs give them), rounded."""
    return RouteData(
        door_in=Pose(-0.5, -0.7, 6.0), porch=Pose(1.0, -6.1, 145.0), gate_out=Pose(9.0, -93.4, -77.0),
        gate_in=Pose(1.7, -95.3, 106.0), stairs_bearing=-73.0, stairs_end=(-16.0, -100.6), px_per_second=9.6,
        house_paths=[[(10.0, 5.0), (4.0, 4.0), (-1.0, 2.0), (-0.5, -0.7)], [(18.0, 12.0), (14.0, 8.0), (8.0, 6.0), (3.0, 4.0), (-0.5, -0.7)]],
        yard_path=[(2.0, -6.0), (-5.0, -10.0), (-9.0, -12.0), (-8.0, -19.0), (-6.0, -30.0), (-3.0, -42.0), (0.0, -54.0),
                   (2.0, -65.0), (4.0, -77.0), (8.0, -88.0), (9.0, -93.4)],
    )


def forward(heading: float) -> tuple[float, float]:
    return math.sin(math.radians(heading)), -math.cos(math.radians(heading))


class World:
    """A flat world in mini-map pixels: the house wall with its door, the container wall with its gate, an optional
    rock. It plays the screen (the light of the place), the keyboard and mouse, the game's door prompt and the clock."""

    SAND, HOUSE, DARK = (225, 175, 105), (70, 48, 34), (32, 26, 26)

    def __init__(self, data: RouteData, x: float, y: float, heading: float, *, house_open: bool = False,
                 gate_open: bool = False, turn_factor: float = 1.0, rock: tuple[float, float, float] | None = None) -> None:
        self.route = data
        self.x, self.y, self.heading = x, y, heading
        self.open = {"house": house_open, "gate": gate_open}
        self.turn_factor = turn_factor
        self.rock = rock
        self.clock = 0.0
        self.keys: set[str] = set()
        self.presses: list[tuple[str, str]] = []   # (door, action) of every E; ("blind", "") when no prompt was shown
        self.px_per_degree = 50.0
        self.random = random.Random(7)
        self.bumps = 0
        fx, fy = forward(data.door_in.heading)
        self.house_centre = (data.door_in.x + 3.0 * fx, data.door_in.y + 3.0 * fy)
        self.house_normal = (fx, fy)
        gx, gy = data.gate_in.x - data.gate_out.x, data.gate_in.y - data.gate_out.y
        depth = math.hypot(gx, gy)
        self.gate_normal = (gx / depth, gy / depth)
        self.gate_centre = (data.gate_out.x + 0.5 * gx, data.gate_out.y + 0.5 * gy)

    # -- geometry
    def _side(self, centre, normal, x, y) -> float:
        return (x - centre[0]) * normal[0] + (y - centre[1]) * normal[1]

    def _aside(self, centre, normal, x, y) -> float:
        return (x - centre[0]) * normal[1] - (y - centre[1]) * normal[0]

    def _crosses(self, name, centre, normal, gap, nx, ny) -> bool:
        before, after = self._side(centre, normal, self.x, self.y), self._side(centre, normal, nx, ny)
        if (before < 0) == (after < 0) or abs(self._aside(centre, normal, nx, ny)) > 40.0:
            return False
        return not (self.open[name] and abs(self._aside(centre, normal, nx, ny)) <= gap)

    def advance(self, seconds: float) -> None:
        left = seconds
        while left > 1e-9:
            dt = min(0.02, left)
            left -= dt
            self.clock += dt
            fx, fy = forward(self.heading)
            vx = vy = 0.0
            if "w" in self.keys:
                vx, vy = vx + fx, vy + fy
            if "s" in self.keys:
                vx, vy = vx - fx, vy - fy
            if "d" in self.keys:
                vx, vy = vx - fy, vy + fx
            if "a" in self.keys:
                vx, vy = vx + fy, vy - fx
            if not vx and not vy:
                continue
            nx, ny = self.x + vx * self.route.px_per_second * dt, self.y + vy * self.route.px_per_second * dt
            hit = self._crosses("house", self.house_centre, self.house_normal, 2.0, nx, ny) \
                or self._crosses("gate", self.gate_centre, self.gate_normal, 2.5, nx, ny) \
                or (self.rock is not None and math.hypot(nx - self.rock[0], ny - self.rock[1]) < self.rock[2])
            if hit:
                self.bumps += 1
            else:
                self.x, self.y = nx, ny

    def prompt(self) -> tuple[str, str] | None:
        """(door, action) the game offers right now: only with the crosshair on a door close by."""
        me = Pose(self.x, self.y, self.heading)
        for name, centre, normal in (("house", self.house_centre, self.house_normal), ("gate", self.gate_centre, self.gate_normal)):
            if me.distance_to(*centre) > 6.5:
                continue
            outside_house = name == "house" and self._side(centre, normal, self.x, self.y) > 0 and self.open["house"]
            # the open leaf of the house door hangs to the side of the porch: that is where the crosshair must be
            aim = wrap(self.route.porch.heading - self.heading) if outside_house else me.turn_to(*centre)
            if abs(aim) <= 14.0:
                return name, "close" if self.open[name] else "open"
        return None

    # -- the screen
    def capture(self) -> np.ndarray:
        self.advance(0.06)  # a screenshot and reading it take time, the character keeps walking meanwhile
        in_house = self._side(self.house_centre, self.house_normal, self.x, self.y) < 0 and abs(self.y) < 40
        in_container = self._side(self.gate_centre, self.gate_normal, self.x, self.y) > 0 and self.y < -80
        colour = self.HOUSE if in_house else self.DARK if in_container else self.SAND
        return np.full((64, 80, 3), colour, np.uint8)

    def is_target_foreground(self) -> bool:
        return True

    # -- the locator (the mini-map)
    def locate(self, _frame, _hint=None) -> Pose:
        noise = lambda: self.random.uniform(-0.4, 0.4)
        return Pose(self.x + noise(), self.y + noise(), wrap(self.heading + self.random.uniform(-1.0, 1.0)), 20)

    usable = True

    # -- the prompt reader
    def read_detail(self, _frame) -> tuple[str | None, bool]:
        shown = self.prompt()
        return (shown[1], True) if shown else (None, False)

    def read(self, _frame) -> str | None:
        return self.read_detail(None)[0]

    # -- the driver
    def move_relative(self, dx: int, _dy: int) -> None:
        self.heading = wrap(self.heading + dx / self.px_per_degree * self.turn_factor)

    def play_key(self, key: str, duration: float) -> None:
        if key == "e":
            shown = self.prompt()
            if shown is None:
                self.presses.append(("blind", ""))
            else:
                self.presses.append(shown)
                self.open[shown[0]] = not self.open[shown[0]]
        self.advance(duration)

    def play_route_event(self, event) -> None:
        if event.kind == "key_down":
            self.keys.add(str(event.value))
        elif event.kind == "key_up":
            self.keys.discard(str(event.value))

    def wait(self, duration: float) -> None:
        self.advance(duration)

    def monotonic(self) -> float:
        return self.clock


def pilot(world: World) -> VisualRouteTestRunner:
    runner = VisualRouteTestRunner(world, world, ROOT / "src" / "harvest_assistant" / "visual_assets", lambda _snapshot: None,
                                   matcher=object(), prompt_reader=world)
    runner.px_per_degree = world.px_per_degree
    runner._locator, runner._route = world, world.route
    return runner


def walk(world: World) -> VisualRouteTestRunner:
    runner = pilot(world)
    with mock.patch.object(map_route, "time", world):   # the pilot's clock is the clock of the world
        assert runner.run_by_map([], []) is True
    return runner


class PilotInTheWorld(unittest.TestCase):
    def arrived(self, world: World, runner: VisualRouteTestRunner) -> None:
        data = world.route
        length = runner.STAIRS_METRES / runner.metres_per_second * data.px_per_second
        fx, fy = forward(data.stairs_bearing)
        end = (data.gate_in.x + length * fx, data.gate_in.y + length * fy)
        self.assertLess(math.hypot(world.x - end[0], world.y - end[1]), 4.0)          # at the bottom of the stairs
        self.assertLess(abs(wrap(world.heading - (data.stairs_bearing - 90.0))), 5.0)  # turned LEFT there
        self.assertTrue(runner.at_platform_entry and runner.stairs_taken)
        self.assertNotIn(("blind", ""), world.presses)                                 # E only ever on a door prompt
        self.assertFalse(world.keys)                                                   # no key left held down

    def test_from_the_spawn_it_opens_and_closes_both_doors_and_ends_at_the_bottom_of_the_stairs(self) -> None:
        world = World(route(), 10.0, 5.0, -70.0)
        runner = walk(world)
        self.assertEqual(world.presses, [("house", "open"), ("house", "close"), ("gate", "open"), ("gate", "close")])
        self.assertFalse(world.open["house"] or world.open["gate"])
        self.arrived(world, runner)

    def test_any_spot_and_any_camera_direction_in_the_house_leads_out(self) -> None:
        for x, y, heading in [(18.0, 12.0, 170.0), (6.0, 9.0, 40.0), (14.0, 2.0, -150.0), (1.0, 3.0, 95.0), (-0.5, -0.5, 180.0)]:
            with self.subTest(start=(x, y, heading)):
                world = World(route(), x, y, heading)
                runner = walk(world)
                self.assertEqual(world.presses[0], ("house", "open"))
                self.arrived(world, runner)

    def test_started_in_the_yard_it_goes_straight_to_the_gate(self) -> None:
        world = World(route(), -3.0, -40.0, 120.0)
        runner = walk(world)
        self.assertEqual(world.presses, [("gate", "open"), ("gate", "close")])
        self.arrived(world, runner)

    def test_started_inside_the_container_it_only_takes_the_stairs(self) -> None:
        world = World(route(), 0.0, -95.8, 30.0, gate_open=False)
        runner = walk(world)
        self.assertEqual(world.presses, [])
        self.arrived(world, runner)

    def test_doors_that_already_stand_open_are_not_toggled_shut_in_front_of_us(self) -> None:
        world = World(route(), 10.0, 5.0, 0.0, house_open=True, gate_open=True)
        runner = walk(world)
        self.assertEqual(world.presses, [("house", "close"), ("gate", "close")])   # walked through, closed behind
        self.arrived(world, runner)

    def test_a_mouse_that_turns_less_or_more_than_expected_is_measured_and_corrected(self) -> None:
        for factor in (0.6, 1.5):
            with self.subTest(factor=factor):
                world = World(route(), 10.0, 5.0, -70.0, turn_factor=factor)
                runner = walk(world)
                self.assertEqual(len(world.presses), 4)
                self.arrived(world, runner)

    def test_something_in_the_way_is_walked_around(self) -> None:
        world = World(route(), -3.0, -36.0, 10.0, rock=(-1.5, -48.0, 1.6))
        runner = walk(world)
        self.assertGreater(world.bumps, 0)   # it did run into the rock
        self.arrived(world, runner)

    def test_a_gate_that_never_shows_its_prompt_stops_the_way_without_a_blind_press(self) -> None:
        world = World(route(), -3.0, -60.0, 0.0)
        world.prompt = lambda: None
        runner = pilot(world)
        with mock.patch.object(map_route, "time", world), self.assertRaises(VisualRouteError):
            runner.run_by_map([], [])
        self.assertEqual(world.presses, [])
        self.assertFalse(world.keys)

    def test_one_wild_reading_of_the_mini_map_is_not_believed(self) -> None:
        world = World(route(), 2.0, -6.0, 0.0)
        runner = pilot(world)
        readings = iter([Pose(2.0, -6.0, 0.0, 20), Pose(60.0, -80.0, 90.0, 9), Pose(2.1, -6.0, 1.0, 20)])
        world.locate = lambda _f, _h=None: next(readings)
        with mock.patch.object(map_route, "time", world):
            first = runner._map_pose()
            second = runner._map_pose()
        self.assertAlmostEqual(first.x, 2.0)
        self.assertAlmostEqual(second.x, 2.1)   # the far reading was skipped


TABLES = [(-12.4, -88.9, 138.0), (-14.7, -83.1, 140.0), (-16.3, -83.0, -96.0), (-16.0, -88.2, -59.0),
          (-22.7, -89.6, -87.0), (-24.1, -84.6, -96.0)]
# the ground the recordings walked on: the stairs, the aisle in the middle, the aisle in the west, the two cross ways
NET = [(1.7, -95.3), (-7.5, -98.2), (-16.5, -101.0), (-14.0, -94.0), (-13.5, -89.0), (-15.3, -83.2), (-16.0, -77.5),
       (-19.5, -94.8), (-22.3, -92.5), (-23.0, -89.8), (-24.0, -85.0), (-23.4, -79.5)]
JOINS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (3, 7), (7, 8), (8, 9), (9, 10), (10, 11), (11, 6)]
ROWS = [(-29.5, -92.0, -26.0, -82.0), (-21.3, -91.5, -18.2, -80.5), (-11.0, -87.5, -8.0, -79.5)]   # the three rows of tables


class Room(World):
    """The room below: three rows of tables that cannot be walked through, and over every table the badge the game
    draws there (a workbench, a clock or a check mark), seen from wherever it is in view."""

    FRAME = np.full((1024, 1280, 3), (40, 20, 60), np.uint8)

    def __init__(self, x: float, y: float, heading: float, states: list[str]) -> None:
        data = route()
        data.tables, data.nodes, data.edges = list(TABLES), list(NET), list(JOINS)
        super().__init__(data, x, y, heading, gate_open=False)
        self.states = states
        self.near: dict[int, float] = {}   # table -> seconds spent within a step of its spot in the aisle
        self.pressed_at: list[float] = []  # the clock at every E
        self.pause_event = __import__("threading").Event()
        self.stop_event = __import__("threading").Event()

    def advance(self, seconds: float) -> None:
        left = seconds
        while left > 1e-9:
            dt = min(0.02, left)
            left -= dt
            was = (self.x, self.y)
            super().advance(dt)
            if any(a <= self.x <= c and b <= self.y <= d for a, b, c, d in ROWS):
                self.x, self.y = was   # a table: no way through
                self.bumps += 1
            for index, (tx, ty, _h) in enumerate(self.route.tables):
                if math.hypot(self.x - tx, self.y - ty) <= 2.0:
                    self.near[index] = self.near.get(index, 0.0) + dt

    def spot(self, index: int) -> tuple[float, float]:
        x, y, heading = self.route.tables[index]
        fx, fy = forward(heading)
        return x + 4.5 * fx, y + 4.5 * fy

    def at_table(self) -> int | None:
        """The table whose menu is up: we stand at its spot in the aisle with the crosshair on the table."""
        me = Pose(self.x, self.y, self.heading)
        for index, (x, y, _heading) in enumerate(self.route.tables):
            if math.hypot(self.x - x, self.y - y) <= 3.2 and abs(me.turn_to(*self.spot(index))) <= 16.0:
                return index
        return None

    def harvest_visible(self, _frame) -> bool:   # the menu «Сбор урожая»: only a ready table has it
        index = self.at_table()
        return index is not None and self.states[index] == "ready"

    def scan(self, _frame):
        """Every badge in the field of view, where it is on the screen and as big as its distance makes it."""
        from harvest_assistant.table_vision import TableBadge

        me = Pose(self.x, self.y, self.heading)
        focal = 640 / math.tan(math.radians(44.0))
        badges = []
        for index in range(len(self.route.tables)):
            spot = self.spot(index)
            distance, aside = me.distance_to(*spot), me.turn_to(*spot)
            if distance < 1.0 or distance > 60.0 or abs(aside) > 42.0:
                continue
            size = int(max(18, min(90, 70 * 4.5 / distance)))
            badges.append(TableBadge(int(640 + focal * math.tan(math.radians(aside)) - size / 2), 200, size, size,
                                     self.states[index], 1.0))
        return badges

    def capture(self) -> np.ndarray:
        self.advance(0.06)
        return self.FRAME

    def time(self) -> float:
        return __import__("time").time()

    def play_key(self, key: str, duration: float) -> None:
        index = self.at_table()
        if key == "e" and index is not None:
            self.presses.append((f"table {index + 1}", self.states[index]))
            self.pressed_at.append(self.clock)
            if self.states[index] == "ready":
                self.states[index] = "growing"   # harvested: it grows again
            self.advance(duration)
            return
        super().play_key(key, duration)


class TourInTheRoom(unittest.TestCase):
    def build(self, room: Room):
        from harvest_assistant.loot import LootItem
        from harvest_assistant.models import Profile, TableProfile
        from harvest_assistant.vision_cycle import VisionCycleRunner

        class Loot:
            last_raw: list = []
            last_crop = None

            def read(self, _frame):
                return [LootItem("Пакет с урожаем", 2, "фиолетовый", (176, 108, 240))]

        profile = Profile(tables=[TableProfile(f"table_{i + 1}", f"Стол {i + 1}", i) for i in range(6)], auto_collect=False)
        runner = VisionCycleRunner(
            room, room, ROOT / "src" / "harvest_assistant" / "visual_assets", lambda _snapshot: None,
            matcher=object(), prompt_reader=room, taught_runs=[], profile=profile,
            profile_lock=__import__("threading").RLock(), loot_reader=Loot(), table_vision=room,
        )
        runner.px_per_degree = room.px_per_degree
        runner._locator, runner._route = room, room.route
        runner._save_goals = lambda: None
        runner._pose_now = lambda frame=None: None
        return runner

    def round(self, room: Room, runner) -> int:
        from harvest_assistant import vision_cycle

        # the loot watcher waits in real seconds, which would run the clock of the world away: one read stands in for it
        with mock.patch.object(map_route, "time", room), mock.patch.object(vision_cycle, "time", room),                 mock.patch.object(vision_cycle, "watch_loot", lambda reader, _capture, _wait, _timeout: reader.read(None)):
            return runner._harvest_pass()

    def test_ready_tables_are_harvested_growing_ones_noted_and_empty_ones_left_alone(self) -> None:
        room = Room(-16.5, -101.0, -163.0, ["ready", "growing", "idle", "ready", "growing", "ready"])
        runner = self.build(room)
        harvested = self.round(room, runner)
        self.assertEqual(sorted(room.presses), [("table 1", "ready"), ("table 4", "ready"), ("table 6", "ready")])
        self.assertEqual(harvested, 3)
        self.assertEqual(room.bumps, 0)                       # never ran into a row
        self.assertFalse(room.keys)
        self.assertLess(room.near.get(2, 0.0), 8.0)           # one look at the empty table, no standing about
        self.assertIn(2, runner._idle_until)                  # it is out of the rounds for now
        self.assertIsNotNone(runner._recheck_at)              # the growing ones are looked at again
        self.assertLess(room.pressed_at[0], 45.0)             # the first harvest within the first minute
        self.assertLess(room.clock, 120.0)                    # and the whole round in two

    def test_one_ready_table_among_empty_ones_is_gone_to_and_harvested_without_touching_the_rest(self) -> None:
        room = Room(-16.5, -101.0, -163.0, ["idle", "ready", "idle", "idle", "idle", "idle"])
        runner = self.build(room)
        harvested = self.round(room, runner)
        self.assertEqual(room.presses, [("table 2", "ready")])
        self.assertEqual(harvested, 1)
        self.assertEqual(room.bumps, 0)
        self.assertLess(room.pressed_at[0], 45.0)
        # the next round: the empty ones are remembered, nothing is ready, so nobody walks anywhere
        before = (room.x, room.y, len(room.presses))
        self.round(room, runner)
        self.assertEqual(len(room.presses), before[2])
        self.assertLess(math.hypot(room.x - before[0], room.y - before[1]), 3.0)

    def test_an_empty_table_is_left_out_again_even_when_an_old_entry_of_it_ran_out(self) -> None:
        room = Room(-16.5, -101.0, -163.0, ["growing", "growing", "growing", "idle", "idle", "idle"])
        runner = self.build(room)
        runner._idle_until = {3: -5.0, 4: -5.0, 5: -5.0}   # remembered long ago: expired
        self.round(room, runner)
        self.assertTrue(all(runner._idle_until[k] > room.clock for k in (3, 4, 5)))
        self.round(room, runner)                              # the next round does not go to them
        near_after = {k: room.near.get(k, 0.0) for k in (3, 4, 5)}
        self.round(room, runner)
        self.assertEqual(near_after, {k: room.near.get(k, 0.0) for k in (3, 4, 5)})

    def test_the_place_decides_which_table_was_harvested_not_a_look_alike_or_an_old_guess(self) -> None:
        room = Room(-16.5, -101.0, -163.0, ["ready"] * 6)
        runner = self.build(room)
        runner._slot_of = {"table_3": 0}                      # a wrong guess learned earlier
        table = runner._pick_table("table_3", set(), 0)
        self.assertEqual(table.table_id, "table_1")
        self.assertIsNone(runner._pick_table(None, {"table_1"}, 0))   # done already: no other table is taken instead

    def test_started_anywhere_in_the_room_every_ready_table_is_found(self) -> None:
        for x, y, heading in [(-23.4, -79.5, 0.0), (-13.5, -89.0, 90.0), (-7.5, -98.2, -70.0)]:
            with self.subTest(start=(x, y)):
                room = Room(x, y, heading, ["ready", "idle", "ready", "idle", "ready", "idle"])
                runner = self.build(room)
                self.round(room, runner)
                self.assertEqual(sorted(room.presses), [("table 1", "ready"), ("table 3", "ready"), ("table 5", "ready")])
                self.assertEqual(room.bumps, 0)

    def test_the_way_between_two_aisles_goes_round_the_row_not_through_it(self) -> None:
        from harvest_assistant.map_route import net_path

        way = net_path(NET, JOINS, 10, 5)   # from the west aisle to the middle one
        self.assertIsNotNone(way)
        for a, b in zip(way, way[1:]):
            for step in range(11):
                x = NET[a][0] + (NET[b][0] - NET[a][0]) * step / 10
                y = NET[a][1] + (NET[b][1] - NET[a][1]) * step / 10
                self.assertFalse(any(left <= x <= right and top <= y <= bottom for left, top, right, bottom in ROWS))


TABLES_FOR_GROUPS = list(TABLES)


class TableGroupTests(unittest.TestCase):
    def test_a_table_marked_from_both_sides_of_an_aisle_is_one_table(self) -> None:
        from harvest_assistant.map_route import table_groups

        spots = [(-6.4, -91.1, 99), (-9.0, -85.5, 101), (-11.8, -85.9, -75), (-9.8, -90.7, -68),
                 (-16.0, -94.7, -83), (-18.5, -88.2, -88), (-13.2, -93.6, 112), (-15.5, -87.8, 116)]   # the taught run
        groups = table_groups(spots)
        self.assertEqual(groups, [0, 1, 2, 3, 4, 5, 3, 2])
        self.assertEqual(len(set(groups)), 6)
        self.assertEqual(table_groups(TABLES_FOR_GROUPS), list(range(len(TABLES_FOR_GROUPS))))   # the simulated room: all apart


class PromptTests(unittest.TestCase):
    def reader(self, lines: list[str]) -> PromptReader:
        reader = PromptReader(require_door=True)

        async def fake(_crop):
            return [(text, (0, 0, 10, 10)) for text in lines]
        reader._ocr._ocr_lines = fake
        return reader

    def test_the_overlay_label_of_the_pilot_itself_is_never_read_as_a_door_prompt(self) -> None:
        frame = np.zeros((200, 300, 3), np.uint8)
        self.assertEqual(self.reader(["Дверь · открыть · вижу · рядом"]).read_detail(frame), (None, False))
        self.assertIsNone(self.reader(["Дверь открыть ищу"]).read(frame))

    def test_title_and_action_are_reported_apart_and_a_window_is_nothing(self) -> None:
        frame = np.zeros((200, 300, 3), np.uint8)
        self.assertEqual(self.reader(["ДВЕРЬ", "Открыть"]).read_detail(frame), ("open", True))
        self.assertEqual(self.reader(["Е", "Закрыть"]).read_detail(frame), ("close", False))
        self.assertEqual(self.reader(["ОКНО", "Закрыть"]).read_detail(frame), (None, False))


class MiniMapMathTests(unittest.TestCase):
    def test_a_turn_on_the_spot_does_not_move_the_pose_and_a_step_forward_does(self) -> None:
        from harvest_assistant.minimap_pose import PIVOT
        from harvest_assistant.radar import CANVAS

        def fit(turn: float, step: float) -> tuple[float, float, float, int]:
            """What `register` reports when the map turns by `turn` around the pivot and slides `step` px down."""
            matrix = cv2.getRotationMatrix2D(PIVOT, -turn, 1.0)   # image y is down: this turns by +turn in our sign
            centre = np.float32([[CANVAS[0] / 2, CANVAS[1] / 2]]).reshape(-1, 1, 2)
            moved = cv2.transform(centre, matrix).reshape(2)
            return (math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])), float(moved[0] - CANVAS[0] / 2),
                    float(moved[1] - CANVAS[1] / 2) + step, 30)

        base = Pose(5.0, -20.0, 30.0)
        turned = pose_from(base, fit(75.0, 0.0))
        self.assertAlmostEqual(turned.x, base.x, delta=0.05)
        self.assertAlmostEqual(turned.y, base.y, delta=0.05)
        self.assertAlmostEqual(abs(wrap(turned.heading - base.heading)), 75.0, delta=0.05)
        walked = pose_from(base, fit(0.0, 10.0))   # the map slid down: we went forward along our heading
        fx, fy = forward(30.0)
        self.assertAlmostEqual(walked.x, base.x + 10.0 * fx, delta=0.05)
        self.assertAlmostEqual(walked.y, base.y + 10.0 * fy, delta=0.05)


@unittest.skipUnless(any(TAUGHT.glob("run_*/run.json")), "no taught runs on this machine")
class RealRunsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from harvest_assistant.taught import TaughtPlan, TaughtStore

        cls.runs = TaughtStore(TAUGHT).load_runs()
        cls.plans = [TaughtPlan(run, 1280, 88.0) for run in cls.runs]
        cls.locator, cls.route = load_or_build(cls.runs, cls.plans, TAUGHT / "maproute.json")

    def test_the_points_of_the_way_make_sense(self) -> None:
        data = self.route
        self.assertLess(data.door_in.distance_to(0.0, 0.0), 4.0)
        self.assertTrue(4.0 < data.door_in.distance_to(data.porch.x, data.porch.y) < 10.0)     # a couple of metres out
        self.assertTrue(70.0 < data.porch.distance_to(data.gate_out.x, data.gate_out.y) < 110.0)  # the yard
        self.assertTrue(5.0 < data.gate_out.distance_to(data.gate_in.x, data.gate_in.y) < 11.0)
        # inside, the author looks back at the gate: about half a turn from the way in
        self.assertGreater(abs(wrap(data.gate_in.heading - data.gate_out.heading)), 150.0)
        self.assertGreaterEqual(len(data.yard_path), 8)
        self.assertTrue(7.0 < data.px_per_second < 13.0)

    def test_every_key_frame_of_every_run_is_located_and_the_doors_of_all_runs_coincide(self) -> None:
        missed, spread = 0, []
        for run, plan in zip(self.runs, self.plans):
            doors = [step.t0 for step in plan.steps if step.kind == "interact"]
            for index, feats in enumerate(run.radar):
                pose = self.locator.sift._locate(feats, None) if feats is not None else None
                if pose is None:
                    missed += 1
                elif len(doors) >= 3 and abs(plan.key_times[index] - doors[2]) < 0.2:
                    spread.append(pose.distance_to(self.route.gate_out.x, self.route.gate_out.y))
        self.assertLessEqual(missed, 0.03 * sum(len(run.radar) for run in self.runs))   # a cold SIFT start misses a few
        self.assertTrue(spread)
        self.assertLess(max(spread), 4.0)   # every run opened the gate from the same spot, within about a metre

    def test_a_picture_without_a_mini_map_gives_no_pose_and_the_room_below_is_known(self) -> None:
        self.locator.last = None
        self.assertIsNone(self.locator.locate(np.full((1024, 1280, 3), (200, 160, 100), np.uint8)))
        self.locator.last = None
        noise = np.random.default_rng(1).integers(0, 255, (1024, 1280, 3), dtype=np.uint8)
        self.assertIsNone(self.locator.locate(noise))
        self.assertGreaterEqual(len(self.route.tables), 1)      # the tables the author marked while recording
        self.assertGreaterEqual(len(self.route.nodes), 10)      # and the ground walked on between them

    def test_live_pictures_of_the_bot_itself_are_placed_where_it_stood(self) -> None:
        cases = [("live_at_house_door.jpg", self.route.door_in), ("live_on_porch.jpg", self.route.porch),
                 ("live_inside_gate.jpg", self.route.gate_in)]
        for name, spot in cases:
            with self.subTest(name=name):
                image = cv2.imdecode(np.fromfile(str(FIXTURES / name), np.uint8), cv2.IMREAD_COLOR)
                rgb = cv2.cvtColor(cv2.resize(image, (1280, 1024), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
                self.locator.last = None
                pose = self.locator.locate(rgb)
                self.assertIsNotNone(pose)
                self.assertLess(pose.distance_to(spot.x, spot.y), 3.5)
                self.assertLess(abs(wrap(pose.heading - spot.heading)), 25.0)


if __name__ == "__main__":
    unittest.main()
