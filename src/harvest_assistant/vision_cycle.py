"""The whole job by sight: way to the grow room, find the tables, harvest the ready ones, wait, repeat.

No recorded macro anywhere. The way to the plantation is the taught route (key frames + door prompts, see
``taught.py``); in the grow room the pilot finds the status badge over a table (``table_vision.py``), turns
towards it, walks up until the menu with «Сбор урожая» shows, presses the key and counts the packages that
arrive. A table counts as harvested only when its packages were seen (same rule as the old route).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

import cv2
import numpy as np

from .input_driver import InputFocusLost, InputPaused, InputStopped
from .loot import watch_loot
from .models import MAX_TABLES, RunState, TableProfile, remaining_seconds, reset_table_timer
from .power import alert, keep_awake
from .screen import TargetWindowNotForeground
from .table_vision import TableBadge, TableVision, format_countdown
from .taught import pixels_to_degrees
from .floorplan import FloorPlan, STAIRS_END, STAIRS_START
from .platform_memory import PlatformMemory
from .navigator import wrap
from .zones import ZONE_TITLES, is_platform
from .visual_route import VisualRouteError, VisualRouteTestRunner

logger = logging.getLogger(__name__)


class VisionCycleRunner(VisualRouteTestRunner):
    MAX_FAILURES = 3           # failed stages in a row before the cycle gives up and calls the user
    PASS_SECONDS = 240.0       # longest single look-and-harvest round
    EXPLORE_CHUNKS = 8         # walks forward (up to EXPLORE_WALK each) while no table badge has shown up yet
    EXPLORE_WALK = 1.6
    AIM_HEIGHT = 0.26          # where the badge sits on screen when the crosshair is on the table (fraction of height)
    WALK_CHUNK = 0.5
    ALIGN_PITCH_TOLERANCE = 6.0

    def __init__(
        self,
        *args: Any,
        profile: Any,
        profile_lock: Any,
        loot_reader: Any = None,
        on_loot: Callable[[TableProfile, list], None] | None = None,
        on_cycle: Callable[[], None] | None = None,
        on_harvest_view: Callable[[TableProfile, Any], None] | None = None,
        memory: Any = None,
        on_new_table: Callable[[], TableProfile | None] | None = None,
        table_vision: TableVision | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.profile_lock = profile_lock
        self.loot_reader = loot_reader
        self.on_loot = on_loot
        self.on_cycle = on_cycle
        self.on_harvest_view = on_harvest_view
        self.memory = memory
        self.on_new_table = on_new_table
        self._badges = table_vision or TableVision()
        self._in_room = False
        self._empty_passes = 0
        self.cycles_done = 0
        self._final_message = "Цикл остановлен."
        self._recheck_at: float | None = None  # when the tables that were not ripe are looked at again
        self._unripe_seen = False
        self._nav = None                       # pictures + map used to know where we are (built on first use)
        self._last_pose: tuple[float, float, float] | None = None
        self._learned_goals: dict[str, tuple[float, float]] = {}  # table id -> where the bot stood when it harvested it
        self._goal_tried: set[str] = set()
        self.hand_over_at_platform = True      # the way ends at the end of the stairs, the room is walked by its plan
        self._floor: FloorPlan | None = None
        self._plan_pose: tuple[float, float, float] | None = None  # x, y (m, room plan), heading (deg clockwise from north)
        self._plan_rot = 98.5                  # map heading -> plan heading (set from the stairs, see _context)
        self._slot_of: dict[str, int] = {}     # table id -> which of the six places it stands at (learned at a harvest)
        self._memory_of_platform: PlatformMemory | None = None
        self._leg_views: list = []             # pictures of the stretch walked now, their places not yet certain
        self._walk_views: list = []            # certain ones of this walk: (small frame, (x, y, heading))

    # ---- plumbing ----------------------------------------------------------------------

    def _frame(self) -> np.ndarray:
        try:
            return self.screen.capture()
        except TargetWindowNotForeground as exc:
            raise InputFocusLost(str(exc)) from exc

    def _tables(self) -> list[TableProfile]:
        with self.profile_lock:
            return list(self.profile.tables)

    def _remaining(self, table: TableProfile) -> int | None:
        with self.profile_lock:
            return remaining_seconds(table, self.profile, time.time())

    def _say(self, status: str, frame: np.ndarray | None = None, *, mode: str = "ok", target: dict | None = None) -> None:
        size = [frame.shape[1], frame.shape[0]] if frame is not None else [1280, 1024]
        zone = ZONE_TITLES["platform"] if frame is not None and is_platform(frame) else "плантация"
        self._show(status=status, mode=mode, stage=zone, frame=size, target=target or {},
                   radar=(self._floorplan().view(self._plan_pose) if self._plan_pose is not None and frame is not None
                          and is_platform(frame) else self._radar(self._last_pose, None, frame is not None and is_platform(frame))))
        self._set_state(RunState.ROUTING, status)

    @staticmethod
    def _box(badge: TableBadge, note: str) -> dict:
        x1, y1, x2, y2 = badge.x - 8, badge.y - 8, badge.x + badge.width + 8, badge.y + badge.height + 8
        return {"label": badge.label, "note": note, "quad": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]}

    # ---- the cycle ---------------------------------------------------------------------

    def _execute(self) -> None:
        keep_awake(True)  # the screen must stay on: the game is read from it
        try:
            self.run_cycle()
            self._set_state(RunState.STOPPED, self._final_message)
        finally:
            keep_awake(False)

    def run_cycle(self) -> None:
        phase = "go"            # go -> harvest -> wait -> harvest ...
        failures = 0
        while not self.stop_event.is_set():
            try:
                if phase == "go":
                    self._ensure_in_plantation()
                    phase = "harvest"
                if phase == "harvest":
                    harvested = self._harvest_pass()
                    failures = 0
                    if harvested:
                        self._empty_passes = 0
                        self.cycles_done += 1
                        if self.on_cycle is not None:
                            try:
                                self.on_cycle()
                            except Exception:
                                logger.exception("Cycle callback failed")
                        with self.profile_lock:
                            auto = self.profile.auto_collect
                        if not auto:  # one round, as the «Авто» switch says
                            self._final_message = "Круг завершён. Включи «Авто», чтобы цикл повторялся сам."
                            return
                    elif self._unripe_seen:
                        self._empty_passes = 0  # tables were found, just not ripe yet: that is a result, not a failure
                        self._say("Столы ещё растут. Загляну через 5 минут")
                    else:
                        self._empty_passes += 1
                        if self._empty_passes >= 5:
                            self._empty_passes = 0
                            raise VisualRouteError("Пять кругов подряд ничего не собрано: столы не готовы или не найдены.")
                        self._say("Ничего не готово. Подожду и посмотрю снова")
                        self.driver.wait(20.0)
                    phase = "wait"
                if phase == "wait":
                    self._wait_for_tables()
                    phase = "harvest"
            except InputStopped:
                raise
            except InputPaused:
                self._hold_until_resumed()
            except (InputFocusLost, TargetWindowNotForeground):
                self._wait_for_focus()
            except VisualRouteError as exc:
                failures += 1
                logger.warning("Vision cycle: stage '%s' failed (%d/%d): %s", phase, failures, self.MAX_FAILURES, exc)
                self._save_failure()
                if phase == "go":
                    self._in_room = False  # a failed way: start it again
                # a failure in the grow room is retried right there: the taught way starts at the spawn, and from an
                # unknown spot it would only wander
                if failures >= self.MAX_FAILURES:
                    alert()
                    raise VisualRouteError(f"Остановился после {failures} неудач подряд. Последняя: {exc}") from exc
                self._say(f"Сбой {failures} из {self.MAX_FAILURES}: {exc}", mode="lost")
                self.driver.wait(4.0)

    def _hold_until_resumed(self) -> None:
        while self.driver.pause_event.is_set() and not self.stop_event.is_set():
            self.stop_event.wait(0.3)

    def _wait_for_focus(self, limit: float = 180.0) -> None:
        """Wait until the configured input mode can safely read/control the game."""
        message = "Игра скрыта или свёрнута. Жду" if self.screen.background_control else "Игра не в фокусе. Жду"
        self._say(message)
        started = time.monotonic()
        while not self.screen.is_automation_ready():
            if self.stop_event.is_set():
                raise InputStopped("stopped")
            if time.monotonic() - started > limit:
                raise VisualRouteError("Игровое окно не стало доступно.")
            self.stop_event.wait(1.0)

    # ---- way to the plantation ---------------------------------------------------------

    def _ensure_in_plantation(self) -> None:
        if self._in_room:
            return
        frame = self._frame()
        if is_platform(frame) or self._badges.scan(frame):
            # the purple grow lamps (or a table badge) are on screen: already on the platform with the tables
            logger.info("cycle: on the platform already")
            self._in_room = True
            return
        if not self.taught_runs:
            raise VisualRouteError("Нет записанного пути до плантации (F2).")
        self.at_platform_entry = False
        self.stairs_taken = False
        self.run_taught(self.taught_runs)
        if self.at_platform_entry:
            self._anchor_at_stairs_end()  # from here the room is walked by its plan
        else:
            self._enter_platform()
            if not is_platform(self._frame()):
                # never pretend to be in the room: if the way did not bring us to the purple light, it failed
                raise VisualRouteError("Путь закончился, а платформы (фиолетового света) не видно.")
        self._in_room = True

    def _anchor_at_stairs_end(self) -> None:
        """We stand where the stairs let out into the room: a known place on the plan. The heading comes from the picture
        (or the mini-map) through the turn between the map and the plan; without it the heading is taken as north."""
        # after the stairs and the left turn we face north by construction; otherwise the picture is asked
        heading = 0.0 if self.stairs_taken else self._plan_heading()
        self._plan_pose = (STAIRS_END[0], STAIRS_END[1], heading if heading is not None else 0.0)
        logger.info("plan: anchored at the end of the stairs, heading %.0f deg", self._plan_pose[2])

    def _plan_heading(self) -> float | None:
        pose = self._pose_now()
        return None if pose is None else (pose[2] + self._plan_rot) % 360.0

    def _enter_platform(self) -> None:
        """The way ends at the landing of the stairs; the platform with the tables is to the LEFT of it. Turn left in
        steps until the purple light of the grow room fills the view."""
        if self._came_down:
            # the rule of the place: from the landing the platform is to the left. The camera was not touched on the way
            # down, so the heading is still the one of the stairs: a quarter turn to the left.
            self._came_down = False
            self._say("Лестница позади: поворачиваю налево", self._frame(), mode="search")
            self._look(-90.0, 0.0)
            self.driver.wait(0.4)
        for _ in range(5):
            frame = self._frame()
            if is_platform(frame):
                return
            self._say("Лестница позади: поворачиваю налево, к платформе", frame, mode="search")
            self._look(-35.0, 0.0)
            self.driver.wait(0.4)
        logger.info("cycle: no purple light after turning left; the pilot will look around from here")

    # ---- knowing where we are on the learned map ----------------------------------------------

    def _context(self):
        """Pictures, mini-maps and the map of the taught runs, built once (None without taught runs)."""
        if self._nav is not None or not self.taught_runs:
            return self._nav
        from types import SimpleNamespace
        from .radar import RadarIndex
        from .taught import Localizer, TaughtPlan

        width = self._frame().shape[1]
        plans = [TaughtPlan(run, width, self.HORIZONTAL_FOV) for run in self.taught_runs]
        route_map = self.ensure_map(self.taught_runs, plans, width)
        if route_map is None:
            return None
        self._nav = SimpleNamespace(localizer=Localizer(self.taught_runs), radar=RadarIndex(self.taught_runs),
                                    plans=plans, width=width, map=route_map)
        if route_map.stairs is not None:
            bearing, length = route_map.stairs
            self._plan_rot = 90.0 - bearing                        # the stairs run east on the plan
            self.metres_per_second = (STAIRS_END[0] - STAIRS_START[0]) / max(0.5, length)
            logger.info("plan: map->plan turn %.1f deg, %.2f m per second of walking", self._plan_rot, self.metres_per_second)
        self._load_goals()
        return self._nav

    def _pose_now(self, frame: np.ndarray | None = None):
        """(x, y, heading) on the map, from the picture, or from the mini-map when the picture does not know the place."""
        nav = self._context()
        if nav is None:
            return None
        frame = frame if frame is not None else self._frame()
        hit = nav.localizer.locate(frame)
        if hit is not None and hit.inliers >= 18:
            run, index = hit.run, hit.index
            turn = pixels_to_degrees(hit.shift_x, nav.width, self.HORIZONTAL_FOV)
        else:
            near = nav.radar.locate(frame) if nav.radar.usable else None
            if near is None:
                return None
            run, index, turn = near.run, near.index, near.rotation
        yaw = nav.plans[run].yaw_at(nav.plans[run].key_times[index]) - turn
        pose = nav.map.pose(run, index, yaw)
        if pose is not None:
            self._last_pose = pose
        return pose

    # -- what the bot has learned: where the tables are -------------------------------------

    def _goals_path(self):
        return self.taught_runs[0].folder.parent / "table_goals.json" if self.taught_runs else None

    def _load_goals(self) -> None:
        import json
        path = self._goals_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else None
            if data and data.get("signature") == self._nav.map.signature():
                self._learned_goals = {k: (float(v[0]), float(v[1])) for k, v in data["goals"].items()}
                self._slot_of = {k: int(v) for k, v in data.get("slots", {}).items()}
        except (OSError, ValueError, KeyError):
            self._learned_goals = {}

    def _save_goals(self) -> None:
        import json
        path = self._goals_path()
        if path is None or self._nav is None:
            return
        try:
            path.write_text(json.dumps({"signature": self._nav.map.signature(),
                                        "goals": {k: list(v) for k, v in self._learned_goals.items()},
                                        "slots": self._slot_of}), encoding="utf-8")
        except OSError:
            logger.debug("could not save the table goals", exc_info=True)

    def _table_goals(self) -> dict[str, tuple[float, float]]:
        """Where each table of the profile is on the map: where the bot itself harvested it. The recordings show only
        that there are tables in a place (which of them is planted is for the bot to find out), see `_go_to_known_table`."""
        self._context()
        return dict(self._learned_goals)

    def _go_to_known_table(self, done: set[str]) -> bool:
        """No badge in view: walk over the map to a table whose place is known. True = it arrived there."""
        nav = self._context()
        if nav is None:
            return False
        from .navigator import MapNavigator

        goals = {k: v for k, v in self._table_goals().items() if k not in done and k not in self._goal_tried}
        area = nav.map.table_area()
        if not goals and area is not None and "__area__" not in self._goal_tried:
            goals = {"__area__": area}  # no table of the profile is known yet: go to where the tables stand
        if not goals:
            return False
        pose = self._pose_now()
        if pose is None:
            return False
        table_id = min(goals, key=lambda k: (goals[k][0] - pose[0]) ** 2 + (goals[k][1] - pose[1]) ** 2)
        self._goal_tried.add(table_id)
        name = "к столам" if table_id == "__area__" else next((t.name for t in self._tables() if t.table_id == table_id), table_id)
        navigator = MapNavigator(nav.map, self._pose_now, lambda degrees: self._look(degrees, 0.0),
                                 lambda seconds: self._hold("w", seconds))
        self._say(f"Иду по карте: {name}", mode="search")
        outcome = navigator.goto(goals[table_id], on_step=lambda p, path: self._show(
            status=f"Иду по карте: {name}", mode="search", stage="карта", frame=[nav.width, int(nav.width * 0.8)],
            target={}, radar=self._radar(p, goals[table_id], True)))
        logger.info("pilot: walk over the map to %s: %s", name, outcome)
        return outcome == "arrived"

    # ---- harvesting --------------------------------------------------------------------

    RECHECK_SECONDS = 300.0    # tables that were not ripe are looked at again after this long
    MAX_DETOURS = 4            # side steps around an obstacle before giving a table up

    def _table_by_id(self, table_id: str | None) -> TableProfile | None:
        if not table_id:
            return None
        return next((t for t in self._tables() if t.table_id == table_id), None)

    def _wanted(self, badge: TableBadge, ident: str | None, done: set, visited: set, survey: bool) -> bool:
        """Is this table worth walking to now? A ready badge always is. A growing one only if its own timer says it
        is due (the picture must then be checked up close), or if its timer is unknown (the first look at all)."""
        if badge.state == "idle":
            return False  # an empty table: nothing grows there, nothing to harvest or to wait for
        if ident is not None and (ident in done or ident in visited):
            return False
        if badge.state == "ready":
            return True
        table = self._table_by_id(ident)
        if table is not None:
            remaining = self._remaining(table)
            return remaining is not None and remaining <= 0
        return survey

    def _identify(self, frame: np.ndarray, badges: list[TableBadge]) -> dict[int, str]:
        if self.memory is None or not badges:
            return {}
        try:
            return self.memory.identify(frame, badges)
        except Exception:
            logger.debug("identify failed", exc_info=True)
            return {}

    def _sweep_for(self, wanted: Callable[[TableBadge, str | None], bool]) -> bool:
        """Turn on the spot (left, right, back) and stop as soon as a table worth walking to is in view."""
        for offset in (-70.0, 70.0, 70.0, -70.0):
            self._look(offset, 0.0)
            self.driver.wait(0.3)
            frame = self._frame()
            badges = self._badges.scan(frame)
            names = self._identify(frame, badges)
            if any(wanted(b, names.get(i)) for i, b in enumerate(badges)):
                return True
        return False

    def _harvest_pass(self) -> int:
        """One round through the grow room: every table worth a visit is walked up to and looked at up close.
        Ripe ones are harvested (and get their timer); unripe ones are only noted, to be looked at again later."""
        harvested = 0
        done: set[str] = set()      # tables harvested in this round
        visited: set[str] = set()   # tables checked up close in this round (ripe or not)
        if self._plan_pose is None and is_platform(self._frame()):
            # no known place (started inside the room): the pictures learned on earlier walks may tell where we are
            self._relocalize()
        if self._map_tour(done, visited):
            # every known table was walked up to by coordinates and looked at up close: the round is complete. No
            # wandering after it: looking for more tables by walking about is what ran the bot into the walls.
            if self._unripe_seen or any(self._remaining(t) is None for t in self._tables()):
                self._recheck_at = time.monotonic() + self.RECHECK_SECONDS
            return len(done)
        memory = self._plan_memory()
        if memory is not None:
            self._say(f"Память платформы: {memory.describe()}", mode="ok")
        if self._plan_pose is not None:
            # the room is known from its plan: walk it place by place instead of looking for the tables
            self._slots_seen: dict[int, str] = {}
            try:
                self._platform_tour(done, visited)
            except VisualRouteError as exc:
                logger.warning("pilot: the walk over the plan failed (%s); looking for the tables by their badges", exc)
            harvested = len(done)
        # More tables may be planted than the profile knows (at most MAX_TABLES places): the round ends when
        # there is nothing left to walk to, not when the known tables are done.
        survey = any(self._remaining(t) is None for t in self._tables()) or len(self._tables()) < MAX_TABLES
        self._unripe_seen = False
        self._goal_tried = set()
        deadline = time.monotonic() + self.PASS_SECONDS
        explore = aligns = give_ups = detours = unripe_checks = blocked = idle_checks = 0
        settle = 0.3
        swept = False
        while time.monotonic() < deadline and harvested < MAX_TABLES:
            self.driver.wait(settle)
            settle = 0.2
            frame = self._frame()
            height, width = frame.shape[:2]
            badges = self._badges.scan(frame)
            names = self._identify(frame, badges)

            def offset(b: TableBadge) -> float:
                return abs(b.x + b.width / 2 - width / 2)

            # 1) standing at a table (its menu is up): decide by the badge seen up close
            if badges and self.prompt_reader.harvest_visible(frame):
                index, here = min(enumerate(badges), key=lambda item: offset(item[1]))
                if offset(here) < 0.2 * width:
                    outcome = self._at_table(frame, badges, index, names, done, visited)
                    if outcome == "idle":
                        idle_checks += 1
                        self._hold("s", 0.5)
                        if idle_checks >= 4:
                            break
                    elif outcome == "harvested":
                        harvested += 1
                        explore = give_ups = detours = 0
                        swept = False
                        self._hold("s", 0.4)  # a step back: the next badge should come into view
                    elif outcome == "failed":
                        give_ups += 1
                        if give_ups >= 3:
                            break
                    else:  # "unripe"
                        unripe_checks += 1
                        if unripe_checks >= 3 * MAX_TABLES + 3:
                            break  # the same tables over and over: nothing new will come of this round
                        explore = detours = 0
                        swept = False
                        self._hold("s", 0.5)
                    continue

            # 2) which tables are worth walking to from here
            wanted = lambda b, ident: self._wanted(b, ident, done, visited, survey)  # noqa: E731
            candidates = [(i, b) for i, b in enumerate(badges) if wanted(b, names.get(i))]
            if not candidates:
                if badges:
                    self._say("Здесь больше нечего проверять", frame, mode="search")
                    if swept:
                        break
                    swept = True
                    if not self._sweep_for(wanted):
                        break
                    continue
                if self._go_to_known_table(done):
                    continue  # it arrived where a table is known to be: look again from there
                if explore >= self.EXPLORE_CHUNKS:
                    raise VisualRouteError("Не вижу ни одного стола с значком. Проверь, что бот в комнате с растениями.")
                self._say("Осматриваюсь: ищу столы", frame, mode="search")
                if explore % 3 == 0:
                    # a full turn on the spot first (a badge is visible from ~9 m). On the platform the heading is
                    # already the right one (the tables are ahead); elsewhere face the most open direction.
                    if self._scan_around(width, keep_heading=is_platform(frame)):
                        explore += 1
                        continue
                else:
                    if self._explore_walk() == "blocked":
                        blocked += 1
                        self._say("Упёрся: обхожу боком", frame, mode="search")
                        # a pillar or a table: step aside (left first), further each time, then on in the same direction
                        self._hold("a" if blocked % 2 else "d", 0.8 * ((blocked + 1) // 2))
                        self._hold("w", 0.6)
                explore += 1
                continue

            index, target = min(candidates, key=lambda item: (item[1].state != "ready", offset(item[1])))
            cx, cy = target.x + target.width / 2, target.y + target.height / 2
            err_yaw = pixels_to_degrees(cx - width / 2, width, self.HORIZONTAL_FOV)
            err_pitch = pixels_to_degrees(cy - self.AIM_HEIGHT * height, width, self.HORIZONTAL_FOV)
            box = self._box(target, "иду")
            if (abs(err_yaw) > self.ALIGN_YAW or abs(err_pitch) > self.ALIGN_PITCH_TOLERANCE) and aligns < 6:
                aligns += 1
                self._say(f"Навожусь на стол: {abs(err_yaw):.0f}°", frame, target=box)
                self._look(err_yaw if abs(err_yaw) > self.ALIGN_YAW else 0.0,
                           err_pitch if abs(err_pitch) > self.ALIGN_PITCH_TOLERANCE else 0.0)
                continue
            aligns = 0
            logger.info("pilot: going to a %s table at (%.0f,%.0f), badge width %d, turn %.1f deg",
                        target.state, cx, cy, target.width, err_yaw)
            self._say("Иду к столу" if target.state == "ready" else "Иду проверить стол", frame, target=box)
            outcome = self._walk_to_table(target)
            settle = 0.12
            if outcome == "stuck":
                detours += 1
                if detours > self.MAX_DETOURS:
                    raise VisualRouteError("Не могу обойти препятствие на пути к столу.")
                # something stands in the way (a pillar, a table): step aside, further each time, then on
                self._say("Обхожу препятствие", frame, target=box)
                self._hold("d" if detours % 2 else "a", 0.8 * detours)
                self._hold("w", 0.8)
        # what to do next: look again in 5 minutes if some table was not ripe or has no timer yet
        if self._unripe_seen or any(self._remaining(t) is None for t in self._tables()):
            self._recheck_at = time.monotonic() + self.RECHECK_SECONDS
        return harvested

    # ---- the tables by coordinates -------------------------------------------------------------

    TABLE_AHEAD = 4.5        # mini-map pixels from where the author stood to the middle of the table (about 1.2 m)
    IDLE_MEMORY = 1800.0     # an empty table is left out of the rounds for this long, then looked at again
    STATE_WORDS = {"idle": "пустой", "growing": "растёт", "ready": "готов"}

    def _table_spot(self, slot: int) -> tuple[float, float]:
        x, y, heading = self._route.tables[slot]
        return (x + self.TABLE_AHEAD * math.sin(math.radians(heading)), y - self.TABLE_AHEAD * math.cos(math.radians(heading)))

    def _map_read_badges(self, states: dict[int, str], seen_from: dict[int, float]) -> None:
        """One look: every badge on screen is given to the table that stands in its direction from where we are. The
        game draws the badge over each table: a workbench (empty), a clock (growing), a check mark (ready). A badge
        counts only when exactly one table lies in its direction; a nearer look replaces a farther one."""
        frame = self._frame()
        pose = self._map_pose(2)
        if pose is None:
            return
        width = frame.shape[1]
        for badge in self._badges.scan(frame):
            bearing = pose.heading + pixels_to_degrees(badge.x + badge.width / 2 - width / 2, width, self.HORIZONTAL_FOV)
            fits = []
            for slot in range(len(self._route.tables)):
                spot = self._table_spot(slot)
                distance = pose.distance_to(*spot)
                if distance < 2.0:
                    continue
                # a table close by fills a wide angle, a far one a narrow one
                if abs(wrap(pose.bearing_to(*spot) - bearing)) <= max(7.0, min(20.0, math.degrees(math.atan(2.5 / distance)))):
                    fits.append((distance, slot))
            if len(fits) != 1:
                continue
            distance, slot = fits[0]
            if distance <= seen_from.get(slot, math.inf):
                if states.get(slot) != badge.state:
                    logger.info("map tour: table %d looks %s from %.0f m", slot + 1, badge.state, distance / 3.9)
                states[slot], seen_from[slot] = badge.state, distance

    def _map_tour(self, done: set, visited: set) -> bool:
        """The round of the room by coordinates: along the rows, judging every table by the badge the game draws over it
        (a workbench: empty; a clock: growing; a check mark: ready). An empty table is passed and left out of the next
        rounds. A growing one is noted (its timer, or a look in five minutes). A ready one is harvested on the spot and
        its timer starts. No menu is waited for at a table that is not ready. True: the round was made. False: it
        cannot be made here (no recorded tables, the mini-map not readable) and the older ways may try."""
        if not self.use_map_route:
            return False
        if self._locator is None or self._route is None:
            if not self.taught_runs:
                return False
            try:
                from .taught import TaughtPlan

                width = self._frame().shape[1]
                plans = [TaughtPlan(run, width, self.HORIZONTAL_FOV) for run in self.taught_runs]
                if not self._map_ready(self.taught_runs, plans):
                    return False
            except Exception:
                logger.exception("map tour: could not prepare the mini-map")
                return False
        route = self._route
        if not route.tables:
            logger.info("map tour: no table was marked in the taught runs (F6 or E at a table while recording)")
            return False
        self._map_frame = [1280, 1024]
        self._map_seen = None
        if self._map_pose() is None:
            logger.info("map tour: the mini-map is not readable here")
            return False
        self._slots_seen = {}
        self._unripe_seen = False
        slots = list(range(len(route.tables)))
        now = time.monotonic()
        idle_until = getattr(self, "_idle_until", {})
        self._idle_until = idle_until
        states: dict[int, str] = {slot: "idle" for slot in slots if idle_until.get(slot, 0.0) > now}
        # a table harvested earlier whose timer is still running is growing: nothing to look at until the timer is out
        for slot in slots:
            table = self._table_of_slot(slot)
            left = self._remaining(table) if table is not None else None
            if left is not None and left > 0:
                states[slot] = "growing"
        seen_from: dict[int, float] = {slot: 0.0 for slot in states}   # known without looking: not looked at again yet
        logger.info("map tour: %d tables%s", len(slots), "" if not states else ", not gone to: " + ", ".join(
            f"{k + 1} ({'пустой' if states[k] == 'idle' else 'идёт таймер'})" for k in sorted(states)))

        # One walk along the rows. At the spot in the aisle next to a table its badge is read (there it is the biggest
        # one ahead, so it cannot be taken for another table's): empty -> on, and out of the rounds; growing -> noted;
        # ready -> harvested right there. A check mark seen from afar only decides which table is gone to first.
        hints: dict[int, str] = {}
        results: dict[int, str] = {}
        tries: dict[int, int] = {}
        failures = 0
        self._map_read_badges(hints, {})
        while any(slot not in states for slot in slots):
            pose = self._map_need()
            open_slots = [k for k in slots if k not in states]
            slot = min(open_slots, key=lambda k: (hints.get(k) != "ready", pose.distance_to(route.tables[k][0], route.tables[k][1])))
            x, y, heading = route.tables[slot]
            self._mark(f"плантация стол {slot + 1}")
            self._show(status=f"Иду к столу {slot + 1}" + (" (вижу галочку)" if hints.get(slot) == "ready" else ""),
                       mode="ok", stage="плантация", frame=self._map_frame)
            try:
                self._map_go(x, y, f"стол {slot + 1}", arrive=1.5)
                badge = self._map_own_badge(slot)
                if badge is None:
                    states[slot] = "unseen"
                elif badge.state == "ready":
                    states[slot] = "ready"
                    results[slot] = self._map_harvest(slot, badge, done, visited)
                    logger.info("map tour: table %d: ready -> %s", slot + 1, results[slot])
                    if results[slot] == "вблизи ещё растёт":
                        states[slot] = "growing"            # the check mark was a neighbour's
                    elif results[slot] != "собран" and tries.get(slot, 0) < 1:
                        tries[slot] = tries.get(slot, 0) + 1
                        del states[slot]                    # not harvested: once more, from the start
                        hints[slot] = "ready"
                else:
                    states[slot] = badge.state
                    logger.info("map tour: table %d: %s", slot + 1, self.STATE_WORDS.get(badge.state, badge.state))
                if states.get(slot) in ("idle", "growing") or (states.get(slot) == "ready" and results.get(slot) == "собран"):
                    for mate in self._mates(slot):         # the same table seen from the other side: judged already
                        states.setdefault(mate, "growing" if states[slot] == "ready" else states[slot])
                self._map_read_badges(hints, {})   # what else shows a check mark from here
            except VisualRouteError as exc:
                failures += 1
                states[slot] = "unreached"
                logger.warning("map tour: table %d not reached: %s", slot + 1, exc)
                self._save_trace(f"сбой стол {slot + 1}")
                if failures >= 3:
                    break
        for slot in slots:
            state = states.get(slot)
            if state == "idle":
                for k in [slot, *self._mates(slot)]:
                    idle_until[k] = now + self.IDLE_MEMORY
                self._slots_seen[slot] = "idle"
            elif state == "growing":
                idle_until.pop(slot, None)
                self._slots_seen[slot] = "growing"
                self._unripe_seen = True
            elif state == "ready":
                idle_until.pop(slot, None)
        words = {**self.STATE_WORDS, "unseen": "значка не видно", "unreached": "не дошёл"}
        logger.info("map tour done: harvested %d; %s", len(done), ", ".join(
            f"{k + 1}: {results.get(k) or words.get(states.get(k), 'не смотрел')}" for k in slots))
        if all(states.get(slot) in ("unreached", None) for slot in slots):
            raise VisualRouteError("Не смог оценить ни один стол на плантации.")
        return True

    OWN_BADGE_CONE = 20.0    # degrees to each side: on the recordings the badge of the table stood at is within 18

    def _badge_ahead(self, frame: np.ndarray) -> TableBadge | None:
        """The biggest badge about straight ahead: the one of the table we stand at (those of the rows behind are smaller)."""
        width = frame.shape[1]
        ahead = [b for b in self._badges.scan(frame)
                 if abs(pixels_to_degrees(b.x + b.width / 2 - width / 2, width, self.HORIZONTAL_FOV)) <= self.OWN_BADGE_CONE]
        return max(ahead, key=lambda b: b.width) if ahead else None

    def _find_badge_ahead(self) -> TableBadge | None:
        """The badge of the table in front of us. Up close it hangs high and is cut off by the top of the screen: look
        up a little, then take a short step back."""
        badge = self._badge_ahead(self._frame())
        if badge is None:
            self._look(0.0, -8.0 / self.TURN_GAIN)   # up
            self.driver.wait(0.25)
            badge = self._badge_ahead(self._frame())
            self._look(0.0, 8.0 / self.TURN_GAIN)    # and back down
        return badge

    def _map_own_badge(self, slot: int) -> TableBadge | None:
        """Standing in the aisle at a table: turn to the table and read its badge. The turn is to where the table is
        from where we really stand (a step beside the author's spot must not put another table's badge in the middle)."""
        pose = self._map_need()
        self._map_face(pose.bearing_to(*self._table_spot(slot)), 3.0)
        self.driver.wait(0.25)
        return self._find_badge_ahead()

    def _at_the_place(self, slot: int) -> bool:
        """Do we stand at the place of this table, looking the way the author looked when harvesting it?"""
        pose = self._map_pose(2)
        if pose is None:
            return False
        x, y, heading = self._route.tables[slot]
        return pose.distance_to(x, y) <= 4.0 and abs(wrap(pose.heading - heading)) <= 28.0

    def _map_harvest(self, slot: int, badge: TableBadge, done: set, visited: set) -> str:
        """At a ready table: bring up its menu (a step towards the badge with the camera on it, as a player does) and
        harvest. E is pressed only when the map says we are at THIS table's place and facing it."""
        frame = self._frame()
        if not self.prompt_reader.harvest_visible(frame):
            self._walk_to_table(badge)
            frame = self._frame()
            if not self.prompt_reader.harvest_visible(frame):
                return "меню сбора не появилось"
        if not self._at_the_place(slot):
            pose = self._map_need()
            self._map_face(pose.bearing_to(*self._table_spot(slot)), 3.0)   # back onto the table of this place
            self.driver.wait(0.25)
            frame = self._frame()
            if not self._at_the_place(slot) or not self.prompt_reader.harvest_visible(frame):
                return "не у своего стола"
        here = self._find_badge_ahead()          # up close the badge may be out of sight: look for it
        frame = self._frame()
        if here is None:
            return "меню есть, значка не видно"
        badges = self._badges.scan(frame)
        width = frame.shape[1]
        ahead = [i for i, b in enumerate(badges)
                 if abs(pixels_to_degrees(b.x + b.width / 2 - width / 2, width, self.HORIZONTAL_FOV)) <= self.OWN_BADGE_CONE]
        if not ahead:
            return "меню есть, значка не видно"
        index = max(ahead, key=lambda i: badges[i].width)
        outcome = self._at_table(frame, badges, index, {}, done, visited, slot)
        return {"idle": "пустой", "harvested": "собран", "failed": "сбор не подтверждён", "unripe": "вблизи ещё растёт"}.get(outcome, outcome)

    def _at_table(self, frame: np.ndarray, badges: list[TableBadge], index: int, names: dict, done: set, visited: set,
                  slot: int | None = None) -> str:
        """Standing at a table with its menu up: decide by the badge seen up close.
        "idle" (empty table), "harvested", "failed" (ripe but nothing came), or "unripe" (the clock is on)."""
        here = badges[index]
        ident = names.get(index)
        if here.state == "idle":
            self._say("Пустой стол: иду дальше", frame, mode="search", target=self._box(here, "пустой"))
            if slot is not None:
                self._slots_seen[slot] = "idle"
            return "idle"
        if here.state == "ready":
            if self._harvest(frame, badges, here, ident, done, slot):
                visited.add(ident or "")
                if slot is not None:
                    self._slots_seen[slot] = "harvested"
                return "harvested"
            return "failed"
        # the clock is on up close: not ripe, no timer. Keep where it stands, come back later.
        key = ident or self._remember_unripe(frame, badges, here)
        visited.add(key)
        self._unripe_seen = True
        if slot is not None:
            self._slots_seen[slot] = "growing"
        logger.info("pilot: table up close is still growing (%s)", key)
        self._say("Стол ещё растёт: загляну позже", frame, mode="search", target=self._box(here, "растёт"))
        return "unripe"

    # ---- walking the room by its plan ---------------------------------------------------------

    def _floorplan(self) -> FloorPlan:
        if self._floor is None:
            self._floor = FloorPlan()
        return self._floor

    def _face(self, bearing: float) -> None:
        """Turn to a heading of the plan (degrees clockwise from north); the picture checks the result."""
        x, y, heading = self._plan_pose
        error = wrap(bearing - heading)
        if abs(error) > 3.0:
            heading = (heading + self._gyro_turn(error)) % 360.0  # what the mini-map measured, not what was commanded
        self._plan_pose = (x, y, heading)

    def _plan_memory(self) -> PlatformMemory | None:
        if self._memory_of_platform is None and self.taught_runs:
            try:
                self._memory_of_platform = PlatformMemory(self.taught_runs[0].folder.parent.parent / "platform_views",
                                                          self.HORIZONTAL_FOV)
            except Exception:
                logger.exception("Could not open the platform memory")
        return self._memory_of_platform

    def _relocalize(self) -> bool:
        """Where are we in the room? Ask the pictures learned on earlier walks. True = the place was found."""
        memory = self._plan_memory()
        if memory is None or not memory.usable:
            return False
        found = memory.locate(self._frame())
        if found is None:
            return False
        x, y, heading, inliers = found
        logger.info("plan: relocalized from learned pictures: (%.1f, %.1f) heading %.0f deg (%d matching points)", x, y, heading, inliers)
        self._plan_pose = (x, y, heading)
        return True

    def _walk_leg(self, seconds: float, start: tuple[float, float], end: tuple[float, float], heading: float) -> float:
        """Hold W for `seconds` as ONE movement from `start` to `end` (metres on the plan), looking at the screen
        all the time. Returns the share of the way walked (1.0 = all of it; less if the picture stopped changing:
        something is in the way). A small picture is kept every half second: if the stretch ends at a place that is
        certain (a table with its menu), those pictures get their places by spreading them along the stretch."""
        from .models import RouteEvent
        started = time.monotonic()
        previous = None
        still = 0
        kept: list = []
        walked = 1.0
        try:
            self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= seconds:
                    break
                frame = self._frame()
                kept.append((cv2.resize(frame, (640, 512), interpolation=cv2.INTER_AREA), elapsed / seconds))
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (64, 51)).astype(np.float32)
                if previous is not None and elapsed > 0.8:
                    still = still + 1 if float(np.abs(small - previous).mean()) < 1.0 else 0
                    if still >= 4:
                        walked = max(0.0, elapsed / seconds)
                        break
                previous = small
                self.driver.wait(min(0.45, max(0.05, seconds - elapsed)))
        finally:
            self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))
        if walked >= 0.999:
            self._leg_views += [(f, (start[0] + (end[0] - start[0]) * fr, start[1] + (end[1] - start[1]) * fr, heading))
                                for f, fr in kept]
        return walked

    def _verify_views(self, x: float, y: float, heading: float, frame: np.ndarray) -> None:
        """We stand at a table with its menu up: the place is certain. The pictures of the stretch that led here and the
        one of this very spot become certain places of the memory."""
        self._walk_views += self._leg_views
        self._leg_views = []
        self._walk_views.append((cv2.resize(frame, (640, 512), interpolation=cv2.INTER_AREA), (x, y, heading)))

    def _keep_views(self) -> None:
        memory = self._plan_memory()
        views, self._walk_views, self._leg_views = self._walk_views, [], []
        if memory is None or len(views) < 5:
            return
        try:
            kept = memory.add_walk(views)
            logger.info("memory of the platform: %d pictures kept from this walk, now %s", kept, memory.describe())
        except Exception:
            logger.exception("Could not keep the pictures of this walk")

    def _plan_walk(self, goal: tuple[float, float], retries: int = 0) -> bool:
        """Walk to a place of the plan along a way that keeps clear of the tables and the pillars."""
        plan = self._floorplan()
        x, y, _ = self._plan_pose
        route = plan.route((x, y), goal)
        if not route:
            logger.warning("plan: no way from (%.1f,%.1f) to (%.1f,%.1f)", x, y, *goal)
            return False
        self._show(status="Иду по плану плантации", mode="ok", stage=ZONE_TITLES["platform"], frame=[1280, 1024],
                   radar=plan.view(self._plan_pose, goal), target={})
        for corner in route[1:]:
            x, y, heading = self._plan_pose
            distance = math.hypot(corner[0] - x, corner[1] - y)
            if distance < 0.2:
                continue
            bearing = math.degrees(math.atan2(corner[0] - x, corner[1] - y)) % 360.0
            self._face(bearing)
            walked = self._walk_leg(distance / self.metres_per_second, (x, y), corner, self._plan_pose[2])
            if walked < 0.999:
                # something is in the way (a rail, a table corner): where we are is only roughly known now
                self._plan_pose = (x + (corner[0] - x) * walked, y + (corner[1] - y) * walked, self._plan_pose[2])
                self._leg_views = []  # a stretch that ended against something gives no trustworthy places
                self._hold("s", 0.3)
                logger.info("plan: blocked after %.0f%% of the way to (%.1f,%.1f)", 100 * walked, *corner)
                return self._relocalize() and retries < 2 and self._plan_walk(goal, retries + 1)
            self._plan_pose = (corner[0], corner[1], self._plan_pose[2])
        return True

    def _tend_side(self, side, done: set, visited: set) -> str:
        """At a standing place, face one table, look at it, step up for the menu if needed, decide, step back."""
        x, y, _ = self._plan_pose
        self._face(side.heading)
        self.driver.wait(0.3)
        frame = self._frame()
        width = frame.shape[1]
        badges = self._badges.scan(frame)
        if not badges:
            logger.info("plan: place %d: no badge in view", side.slot)
            return "none"
        index, near = min(enumerate(badges), key=lambda item: abs(item[1].x + item[1].width / 2 - width / 2))
        if abs(near.x + near.width / 2 - width / 2) > 0.3 * width:
            logger.info("plan: place %d: the nearest badge is off to the side", side.slot)
            return "none"
        stepped = 0.0
        if not self.prompt_reader.harvest_visible(frame):
            started = time.monotonic()
            self._walk_to_table(near)  # a step or two closer, aimed at the badge, until the menu shows
            stepped = time.monotonic() - started
            frame = self._frame()
            badges = self._badges.scan(frame)
            if not badges:
                outcome = "none"
            else:
                index, near = min(enumerate(badges), key=lambda item: abs(item[1].x + item[1].width / 2 - width / 2))
                outcome = "seen" if not self.prompt_reader.harvest_visible(frame) else None
        else:
            outcome = None
        if outcome is None:
            self._verify_views(x, y, side.heading, frame)  # the menu is up: where we stand and where we face is certain
            names = self._identify(frame, badges)
            outcome = self._at_table(frame, badges, index, names, done, visited, side.slot)
        elif outcome == "none":
            self._leg_views = []  # the table was not where the plan says: this stretch is not trusted
            self._relocalize()
        if stepped > 0.1:
            self._hold("s", min(stepped, 3.0))  # back to the standing place
        self._plan_pose = (x, y, self._plan_pose[2])
        return outcome

    def _platform_tour(self, done: set, visited: set) -> None:
        """The four standing places of the plan, nearest first; at each one look at the tables on its sides."""
        plan = self._floorplan()
        x, y, _ = self._plan_pose
        for spot in plan.tour((x, y)):
            if not self._plan_walk((spot.x, spot.y)):
                raise VisualRouteError(f"На плане нет пути к месту {spot.name}.")
            for side in spot.sides:
                outcome = self._tend_side(side, done, visited)
                logger.info("plan: place %s, table %d: %s", spot.name, side.slot + 1, outcome)
        logger.info("plan: tour done; slots %s", getattr(self, "_slots_seen", {}))
        self._keep_views()

    def _remember_unripe(self, frame: np.ndarray, badges: list[TableBadge], here: TableBadge) -> str:
        """A table that is not ripe has no name yet: keep how it looks under a temporary one."""
        key = f"anon_{time.strftime('%H%M%S')}_{int(here.x)}"
        if self.memory is not None:
            try:
                self.memory.remember(key, frame, badges)
            except Exception:
                logger.debug("could not remember an unripe table", exc_info=True)
        return key

    WALK_MAX = 5.0   # longest single walk towards a table, seconds

    @staticmethod
    def _texture(frame: np.ndarray) -> float:
        """How much there is to see in the middle of the picture: a bare wall scores near zero, an open room high."""
        import cv2
        height, width = frame.shape[:2]
        crop = cv2.cvtColor(frame[int(height * 0.2):int(height * 0.7), int(width * 0.2):int(width * 0.8)], cv2.COLOR_RGB2GRAY)
        return float(cv2.Laplacian(cv2.resize(crop, (320, 160)), cv2.CV_64F).var())

    def _scan_around(self, width: int = 1280, stops: int = 8, keep_heading: bool = False) -> bool:
        """Turn once all the way round on the spot looking for table badges. True = one is in view (the camera is
        left on it). Otherwise the camera is turned towards the direction with the most to see."""
        step = 360.0 / stops
        textures: list[float] = []
        for index in range(stops):
            frame = self._frame()
            if self._badges.scan(frame):
                return True
            textures.append(self._texture(frame))
            self._look(step, 0.0)
            self.driver.wait(0.3)
        if keep_heading:
            return False  # a full turn brought us back to the heading we started with
        best = max(range(stops), key=lambda i: textures[i])
        logger.info("pilot: no badge all around; most open direction is %d deg (texture %.0f, wall-like %.0f)",
                    int(best * step), textures[best], min(textures))
        turn = best * step
        self._look(turn - 360.0 if turn > 180.0 else turn, 0.0)
        return False

    def _explore_walk(self) -> str:
        """Walk forward in one go. Stops when a table badge shows up ("seen") or when the picture stops changing, which
        means a wall or a pillar ("blocked")."""
        import cv2
        from .models import RouteEvent
        started = time.monotonic()
        previous = None
        still = 0
        try:
            self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
            while time.monotonic() - started < self.EXPLORE_WALK:
                frame = self._frame()
                if self._badges.scan(frame):
                    return "seen"
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (64, 51)).astype(np.float32)
                if previous is not None and time.monotonic() - started > 0.5:
                    still = still + 1 if float(np.abs(small - previous).mean()) < 1.2 else 0
                    if still >= 3:
                        return "blocked"
                previous = small
                self.driver.wait(0.05)
            return "walked"
        finally:
            self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))

    def _walk_to_table(self, target: TableBadge) -> str:
        """Walk to the table as ONE movement: W stays down, its badge is followed and the camera eased onto it, until
        the table menu («Сбор урожая») shows up. Returns "arrived", "lost", "stuck" or "timeout"."""
        from .taught import pixels_to_degrees
        from .models import RouteEvent

        started = time.monotonic()
        missed = ticks = 0
        tracked = (target.x + target.width / 2, target.y + target.height / 2)
        still_since, reference = started, (tracked[1], float(target.width))
        try:
            self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
            while time.monotonic() - started < self.WALK_MAX:
                frame = self._frame()
                height, width = frame.shape[:2]
                badges = self._badges.scan(frame)
                if not badges:
                    missed += 1
                    if missed >= 4:
                        return "lost"
                    continue
                # keep following the same badge: the one nearest to where it was a moment ago
                badge = min(badges, key=lambda b: (b.x + b.width / 2 - tracked[0]) ** 2 + (b.y + b.height / 2 - tracked[1]) ** 2)
                missed = 0
                ticks += 1
                cx, cy = badge.x + badge.width / 2, badge.y + badge.height / 2
                tracked = (cx, cy)
                if ticks % 2 == 0 and self.prompt_reader.harvest_visible(frame):
                    return "arrived"  # the menu is up: we are at the table
                now = time.monotonic()
                if abs(cy - reference[0]) >= 4 or abs(badge.width - reference[1]) >= 2:
                    still_since, reference = now, (cy, float(badge.width))
                elif now - still_since > 1.4:
                    return "stuck"  # W is down but the badge does not grow: something is in the way
                yaw = pixels_to_degrees(cx - width / 2, width, self.HORIZONTAL_FOV)
                pitch = pixels_to_degrees(cy - self.AIM_HEIGHT * height, width, self.HORIZONTAL_FOV)
                if abs(yaw) > 2.5 or abs(pitch) > self.ALIGN_PITCH_TOLERANCE:
                    self._look(max(-8.0, min(8.0, yaw * 0.7)) if abs(yaw) > 2.5 else 0.0,
                               max(-5.0, min(5.0, pitch * 0.5)) if abs(pitch) > self.ALIGN_PITCH_TOLERANCE else 0.0)
                else:
                    self.driver.wait(0.03)
            return "timeout"
        finally:
            self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))

    def _spot_tables(self) -> list[int]:
        """Which table each spot of the route belongs to (a table marked from both sides has two spots)."""
        from .map_route import table_groups

        return table_groups(self._route.tables)

    def _mates(self, slot: int) -> list[int]:
        """The other spots of the same table."""
        groups = self._spot_tables()
        return [k for k, g in enumerate(groups) if g == groups[slot] and k != slot]

    def _table_of_slot(self, slot: int) -> TableProfile | None:
        """The profile table of a spot of the room: table N is the N-th by its number in the route."""
        tables = sorted(self._tables(), key=lambda t: t.route_index)
        index = self._spot_tables()[slot] if self._route is not None and 0 <= slot < len(self._route.tables) else slot
        return tables[index] if 0 <= index < len(tables) else None

    def _pick_table(self, ident: str | None, done: set[str], slot: int | None = None) -> TableProfile | None:
        if slot is not None and self._route is not None:
            # by coordinates the place tells which table this is: how a table looks (or a guess made earlier) does not
            table = self._table_of_slot(slot)
            if table is not None:
                return table if table.table_id not in done else None
        table = self._table_by_id(ident)
        if table is not None and table.table_id not in done:
            return table
        if slot is not None:  # the place of the room tells which table this is, once it has been learned
            for table_id, known in self._slot_of.items():
                if known == slot and table_id not in done and self._table_by_id(table_id) is not None:
                    return self._table_by_id(table_id)
        # not remembered yet: the next table by count (the one that has waited longest among those not harvested yet)
        free = [t for t in self._tables() if t.table_id not in done]
        if not free:
            # a ripe table the profile does not have: a newly planted one, if there is still room for it
            return self.on_new_table() if self.on_new_table is not None else None
        return min(free, key=lambda t: (self._remaining(t) if self._remaining(t) is not None else 0, t.route_index))

    def _harvest(self, frame: np.ndarray, badges: list[TableBadge], target: TableBadge, ident: str | None, done: set[str],
                 slot: int | None = None) -> bool:
        table = self._pick_table(ident, done, slot)
        if table is None:
            logger.warning("pilot: a ready table in front of me, but every table of the profile is already done")
            return False
        remaining = self._remaining(table)
        if remaining is not None and remaining > 120:
            logger.info("pilot: %s is ready by sight although its timer says %s", table.name, format_countdown(remaining))
        interaction = self.profile.interaction
        hold = interaction.hold_seconds if interaction.mode == "hold" else 0.12
        items: list = []
        for attempt in range(2):  # the first press can miss the menu
            self._say(f"Собираю: {table.name}", frame, target=self._box(target, "жму E"))
            if attempt:
                self.driver.wait(1.0)
            self.driver.play_key(interaction.key, hold)
            items = watch_loot(self.loot_reader, self._frame, self.driver.wait, max(0.5, interaction.harvest_seconds))
            if items:
                break
        if not items:
            logger.warning("pilot: %s: harvest not confirmed, timer NOT reset", table.name)
            self._say(f"{table.name}: пакеты не получены", frame, mode="lost")
            return False
        if self.on_loot is not None:
            try:
                self.on_loot(table, items)
            except Exception:
                logger.exception("Loot callback failed")
        with self.profile_lock:
            reset_table_timer(table, self.profile, time.time())  # the timer of THIS table starts only now
        if self.memory is not None and ident and ident.startswith("anon_"):
            try:
                self.memory.rename(ident, table.table_id)  # it was seen before as an unnamed table
            except Exception:
                logger.debug("rename failed", exc_info=True)
        if self.on_harvest_view is not None:
            try:
                self.on_harvest_view(table, frame)
            except Exception:
                logger.exception("Table view callback failed")
        if slot is not None:
            self._slot_of[table.table_id] = slot  # remembered: this table stands at this place of the room
            self._save_goals()
        pose = self._pose_now(frame)  # where the bot stood when it harvested: that is where this table is
        if pose is not None:
            self._learned_goals[table.table_id] = (pose[0], pose[1])
            self._save_goals()
        logger.info("pilot: %s harvested and remembered%s", table.name,
                    "" if pose is None else f" at map {pose[0]:.1f},{pose[1]:.1f}")
        done.add(table.table_id)
        return True

    # ---- waiting -----------------------------------------------------------------------

    def _wait_for_tables(self) -> None:
        """Stand in the grow room until a table is due by its timer, the 5-minute re-check is due, or a ready badge shows."""
        self.overlay_busy = False  # let the plantation overlay draw the timers of the tables in view
        next_look = time.monotonic() + 5.0
        try:
            while True:
                tables = self._tables()
                remaining = [r for r in (self._remaining(t) for t in tables) if r is not None]
                if any(r <= 0 for r in remaining):
                    return
                recheck = None
                if self._recheck_at is not None:
                    recheck = max(0, int(self._recheck_at - time.monotonic()))
                    if recheck <= 0:
                        self._recheck_at = None
                        return
                parts = []
                if remaining:
                    parts.append(f"ближайший сбор через {format_countdown(min(remaining))}")
                if recheck is not None:
                    parts.append(f"проверю несозревшие через {format_countdown(recheck)}")
                self._set_state(RunState.WAITING, "Жду: " + (", ".join(parts) or "нет таймеров") + ".")
                self.driver.wait(1.0)
                if time.monotonic() >= next_look:
                    next_look = time.monotonic() + 5.0
                    if any(b.state == "ready" for b in self._badges.scan(self._frame())):
                        logger.info("pilot: a table looks ready before its timer ran out")
                        return
        finally:
            self.overlay_busy = True
