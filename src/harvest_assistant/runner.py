"""Due-table scheduler and fail-closed route/harvest state machine."""

from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Any, Callable

from .input_driver import InputDriver, InputInterrupted, InputStopped
from .laboratory import marker_available, parse_steps
from .models import (
    Profile,
    RecordedRoute,
    RunSnapshot,
    RunState,
    ScreenMarker,
    TableProfile,
    TableStatus,
    Waypoint,
    remaining_seconds,
    reset_table_timer,
    start_table_timer,
)
from .loot import LootItem, LootReader, watch_loot
from .screen import ScreenService
from .vision import VisionService


class RouteMismatch(RuntimeError):
    pass


logger = logging.getLogger(__name__)


class RouteRunner:
    def __init__(
        self,
        profile: Profile,
        screen: ScreenService,
        vision: VisionService,
        driver: InputDriver,
        on_state_change: Callable[[RunSnapshot], None],
        profile_lock: Any | None = None,
        loot_reader: LootReader | None = None,
        on_loot: Callable[[TableProfile, list[LootItem]], None] | None = None,
        on_cycle: Callable[[], None] | None = None,
        on_harvest_view: Callable[[TableProfile, Any], None] | None = None,
    ) -> None:
        self.profile = profile
        self.screen = screen
        self.vision = vision
        self.driver = driver
        self.on_state_change = on_state_change
        self.profile_lock = profile_lock or threading.RLock()
        self.loot_reader = loot_reader
        self.on_loot = on_loot
        self.on_cycle = on_cycle
        self.on_harvest_view = on_harvest_view
        self.stop_event = driver.stop_event
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._state = RunState.STOPPED
        self._current_table_id: str | None = None
        self._reason = ""
        self._last_publish = 0.0
        self._pass_count = 0
        self._pass_interrupted = False
        self._unconfirmed_tables: set[str] = set()
        self.last_mismatch_detail = ""
        self._final_reason = "Маршрут остановлен."

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self.stop_event.is_set():
                raise InputStopped("Create a new runner after Stop")
            self.driver.resume()
            self._set_state_locked(RunState.WAITING, "Проверяю готовность выбранных зон.")
            self._thread = threading.Thread(target=self._run, name="harvest-route-runner", daemon=True)
            self._thread.start()

    def pause(self, reason: str = "Пауза по запросу пользователя.") -> None:
        with self._lock:
            if self._state in (RunState.ROUTING, RunState.COLLECTING):
                self._pass_interrupted = True
        self.driver.pause()
        with self._lock:
            self._set_state_locked(RunState.PAUSED, reason)

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise InputStopped("Остановленный маршрут нельзя продолжить; создай новый запуск.")
        if not self.driver.pause_event.is_set():
            return
        # The worker verifies the shared start before resuming a configured loop.
        self.driver.resume()
        with self._lock:
            self._current_table_id = None
            self._set_state_locked(RunState.WAITING, "Продолжаю: маршрут пойдёт с начала (персонаж должен стоять на старте).")

    @property
    def worker_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> bool:
        self.driver.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is not None and thread.is_alive():
            with self._lock:
                self._set_state_locked(
                    RunState.PAUSED,
                    "Ожидаю завершения потока маршрута; профиль пока заблокирован.",
                )
            return False
        with self._lock:
            self._set_state_locked(RunState.STOPPED, "Маршрут остановлен.")
        return True

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return self._build_snapshot_locked()

    def _run(self) -> None:
        logger.info("Route worker entered its scheduler loop")
        while not self.stop_event.is_set():
            if self.driver.pause_event.is_set():
                self._set_state(RunState.PAUSED, self._reason or "Маршрут на паузе.")
                self.stop_event.wait(0.2)
                continue
            try:
                now = time.time()
                self._publish_if_due()
                with self.profile_lock:
                    timers_paused = self.profile.manual_timer_paused
                    active_timers = list(self.profile.tables) if self.profile.runs_plantation else []
                    if self.profile.runs_laboratory:
                        active_timers.append(self.profile.laboratory.timer)
                    waiting_for = [
                        table.name
                        for table in active_timers
                        if (remaining := remaining_seconds(table, self.profile, now)) is None or remaining > 0
                    ]
                if timers_paused:
                    self._set_state(RunState.WAITING, "Таймеры роста приостановлены вручную.")
                    self.stop_event.wait(0.5)
                    continue
                if not active_timers:
                    raise RouteMismatch("В выбранном режиме нет таймеров.")
                if waiting_for and not self._pass_interrupted:
                    reason = f"Ещё не готово: {', '.join(waiting_for)}."
                    if self.profile.waiting_route.events:
                        self._run_waiting_loop(reason)
                    else:
                        self._set_state(RunState.WAITING, reason)
                        self.stop_event.wait(0.5)
                    continue
                self._run_route_pass()
                self._pass_interrupted = False
                self._pass_count += 1
                if self.on_cycle is not None and self.profile.runs_plantation:
                    try:
                        self.on_cycle()  # start -> every harvest -> back at the start point
                    except Exception:
                        logger.exception("Cycle callback failed")
                with self.profile_lock:
                    auto = self.profile.auto_collect
                if not auto:
                    # Auto-collect is off: one cycle, back at the start point, then rest.
                    self._final_reason = "Цикл завершён. Включи «Авто», чтобы цикл повторялся сам."
                    self.driver.stop()
                    break
            except InputStopped:
                break
            except InputInterrupted as exc:
                if self._state in (RunState.ROUTING, RunState.COLLECTING):
                    self._pass_interrupted = True
                self.driver.pause()
                logger.warning("Route paused for a safety check: %s", exc)
                self._set_state(RunState.PAUSED, str(exc))
            except Exception as exc:
                if self._state in (RunState.ROUTING, RunState.COLLECTING):
                    self._pass_interrupted = True
                self.driver.pause()
                logger.exception("Route worker failed; automation paused")
                self._set_state(RunState.PAUSED, f"Остановлено для проверки: {exc}")
        self._set_state(RunState.STOPPED, self._final_reason)
        logger.info("Route worker stopped")

    def _run_waiting_loop(self, reason: str) -> None:
        self._verify_shared_start()
        self._set_state(RunState.WAIT_ROUTE, f"Маршрут ожидания. {reason}")
        self._play_route(self.profile.waiting_route, include_stations=False)
        self._verify_shared_start()

    def _run_route_pass(self) -> None:
        with self.profile_lock:
            route = deepcopy(self.profile.route)
        if self.profile.waiting_route.events or self.profile.runs_laboratory or self.profile.start_marker.template:
            self._verify_shared_start()
        self._set_state(RunState.ROUTING, "Выполняю основной маршрут.")
        self._play_route(route, include_stations=True)
        if self.profile.waiting_route.events or self.profile.runs_laboratory or self.profile.start_marker.template:
            self._verify_shared_start()
        if self.profile.runs_plantation:
            with self.profile_lock:
                missed = [
                    table.name for table in self.profile.tables
                    if table.table_id in self._unconfirmed_tables
                ]
            if missed:
                raise RouteMismatch(f"Сбор не подтверждён: {', '.join(missed)}. Проверь столы перед продолжением.")
        self._set_state(RunState.WAITING, "Маршрут завершён. Проверяю таймеры.")

    def _play_route(self, route: RecordedRoute, *, include_stations: bool) -> None:
        actions: list[tuple[float, int, str, object]] = []
        actions.extend((event.elapsed_offset, 0, "event", event) for event in route.events)
        if include_stations and self.profile.runs_plantation:
            actions.extend((waypoint.event_offset, 1, "waypoint", waypoint) for waypoint in route.waypoints)
        if include_stations and self.profile.runs_laboratory and route.lab_offset is not None:
            actions.append((route.lab_offset, 1, "lab", None))
        actions.sort(key=lambda action: (action[0], action[1]))
        route_started = time.monotonic()
        added_delay = 0.0
        self._unconfirmed_tables.clear()
        try:
            for offset, _priority, action_type, action in actions:
                self._wait_until(route_started + offset + added_delay)
                if action_type == "event":
                    self.driver.play_route_event(action)  # type: ignore[arg-type]
                elif action_type == "waypoint":
                    assert isinstance(action, Waypoint)
                    added_delay += self._visit_waypoint(action)
                else:
                    added_delay += self._visit_laboratory()
            self._wait_until(route_started + route.duration_seconds + added_delay)
        finally:
            self._current_table_id = None
            self.driver.release_all()

    def _marker_matches(self, marker: ScreenMarker, frame) -> bool:
        return bool(marker_available(marker, self.vision) and self.vision.match_template(
            frame, marker.template, marker.region, self.profile.interaction.anchor_threshold,
        ).found)

    def _verify_shared_start(self) -> None:
        if not self._marker_matches(self.profile.start_marker, self._capture_guarded()):
            raise RouteMismatch("Общий старт не распознан. Верни персонажа на старт и наведи камеру как при настройке.")

    def _visit_laboratory(self) -> float:
        lab = self.profile.laboratory
        with self.profile_lock:
            remaining = remaining_seconds(lab.timer, self.profile, time.time())
        if remaining != 0:
            return 0.0
        started = time.monotonic()
        self._set_state(RunState.COLLECTING, "Лаборатория: проверяю готовность.")
        if lab.prepare.strip() and not self._marker_matches(lab.station, self._capture_guarded()):
            raise RouteMismatch("Ориентир у лаборатории не совпал. Проверь точку и камеру.")
        self._play_lab_steps(lab.prepare)
        frame = self._capture_guarded()
        if not self._marker_matches(lab.ready, frame) or self._marker_matches(lab.running, frame):
            raise RouteMismatch("Лаборатория не показывает состояние «Готово». Проверь станцию.")
        self._set_state(RunState.COLLECTING, "Лаборатория: сбор и запуск партии.")
        self._play_lab_steps(lab.actions)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            frame = self._capture_guarded()
            if self._marker_matches(lab.running, frame) and not self._marker_matches(lab.ready, frame):
                with self.profile_lock:
                    start_table_timer(lab.timer, self.profile, lab.timer.interval_seconds, time.time())
                self._publish()
                self._play_lab_steps(lab.finish)
                self._set_state(RunState.ROUTING, "Лаборатория: новая партия запущена.")
                return time.monotonic() - started
            self.driver.wait(0.5)
        raise RouteMismatch("Запуск партии не подтверждён. Проверь лабораторию перед повтором.")

    def _play_lab_steps(self, source: str) -> None:
        for command, args in parse_steps(source):
            if command == "key":
                self.driver.play_key(*args)
            elif command == "wait":
                self.driver.wait(*args)
            elif command == "drag":
                self.driver.drag_to(*args)
            else:
                self.driver.click_at(*args, button="right" if command == "rightclick" else "left")

    def _visit_waypoint(self, waypoint: Waypoint) -> float:
        """Harvest the table if its timer has run out; returns the time spent."""
        with self.profile_lock:
            table = self._table_by_id(waypoint.table_id)
            remaining = remaining_seconds(table, self.profile, time.time())
        if remaining is None:
            raise RouteMismatch(f"Задай начальное время роста для стола {table.name} в окне программы.")
        if remaining > 0:
            return 0.0
        started = time.monotonic()
        self._current_table_id = table.table_id
        self._set_state(RunState.COLLECTING, f"Собираю урожай: {table.name}.")
        interaction = self.profile.interaction
        hold = interaction.hold_seconds if interaction.mode == "hold" else 0.12
        items: list[LootItem] = []
        view = None
        if self.on_harvest_view is not None:
            try:
                view = self._capture_guarded().copy()  # the table as it looks right before the press
            except (InputInterrupted, InputStopped):
                raise
            except Exception:
                logger.debug("could not take the table view", exc_info=True)
        for attempt in range(2):  # one retry: the first press can miss the prompt
            if attempt:
                logger.warning("%s: no packages after the first press, pressing again", table.name)
                self.driver.wait(1.0)
            self.driver.play_key(interaction.key, hold)
            items = self._watch_loot(max(0.5, interaction.harvest_seconds))
            if items:
                break
        if items:
            # A table counts as harvested only when its packages arrived and were counted.
            if self.on_loot is not None:
                try:
                    self.on_loot(table, items)
                except Exception:
                    logger.exception("Loot callback failed")
            with self.profile_lock:
                reset_table_timer(table, self.profile, time.time())
            if view is not None:
                try:
                    self.on_harvest_view(table, view)
                except Exception:
                    logger.exception("Table view callback failed")
        else:
            logger.warning("%s: harvest not confirmed, timer NOT reset", table.name)
            self._unconfirmed_tables.add(table.table_id)
            self._set_state(RunState.ROUTING, f"{table.name}: пакеты не получены — таймер не сброшен.")
        self._current_table_id = None
        self._set_state(RunState.ROUTING, "Выполняю основной маршрут.")
        self._publish()
        return max(0.0, time.monotonic() - started)

    def _watch_loot(self, timeout: float) -> list[LootItem]:
        """Count the «+N Пакет…» notifications that follow a harvest (see loot.watch_loot)."""
        return watch_loot(self.loot_reader, self._capture_guarded, self.driver.wait, timeout, self._save_loot_debug)

    def _save_loot_debug(self, reason: str, crop=None) -> bool:
        """Keep the pick-up area of a doubtful read, so recognition can be tuned on real frames."""
        if crop is None:
            crop = getattr(self.loot_reader, "last_crop", None)
        if crop is None:
            return False
        try:
            import cv2
            from pathlib import Path

            folder = Path(__file__).resolve().parents[2] / "profiles" / "logs" / "loot_debug"
            folder.mkdir(parents=True, exist_ok=True)
            for old in sorted(folder.glob("*.png"))[:-39]:
                old.unlink(missing_ok=True)
            cv2.imwrite(str(folder / f"{time.strftime('%H%M%S')}_{reason}.png"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            return True
        except Exception:
            return False

    def _capture_guarded(self, allow_paused: bool = False):
        if self.stop_event.is_set():
            raise InputStopped("Emergency stop is active")
        if self.driver.pause_event.is_set() and not allow_paused:
            raise InputInterrupted("Маршрут приостановлен пользователем.")
        if not self.screen.is_automation_ready():
            reason = "Игровое окно скрыто или свёрнуто." if self.screen.background_control else "Игровое окно потеряло фокус."
            raise InputInterrupted(reason)
        return self.screen.capture()

    def _wait_until(self, deadline: float) -> None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.driver.wait(min(remaining, 0.1))

    def _table_by_id(self, table_id: str) -> TableProfile:
        for table in self.profile.tables:
            if table.table_id == table_id:
                return table
        raise RouteMismatch(f"Стол {table_id} есть в маршруте, но отсутствует в списке.")

    def _build_snapshot_locked(self) -> RunSnapshot:
        now = time.time()
        statuses: list[TableStatus] = []
        with self.profile_lock:
            for table in sorted(self.profile.tables, key=lambda item: item.route_index):
                remaining = remaining_seconds(table, self.profile, now)
                calibrated = any(wp.table_id == table.table_id for wp in self.profile.route.waypoints)
                if self._current_table_id == table.table_id and self._state == RunState.COLLECTING:
                    state = "collecting"
                elif not calibrated:
                    state = "needs calibration"
                elif remaining is None:
                    state = "set timer"
                elif remaining <= 0:
                    state = "ready"
                else:
                    state = "growing"
                statuses.append(
                    TableStatus(
                        table_id=table.table_id,
                        name=table.name,
                        remaining_seconds=remaining,
                        state=state,
                        calibrated=calibrated,
                        route_index=table.route_index,
                    )
                )
        return RunSnapshot(
            state=self._state,
            current_table_id=self._current_table_id,
            table_statuses=statuses,
            reason=self._reason,
        )

    def _set_state(self, state: RunState, reason: str) -> None:
        with self._lock:
            self._set_state_locked(state, reason)

    def _set_state_locked(self, state: RunState, reason: str) -> None:
        self._state = state
        self._reason = reason
        snapshot = self._build_snapshot_locked()
        try:
            self.on_state_change(snapshot)
        except Exception:
            # UI callbacks must not crash the route worker.
            pass

    def _publish_if_due(self) -> None:
        if time.monotonic() - self._last_publish >= 1.0:
            self._publish()

    def _publish(self) -> None:
        self._last_publish = time.monotonic()
        with self._lock:
            snapshot = self._build_snapshot_locked()
        try:
            self.on_state_change(snapshot)
        except Exception:
            pass
