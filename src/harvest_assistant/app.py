"""Tkinter control panel for table timers and route automation."""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from pynput import keyboard

from . import overlay_bridge
from .loot import LootItem, LootReader, summarize
from .stats import StatsStore
from . import routes as route_store
from .reconnect import Reconnector, describe_screen
from .table_vision import TableMemory, TableVision, TableWatcher
from .approach import ApproachRecorder
from .taught import TaughtStore
from .calibration_status import missing_table_calibration, table_calibration_ready
from .input_driver import InputDriver
from .autocop import AutocopRunner
from .laboratory import laboratory_errors, marker_available, parse_steps
from .models import (
    AUTOMATION_MODES,
    DEFAULT_INTERVAL_SECONDS,
    MAX_TABLES,
    Profile,
    RunSnapshot,
    RunState,
    RecordedRoute,
    ScreenMarker,
    TableProfile,
    TableStatus,
    advance_game_clock,
    remaining_seconds,
    set_table_modifiers,
    set_timer_paused,
    start_table_timer,
)
from .persistence import save_profile
from .route_recorder import RouteRecorder
from .runner import RouteRunner
from .screen import ScreenService
from .vision import VisionService
from .vision_cycle import VisionCycleRunner
from .visual_route import VisualRouteTestRunner
from .ui.dashboard_page import DashboardPage
from .ui.lab_page import LabPage
from .ui.stats_page import StatsPage
from .ui.settings_page import SettingsPage
from .ui.table_dialogs import TableDialogs
from .ui.overlay_page import OverlayPage
from .ui.autocop_page import AutocopPage
from .ui.theme import COLORS, FONT, FONT_SEMI, NavItem, StatusDot, apply_theme


class HarvestAssistantApp:
    def __init__(
        self,
        root: tk.Tk,
        profile: Profile,
        profile_path: Path,
        runner_factory: Callable[..., RouteRunner] = RouteRunner,
    ) -> None:
        self.root = root
        self.profile = profile
        self.profile_path = profile_path
        self.logger = self._configure_logging(profile_path)
        self.template_dir = profile_path.parent / "templates"
        self.runner_factory = runner_factory
        self.profile_lock = threading.RLock()
        self.screen: ScreenService | None = None
        self.vision = VisionService(self.template_dir)
        self.loot_reader = LootReader()
        self.stats = StatsStore(profile_path.parent / "stats.json")
        self.routes_dir = profile_path.parent / "routes"
        self.taught_store = TaughtStore(profile_path.parent / "taught")
        self.profile.loot_totals.clear()  # old lifetime counters: statistics now live in stats.json
        if not self.profile.interval_migrated:
            # The old default was a guess of 40 minutes; a table really takes one hour. Tables that
            # still carry the guess are corrected once, a value the user chose is left alone.
            if all(table.interval_seconds == 40 * 60 for table in self.profile.tables):
                for table in self.profile.tables:
                    table.interval_seconds = DEFAULT_INTERVAL_SECONDS
            self.profile.interval_migrated = True
            save_profile(self.profile, profile_path)
        self.recorder: RouteRecorder | None = None
        self.wizard = None
        self.route_page = None
        self.runner: RouteRunner | VisualRouteTestRunner | None = None
        self.autocop_runner: AutocopRunner | None = None
        self._hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._autocop_bind_listener: keyboard.Listener | None = None
        self._route_recording = False
        self._recording_waiting = False
        self._last_saved = 0.0
        self._last_snapshot = RunSnapshot(state=RunState.STOPPED, reason="Автосбор остановлен.")
        self._selected_table_id: str | None = None
        self._ui_callbacks: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._runner_snapshot_lock = threading.Lock()
        self._pending_runner_snapshot: RunSnapshot | None = None
        self._closing = False

        self.root.title("Hidden pool — rob run edition")
        self.root.geometry("1100x780")
        self.root.minsize(920, 640)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.profile.target_window_title_match:
            self._configure_game_window(show_message=False)
        self._restart_hotkeys()
        self._refresh_rows()
        self.root.after(50, self._drain_ui_callbacks)
        self._start_reconnector()
        self._start_table_watcher()
        self.root.after(1000, self._tick)

    @staticmethod
    def _configure_logging(profile_path: Path) -> logging.Logger:
        logger = logging.getLogger("harvest_assistant")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        log_path = profile_path.parent / "logs" / "harvest_assistant.log"
        for handler in logger.handlers:
            if getattr(handler, "baseFilename", None) == str(log_path.resolve()):
                return logger
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        logger.addHandler(handler)
        logger.info("Application started; log file: %s", log_path)
        return logger

    def _post_to_ui(self, callback: Callable[[], None]) -> None:
        if not self._closing:
            self._ui_callbacks.put(callback)

    def _drain_ui_callbacks(self) -> None:
        if self._closing:
            return
        while True:
            try:
                callback = self._ui_callbacks.get_nowait()
            except Empty:
                break
            try:
                callback()
            except Exception:
                self.logger.exception("UI callback failed")
        with self._runner_snapshot_lock:
            snapshot = self._pending_runner_snapshot
            self._pending_runner_snapshot = None
        if snapshot is not None:
            try:
                self._apply_snapshot(snapshot)
            except Exception:
                self.logger.exception("Could not refresh the UI from a route update")
        self.root.after(50, self._drain_ui_callbacks)

    def _build_ui(self) -> None:
        apply_theme(self.root)
        self.window_status_var = tk.StringVar(value="Окно не привязано")
        self.timer_pause_var = tk.BooleanVar(value=self.profile.manual_timer_paused)
        self.run_state_var = tk.StringVar(value="Остановлено")
        self.auto_state_var = tk.StringVar(value="Авто: да" if self.profile.auto_collect else "Авто: нет")
        self.status_message_var = tk.StringVar(value="Остановлено.")
        self.game_connection_var = tk.StringVar(value="Окно не привязано")
        self.hotkey_help_var = tk.StringVar(value="")
        self._status_text = self.status_message_var.get()

        shell = tk.Frame(self.root, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True)
        rail = tk.Frame(shell, bg=COLORS["rail"], width=214)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        brand = tk.Frame(rail, bg=COLORS["rail"])
        brand.pack(fill="x", padx=24, pady=(34, 34))
        tk.Label(brand, text="Hidden pool", bg=COLORS["rail"], fg=COLORS["text"], font=(FONT_SEMI, 15)).pack(anchor="w")
        tk.Label(brand, text="ROB RUN EDITION", bg=COLORS["rail"], fg=COLORS["faint"], font=(FONT_SEMI, 7)).pack(anchor="w", pady=(2, 0))

        self._nav_buttons: dict[str, NavItem] = {}
        nav = tk.Frame(rail, bg=COLORS["rail"])
        nav.pack(fill="x", padx=12)
        for page_id, text, badge in (
            ("tables", "Плантация", ""),
            ("lab", "Лаборатория", ""),
            ("autocop", "Автокоп", ""),
            ("stats", "Статистика", ""),
            ("overlay", "Оверлей", ""),
            ("settings", "Настройки", ""),
        ):
            item = NavItem(nav, text, lambda value=page_id: self._show_page(value), badge=badge)
            item.pack(fill="x", pady=2)
            self._nav_buttons[page_id] = item

        main = tk.Frame(shell, bg=COLORS["bg"])
        main.pack(side="left", fill="both", expand=True)
        topbar = tk.Frame(main, bg=COLORS["bg"])
        topbar.pack(fill="x", padx=36, pady=(22, 0))
        StatusDot(topbar, self.game_connection_var, self._pill_color).pack(side="right", padx=(18, 0))
        StatusDot(topbar, self.run_state_var, self._pill_color).pack(side="right")
        StatusDot(topbar, self.auto_state_var, self._pill_color, command=self._toggle_auto).pack(side="right", padx=(0, 18))
        tk.Label(topbar, textvariable=self.hotkey_help_var, bg=COLORS["bg"], fg=COLORS["faint"], font=(FONT, 8)).pack(side="left")

        self.page_host = tk.Frame(main, bg=COLORS["bg"])
        self.page_host.pack(fill="both", expand=True)
        self.status_label = tk.Label(main, textvariable=self.status_message_var, bg=COLORS["bg"], fg=COLORS["muted"],
                                     font=(FONT, 9), anchor="w", justify="left", wraplength=760)
        self.status_label.pack(fill="x", padx=40, pady=(0, 16))

        dashboard_callbacks = {
            "start": self._start_automation,
            "set_mode": self._set_automation_mode,
            "pause": self._pause_automation,
            "stop": self._end_session,
            "timer_pause": self._toggle_timer_pause,
            "add": self._add_table,
            "rename": self._rename_table,
            "set_timer": self._set_selected_timer,
            "set_modifiers": self._set_table_modifiers,
            "remove": self._remove_table,
        }
        self.dashboard_page = DashboardPage(self.page_host, dashboard_callbacks, self._on_table_selected, self.profile, self.vision, self.timer_pause_var, self.stats)
        self.stats_page = StatsPage(self.page_host, self.stats)
        self.lab_page = LabPage(
            self.page_host, self.profile, self._save_laboratory,
            lambda stage: self._choose_marker(stage),
        )
        self.autocop_page = AutocopPage(
            self.page_host,
            self._start_autocop,
            self._stop_autocop,
            self._begin_autocop_hotkey_capture,
            self.profile.autocop_hotkey,
            self._select_autocop_class,
            self.profile.autocop_class,
            memory=self._autocop_memory(),
        )
        self.overlay_page = OverlayPage(
            self.page_host,
            self.profile,
            {"start": self._start_overlay, "stop": self._stop_overlay},
        )
        self.settings_page = SettingsPage(
            self.page_host,
            {
                "apply_window": self._apply_game_window,
                "timer_pause": self._toggle_timer_pause,
                "apply_settings": self._apply_settings,
                "set_base_interval": self._set_base_interval,
                "save_route": self._save_current_route,
                "list_routes": lambda: route_store.list_routes(self.routes_dir),
                "load_route": self._load_saved_route,
                "delete_route": self._delete_saved_route,
                "toggle_reconnect": lambda: self._set_reconnect(self.settings_page.reconnect_var.get()),
                "toggle_table_vision": lambda: self._set_table_vision(self.settings_page.table_vision_var.get()),
                "toggle_vision_harvest": lambda: self._set_vision_harvest(self.settings_page.vision_harvest_var.get()),
                "toggle_background_control": lambda: self._set_background_control(self.settings_page.background_control_var.get()),
                "scan_reconnect": self._scan_reconnect_screen,
                "toggle_auto": lambda: self._set_auto(self.settings_page.auto_var.get()),
                "toggle_recording": self._toggle_route_recording_from_ui,
                "record_waiting": lambda: self._toggle_route_recording_from_ui(waiting=True),
                "clear_waiting": self._clear_waiting_route,
                "start_marker": lambda: self._choose_marker("start"),
                "visual_test": self._start_visual_test,
                "approach_record": self._toggle_approach_recording,
                "taught_list": lambda: self.taught_store.load_runs(),
                "taught_delete": self._delete_taught_run,
                "visual_stop": self._stop_automation,
                "mark_waypoint": self._mark_next_waypoint_from_ui,
                "read_loot": self._read_loot_now,
                "before_edit": self._prepare_calibration_edit,
                "profile_changed": self._on_calibration_profile_changed,
            },
            self.profile,
            self.timer_pause_var,
        )
        self.route_page = self.settings_page.route
        self.wizard = self.settings_page.collect
        self.window_title_var = self.settings_page.window_title_var
        self.clock_mode_var = self.settings_page.clock_mode_var
        self.hotkey_vars = self.settings_page.hotkey_vars
        self.pages = {
            "tables": self.dashboard_page,
            "lab": self.lab_page,
            "autocop": self.autocop_page,
            "stats": self.stats_page,
            "overlay": self.overlay_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._show_page("tables")
        self._update_hotkey_help()

    @staticmethod
    def _pill_color(text: str) -> str:
        lowered = text.lower()
        if lowered.startswith("авто"):
            return COLORS["green"] if lowered.endswith("да") else COLORS["faint"]
        if any(word in lowered for word in ("ошибка", "не найден", "открой")):
            return COLORS["red"]
        if any(word in lowered for word in ("пауза", "не привязано")):
            return COLORS["amber"]
        if any(word in lowered for word in ("игра найдена", "маршрут", "собира", "ожидание", "запись", "в пути")):
            return COLORS["green"]
        return COLORS["muted"]

    def _show_page(self, page_id: str) -> None:
        page = getattr(self, "pages", {}).get(page_id)
        if page is None:
            return
        previous_page = getattr(self, "_current_page", None)
        page.tkraise()
        self._current_page = page_id
        for key, item in self._nav_buttons.items():
            item.select(key == page_id)
        self._sync_overlay_edit_mode()
        if page_id == "autocop":
            self._set_status(self.autocop_page.status.get())
        elif previous_page == "autocop":
            self._set_status(self._last_snapshot.reason or "Автосбор остановлен.")
        if page_id == "stats":
            self.stats_page.refresh()

    def _apply_game_window(self) -> None:
        title = self.window_title_var.get().strip()
        if not title:
            messagebox.showwarning("Нужно окно игры", "Введи часть заголовка игрового окна.", parent=self.root)
            return
        if not self._stop_runner_for_profile_edit():
            return
        if self._route_recording and self.recorder is not None:
            self.recorder.cancel()
            self._route_recording = False
        try:
            screen = ScreenService(title, background_control=self.profile.background_control)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Не удалось выбрать окно", str(exc), parent=self.root)
            return
        self.profile.target_window_title_match = title
        self.screen = screen
        self.recorder = RouteRecorder(screen, self.vision, self.profile)
        if self.wizard is not None:
            self.wizard.set_services(self.screen, self.recorder)
        self.window_status_var.set("Найдено" if screen.is_target_open() else "Открой игру и проверь заголовок")
        self.game_connection_var.set("Игра найдена" if screen.is_target_open() else "Открой игру")
        self._save_profile(force=True)
        self._last_snapshot.reason = "Игровое окно настроено. Запиши маршрут клавишей F5."
        self._set_status(self._last_snapshot.reason)
        self._restart_hotkeys()

    def _configure_game_window(self, show_message: bool) -> None:
        title = self.profile.target_window_title_match.strip()
        if not title:
            return
        try:
            self.screen = ScreenService(title, background_control=self.profile.background_control)
            self.recorder = RouteRecorder(self.screen, self.vision, self.profile)
            self.window_status_var.set("Найдено" if self.screen.is_target_open() else "Открой игру")
            self.game_connection_var.set("Игра найдена" if self.screen.is_target_open() else "Открой игру")
        except (OSError, ValueError) as exc:
            self.screen = None
            self.recorder = None
            self.window_status_var.set("Ошибка настройки")
            self.game_connection_var.set("Ошибка окна")
            if show_message:
                messagebox.showerror("Не удалось выбрать окно", str(exc), parent=self.root)
        if self.wizard is not None:
            self.wizard.set_services(self.screen, self.recorder)

    def _bind_current_window_help(self) -> None:
        messagebox.showinfo(
            "Привязка окна",
            "Нажми Ctrl+F10, пока игровое окно активно. Программа сохранит его заголовок.",
            parent=self.root,
        )

    def _set_background_control(self, enabled: bool) -> None:
        if self.autocop_runner is not None and self.autocop_runner.worker_alive:
            self.settings_page.background_control_var.set(self.profile.background_control)
            self._set_status("Останови автокоп перед сменой режима управления.")
            return
        if self.runner is not None and self.runner.worker_alive:
            self.settings_page.background_control_var.set(self.profile.background_control)
            self._set_status("Останови маршрут перед сменой режима управления.")
            return
        if self._route_recording or getattr(getattr(self, "approach_recorder", None), "recording", False):
            self.settings_page.background_control_var.set(self.profile.background_control)
            self._set_status("Заверши запись маршрута перед сменой режима управления.")
            return

        title = self.profile.target_window_title_match.strip()
        screen = self.screen
        if title:
            try:
                screen = ScreenService(title, background_control=enabled)
            except (OSError, ValueError) as exc:
                self.settings_page.background_control_var.set(self.profile.background_control)
                self._set_status(f"Не удалось включить режим: {exc}")
                return

        with self.profile_lock:
            self.profile.background_control = bool(enabled)
        self.screen = screen
        if screen is not None:
            self.recorder = RouteRecorder(screen, self.vision, self.profile)
            if self.wizard is not None:
                self.wizard.set_services(screen, self.recorder)
        self._save_profile(force=True)
        if enabled:
            self._set_status("Фоновое управление включено. Держи игровое окно открытым и не перекрывай его на втором мониторе.")
        else:
            self._set_status("Обычный режим включён: для управления игра должна быть активным окном.")

    def _capture_active_window_title(self) -> None:
        _handle, title = ScreenService.active_window_details()
        if not title or "Harvest Assistant" in title:
            return
        self._post_to_ui(lambda: self._apply_bound_window(title))

    def _apply_bound_window(self, title: str) -> None:
        self.window_title_var.set(title)
        self._apply_game_window()

    def _restart_hotkeys(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
                self._hotkey_listener.join(timeout=0.5)
            except Exception:
                pass
        bindings = {
            self._pynput_hotkey(self.profile.record_route_hotkey): self._hotkey_route,
            self._pynput_hotkey(self.profile.mark_waypoint_hotkey): self._hotkey_mark_waypoint,
            self._pynput_hotkey(self.profile.pause_hotkey): self._hotkey_pause,
            self._pynput_hotkey(self.profile.stop_hotkey): self._hotkey_stop,
            self._pynput_hotkey(self.profile.loot_hotkey): self._hotkey_loot,
            self._pynput_hotkey(self.profile.auto_hotkey): self._hotkey_auto,
            self._pynput_hotkey(self.profile.visual_hotkey): self._hotkey_visual,
            self._pynput_hotkey(self.profile.approach_hotkey): self._hotkey_approach,
            self._pynput_hotkey(self.profile.autocop_hotkey): self._hotkey_autocop,
            "<ctrl>+<f10>": self._capture_active_window_title,
            "<ctrl>+<f3>": lambda: self._post_to_ui(self._mark_laboratory),
        }
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(bindings)
            self._hotkey_listener.start()
        except Exception as exc:
            self._set_status(f"Не удалось включить горячие клавиши: {exc}")
            self._hotkey_listener = None

    @staticmethod
    def _pynput_hotkey(value: str) -> str:
        parts = [part.strip().lower() for part in value.split("+") if part.strip()]
        if not parts:
            return "<f8>"
        return "+".join(part if len(part) == 1 else f"<{part}>" for part in parts)

    @staticmethod
    def _hotkey_parts(value: str) -> set[str]:
        return {part.strip().strip("<>").lower() for part in value.split("+") if part.strip()}

    def _update_hotkey_help(self) -> None:
        self.hotkey_help_var.set(
            f"{self.profile.record_route_hotkey.upper()} — запись · "
            f"{self.profile.mark_waypoint_hotkey.upper()} — отметить · "
            f"{self.profile.pause_hotkey.upper()} — старт/пауза · {self.profile.stop_hotkey.upper()} — стоп · "
            f"{self.profile.auto_hotkey.upper()} — авто · {self.profile.approach_hotkey.upper()} — запись пути · {self.profile.visual_hotkey.upper()} — путь до столов · {self.profile.loot_hotkey.upper()} — пакеты"
        )
        if getattr(self, "settings_page", None) is not None:
            self.settings_page.update_hotkey_help(self.profile)

    def _hotkey_route(self) -> None:
        self._post_to_ui(self._toggle_route_recording)

    def _hotkey_mark_waypoint(self) -> None:
        self._post_to_ui(self._mark_next_waypoint)

    def _hotkey_pause(self) -> None:
        self._post_to_ui(self._toggle_runner_pause)

    def _sync_overlay_edit_mode(self) -> None:
        """Keep the in-game overlay visible while its settings tab is open, for live adjustment."""
        editing = getattr(self, "_current_page", "") == "overlay" and self.root.state() != "iconic"
        overlay_bridge.set_edit_mode(editing)

    def _start_overlay(self) -> None:
        error = overlay_bridge.launch_overlay()
        self._set_status(f"Не удалось запустить оверлей: {error}" if error else "Оверлей запускается… первый запуск может занять минуту.")

    def _stop_overlay(self) -> None:
        self._set_status("Оверлей закрывается." if overlay_bridge.stop_overlay() else "Оверлей не запущен.")

    def _publish_overlay_feed(self) -> None:
        state = self.runner.snapshot().state.value if self.runner is not None else "stopped"
        with self.profile_lock:
            overlay_bridge.write_feed(self.profile, running=state not in ("stopped", "error"), state=state,
                                      counts=self.stats.session_counts())

    def _archive_route(self, reason: str, label: str = "") -> bool:
        """Keep a copy of the current route in profiles/routes. False if there was nothing to keep."""
        try:
            return route_store.archive_route(self.profile.route, self.profile, self.routes_dir, reason, label) is not None
        except OSError:
            self.logger.exception("Could not archive the route")
            return False

    def _save_current_route(self) -> None:
        if not self.profile.route.waypoints and self.profile.route.lab_offset is None:
            self._set_status("Маршрута ещё нет: сначала запиши его.")
            return
        if self._archive_route("сохранён вручную"):
            self.settings_page.render_saved()
            self._set_status("Маршрут сохранён.")

    def _load_saved_route(self, path) -> None:
        if self._route_recording:
            self._set_status("Сначала заверши запись маршрута.")
            return
        try:
            route, meta = route_store.load_route(path)
        except (OSError, ValueError, KeyError):
            messagebox.showerror("Не удалось открыть маршрут", "Файл маршрута повреждён или недоступен.", parent=self.root)
            return
        current = self.profile.route
        if (current.waypoints or current.lab_offset is not None) and not messagebox.askyesno(
            "Заменить маршрут?",
            f"Текущий маршрут ({len(current.waypoints)} стол., {current.duration_seconds:.0f} с) будет заменён маршрутом "
            f"«{meta['label']}».\n\nТекущий сохранится в списке.",
            parent=self.root,
        ):
            return
        existing = {table.table_id for table in self.profile.tables}
        missing = [waypoint.table_id for waypoint in route.waypoints if waypoint.table_id not in existing]
        add_missing = False
        if missing:
            names = ", ".join(meta["tables"].get(table_id, table_id) for table_id in missing)
            answer = messagebox.askyesnocancel(
                "Столов из маршрута нет в списке",
                f"Маршрут записан и для: {names}.\n\n"
                "Да — добавить эти столы.\n"
                "Нет — загрузить маршрут без них (их точки будут убраны).\n"
                "Отмена — ничего не менять.",
                parent=self.root,
            )
            if answer is None:
                return
            add_missing = bool(answer)
            if not add_missing:
                route.waypoints = [w for w in route.waypoints if w.table_id in existing]
        if not self._stop_runner_for_profile_edit():
            return
        self._archive_route("заменён загруженным")
        base = self.profile.tables[0].interval_seconds if self.profile.tables else DEFAULT_INTERVAL_SECONDS
        with self.profile_lock:
            for table_id in (missing if add_missing else []):
                self.profile.tables.append(TableProfile(
                    table_id=table_id, name=meta["tables"].get(table_id, table_id),
                    route_index=len(self.profile.tables), interval_seconds=base,
                ))
            self.profile.route = route
            for index, waypoint in enumerate(route.waypoints):
                self._table_by_id(waypoint.table_id).route_index = index
        self._save_profile(force=True)
        self.settings_page.render_saved()
        self._refresh_rows()
        suffix = f" Добавлено столов: {len(missing)}." if missing and add_missing else ""
        self._set_status(f"Загружен маршрут «{meta['label']}».{suffix}")

    def _delete_saved_route(self, path) -> None:
        if not messagebox.askyesno("Удалить сохранённый маршрут?", "Файл маршрута будет удалён без возможности восстановления.", parent=self.root):
            return
        route_store.delete_route(path)
        self.settings_page.render_saved()

    def _set_base_interval(self, seconds: int) -> None:
        """Growth time without upgrades, for every table. Timers that are already running keep their value."""
        with self.profile_lock:
            for table in self.profile.tables:
                table.interval_seconds = int(seconds)
        self._save_profile(force=True)
        self._refresh_rows()
        self._set_status(f"Время роста без улучшений: {seconds // 60}:{seconds % 60:02d}.")

    def _set_table_modifiers(self, table_id: str, modifiers: list[str]) -> None:
        """Upgrades change the cycle length; a running timer is rescaled. The route keeps running."""
        with self.profile_lock:
            table = self._table_by_id(table_id)
            set_table_modifiers(table, self.profile, modifiers, time.time())
        self._save_profile(force=True)
        self._refresh_rows()
        self._publish_overlay_feed()

    # ---- plantation vision --------------------------------------------------------------

    def _start_table_watcher(self) -> None:
        self.table_memory = TableMemory(self.profile_path.parent / "table_views")
        self.table_watcher = TableWatcher(
            lambda: self.screen,
            lambda: self.profile.table_vision,
            lambda: isinstance(self.runner, VisualRouteTestRunner) and self.runner.worker_alive and self.runner.overlay_busy,
            overlay_bridge.write_vision,
            lambda text: self._post_to_ui(lambda: self.settings_page.table_vision_note.set(text)),
            memory=self.table_memory,
            timers=self._table_timers,
        )
        self.table_watcher.start()

    def _table_timers(self) -> dict[str, tuple[str, int | None]]:
        now = time.time()
        with self.profile_lock:
            return {t.table_id: (t.name, remaining_seconds(t, self.profile, now)) for t in self.profile.tables}

    def _remember_table_view(self, table: TableProfile, frame) -> None:
        """Called by the route right after a confirmed harvest: this is what table `table.name` looks like."""
        try:
            badges = TableVision().scan(frame)
            if self.table_memory.remember(table.table_id, frame, badges):
                self.logger.info("Remembered the look of %s", table.name)
            else:
                self.logger.info("No badge in view while harvesting %s: look not remembered", table.name)
        except Exception:
            self.logger.exception("Could not remember the look of %s", table.name)

    def _set_vision_harvest(self, enabled: bool) -> None:
        with self.profile_lock:
            self.profile.vision_harvest = bool(enabled)
        self._save_profile(force=True)
        self._set_status("Сбор по зрению включён." if enabled else "Включён старый режим: повтор записанного маршрута.")

    def _set_table_vision(self, enabled: bool) -> None:
        with self.profile_lock:
            self.profile.table_vision = bool(enabled)
        self._save_profile(force=True)
        self._set_status("Зрение плантации включено." if enabled else "Зрение плантации выключено.")

    # ---- auto-reconnect -----------------------------------------------------------------

    def _start_reconnector(self) -> None:
        self._resume_after_reconnect = False
        self.reconnector = Reconnector(
            lambda: self.screen,
            lambda: self.profile.auto_reconnect,
            lambda kind, text: self._post_to_ui(lambda: self._on_reconnect_event(kind, text)),
        )
        self.reconnector.start()

    def _on_reconnect_event(self, kind: str, text: str) -> None:
        """Runs on the UI thread. The log watcher reports; the route is stopped here."""
        if kind == "lobby":
            active = self.runner is not None and not self.runner.stop_event.is_set()
            if active:
                # A visual pilot must never turn into the production macro after reconnect.
                # A plain visual test never turns into production; the vision harvest cycle restarts by itself
                # (it looks where it is first), like the old route did.
                self._resume_after_reconnect = (
                    not isinstance(self.runner, VisualRouteTestRunner) or isinstance(self.runner, VisionCycleRunner)
                )
                self.runner.stop()  # never let the route type into the menu
            self.logger.warning("Thrown into the lobby (route %s)", "stopped" if active else "was not running")
            self._set_status(
                "Выкинуло в лобби. Маршрут остановлен." + (" Возвращаюсь…" if self.profile.auto_reconnect else " Автореконнект выключен.")
            )
        elif kind == "status":
            self._set_status(text)
        elif kind == "reconnected":
            self.stats.add_reconnect()
            self.logger.info("Reconnected")
            self._set_status("Вернулся в игру.")
            if self._resume_after_reconnect:
                self._resume_after_reconnect = False
                self.root.after(2500, lambda: self._start_automation(from_hotkey=True))
        elif kind == "failed":
            self._resume_after_reconnect = False
            self.logger.warning("Reconnect failed: %s", text)
            self._set_status(text)

    def _set_reconnect(self, enabled: bool) -> None:
        with self.profile_lock:
            self.profile.auto_reconnect = bool(enabled)
        if self.settings_page.reconnect_var.get() != enabled:
            self.settings_page.reconnect_var.set(enabled)
        self._save_profile(force=True)
        self._set_status("Автореконнект включён." if enabled else "Автореконнект выключен.")

    def _scan_reconnect_screen(self) -> None:
        """Dry run: say which login button is on screen, click nothing."""
        if self.screen is None:
            self._set_status("Сначала привяжи игровое окно.")
            return

        def work() -> None:
            try:
                self.screen.focus_target()
                text = describe_screen(self.screen.capture())
            except Exception as exc:
                text = f"Не удалось посмотреть экран: {exc}"
            self._post_to_ui(lambda: self.settings_page.reconnect_note.set(text))
            self._post_to_ui(lambda: self._set_status(text))

        threading.Thread(target=work, name="reconnect-scan", daemon=True).start()

    def _hotkey_auto(self) -> None:
        self._post_to_ui(self._toggle_auto)

    def _toggle_auto(self) -> None:
        self._set_auto(not self.profile.auto_collect)

    def _set_auto(self, enabled: bool) -> None:
        with self.profile_lock:
            self.profile.auto_collect = bool(enabled)
        self.auto_state_var.set("Авто: да" if enabled else "Авто: нет")
        if self.settings_page.auto_var.get() != enabled:
            self.settings_page.auto_var.set(enabled)
        self._save_profile(force=True)
        self.logger.info("Auto-collect %s", "on" if enabled else "off")
        self._set_status(
            "Автосбор включён: цикл будет повторяться, когда таймеры заканчиваются."
            if enabled else "Автосбор выключен: после одного цикла маршрут остановится."
        )

    def _hotkey_loot(self) -> None:
        self._post_to_ui(self._read_loot_now)

    def _hotkey_stop(self) -> None:
        self._post_to_ui(self._emergency_stop)

    def _toggle_route_recording_from_ui(self, waiting: bool = False) -> None:
        route = self.profile.route
        if self._route_recording and waiting != self._recording_waiting:
            self._set_status("Сначала заверши текущую запись клавишей записи маршрута.")
            return
        if not self._route_recording and not waiting and (route.waypoints or route.lab_offset is not None):
            if not messagebox.askyesno(
                "Записать маршрут заново?",
                f"Уже записан маршрут: {len(route.waypoints)} стол., {route.duration_seconds:.0f} с.\n\n"
                "Новая запись заменит его. Старый сохранится в списке сохранённых маршрутов.",
                parent=self.root,
            ):
                return
        if self.screen is None:
            self._set_status("Сначала привяжи игровое окно.")
            return
        try:
            self.screen.focus_target()
        except Exception as exc:
            self._set_status(f"Не удалось переключиться в игру: {exc}")
            return
        self._toggle_route_recording(waiting)

    def _mark_next_waypoint_from_ui(self) -> None:
        if self.screen is None:
            self._set_status("Сначала привяжи игровое окно.")
            return
        try:
            self.screen.focus_target()
        except Exception as exc:
            self._set_status(f"Не удалось переключиться в игру: {exc}")
            return
        self._mark_next_waypoint()

    def _toggle_route_recording(self, waiting: bool = False) -> None:
        if self.recorder is None:
            self._set_status("Сначала привяжи игровое окно.")
            return
        if not self._route_recording:
            if not self._stop_runner_for_profile_edit():
                return
            try:
                marker = self.profile.start_marker
                self.recorder.start(marker if marker_available(marker, self.vision) else None)
            except Exception as exc:
                self._set_status(str(exc))
                return
            self._route_recording = True
            self._recording_waiting = waiting
            self._set_status(
                "Записываю ожидание. Сделай круг и вернись на общий старт." if waiting else
                f"Записываю основной маршрут. {self.profile.mark_waypoint_hotkey.upper()} — стол, "
                "Ctrl+F3 — лаборатория. Заверши запись на старте."
            )
            return

        self._route_recording = False
        try:
            route = self.recorder.finish()
        except Exception as exc:
            self.recorder.cancel()
            self._set_status(str(exc))
            return
        if self._recording_waiting:
            self._recording_waiting = False
            if not route.events or route.duration_seconds <= 0:
                self._set_status("Ожидание не сохранено: в записи нет движения.")
                return
            if marker_available(self.profile.start_marker, self.vision):
                try:
                    self._verify_recorded_start_and_end(route)
                except (RuntimeError, ValueError, OSError) as exc:
                    self._set_status(f"Ожидание не сохранено: {exc}")
                    return
            with self.profile_lock:
                self.profile.waiting_route = route
            self._save_profile(force=True)
            self._refresh_rows()
            self._set_status(f"Маршрут ожидания записан: {route.duration_seconds:.1f} с.")
            return
        current_table_ids = {table.table_id for table in self.profile.tables}
        stale_ids = {waypoint.table_id for waypoint in route.waypoints} - current_table_ids
        if stale_ids:
            self._set_status(
                "Запись не сохранена: маршрут содержит удалённые столы "
                f"{', '.join(sorted(stale_ids))}. Запиши его заново."
            )
            return
        if not route.waypoints and route.lab_offset is None:
            self._set_status("Маршрут не сохранён: отметь хотя бы стол или лабораторию.")
            return
        if marker_available(self.profile.start_marker, self.vision):
            try:
                self._verify_recorded_start_and_end(route)
            except (RuntimeError, ValueError, OSError) as exc:
                self._set_status(f"Основной маршрут не сохранён: {exc}")
                return
        self._archive_route("заменён новой записью")
        with self.profile_lock:
            self.profile.route = route
            for index, waypoint in enumerate(route.waypoints):
                table = self._table_by_id(waypoint.table_id)
                table.route_index = index
        self._save_profile(force=True)
        mouse_note = "" if self.recorder.mouse_trace_usable else " Камера записана неуверенно: проверь повороты и при необходимости добавь ручные в мастере."
        lab_note = " Лаборатория отмечена." if route.lab_offset is not None else ""
        self._set_status(f"Маршрут записан: {len(route.waypoints)} столов.{lab_note}{mouse_note}")

    def _mark_next_waypoint(self) -> None:
        approach = getattr(self, "approach_recorder", None)
        if approach is not None and approach.recording:
            # while a run to the plantation is being recorded the same key marks "I am at a table"
            if approach.mark_table():
                self._set_status(f"Стол отмечен (отмечено столов: {sum(1 for e in approach._events if e.kind == 'interact' and e.value == 'harvest')}).")
            return
        if not self._route_recording or self.recorder is None:
            self._set_status("Для отметки столов сначала начни запись маршрута клавишей F5.")
            return
        if self._recording_waiting:
            self._set_status("У маршрута ожидания нет точек сбора. Вернись на старт и заверши запись.")
            return
        marked = self.recorder.marked_table_ids
        next_table = next(
            (table for table in sorted(self.profile.tables, key=lambda item: item.route_index) if table.table_id not in marked),
            None,
        )
        if next_table is None:
            self._set_status("Все столы уже отмечены. Вернись к старту и нажми F5.")
            return
        try:
            self.recorder.mark_waypoint(next_table.table_id)
        except Exception as exc:
            self._set_status(str(exc))
            return
        self._set_status(f"Отмечен {next_table.name}. Следующий: нажми F6 у следующего стола.")

    def _mark_laboratory(self) -> None:
        if not self._route_recording or self._recording_waiting or self.recorder is None:
            self._set_status("Лаборатория отмечается в основном маршруте.")
            return
        try:
            self.recorder.mark_laboratory()
        except RuntimeError as exc:
            self._set_status(str(exc))
            return
        self._set_status("Лаборатория отмечена. Продолжи маршрут и вернись на старт.")

    def _set_automation_mode(self, mode: str) -> None:
        if mode == self.profile.automation_mode:
            return
        if self._route_recording or (self.runner is not None and self.runner.worker_alive):
            self.dashboard_page.mode_var.set(self.profile.automation_mode)
            self._set_status("Останови автоматизацию или заверши запись, чтобы сменить режим.")
            return
        if mode not in dict(AUTOMATION_MODES):
            return
        with self.profile_lock:
            self.profile.automation_mode = mode
        self._save_profile(force=True)
        self._refresh_rows()

    def _clear_waiting_route(self) -> None:
        if self._route_recording or (self.runner is not None and self.runner.worker_alive):
            self._set_status("Останови автоматизацию или заверши запись, чтобы убрать ожидание.")
            return
        with self.profile_lock:
            self.profile.waiting_route = RecordedRoute()
        self._save_profile(force=True)
        self._refresh_rows()
        self._set_status("Маршрут ожидания убран.")

    def _save_laboratory(self, minutes: str, remaining: str, prepare: str, actions: str, finish: str) -> str:
        try:
            duration_minutes = float(minutes.replace(",", "."))
            remaining_minutes = float(remaining.replace(",", ".")) if remaining.strip() else None
            if remaining_minutes is not None and remaining_minutes < 0:
                return "Не сохранено. Остаток времени должен быть от нуля."
            interval = round(duration_minutes * 60)
            seconds = round(remaining_minutes * 60) if remaining_minutes is not None else None
        except (ValueError, OverflowError):
            return "Не сохранено. Введи длительность и остаток в минутах, например 20 или 0,5."
        if interval < 1:
            return "Не сохранено. Длительность партии должна быть не меньше секунды."
        try:
            for source in (prepare, actions, finish):
                parse_steps(source)
        except ValueError as exc:
            return f"Не сохранено. {exc}"
        if self._route_recording or (self.runner is not None and self.runner.worker_alive):
            return "Останови автоматизацию или заверши запись перед сохранением."
        with self.profile_lock:
            lab = self.profile.laboratory
            lab.timer.interval_seconds = interval
            if seconds is not None:
                start_table_timer(lab.timer, self.profile, seconds, time.time())
            lab.prepare, lab.actions, lab.finish = prepare, actions, finish
        self._save_profile(force=True)
        self._refresh_rows()
        return "Настройки лаборатории сохранены."

    def _choose_marker(self, stage: str) -> None:
        from .ui.marker_picker import select_marker_region

        if self._route_recording or (self.runner is not None and self.runner.worker_alive):
            self._set_status("Останови автоматизацию или заверши запись перед настройкой ориентира.")
            return
        filename = filedialog.askopenfilename(
            parent=self.root, title="Выбери полный скриншот игры",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp")],
        )
        if not filename:
            return
        try:
            frame, region = select_marker_region(self.root, filename)
            if region is None:
                return
            left, top, width, height = region
            if width < 12 or height < 12 or frame[top:top + height, left:left + width].mean(axis=2).std() < 4:
                raise ValueError("Выдели контрастную деталь размером от 12 × 12 пикселей.")
            name = f"automation_{stage}_{time.time_ns()}.png"
            self.vision.save_template(frame, region, name)
            pad = 12
            x, y = max(0, left - pad), max(0, top - pad)
            marker = ScreenMarker(name, (x, y, min(frame.shape[1], left + width + pad) - x,
                                          min(frame.shape[0], top + height + pad) - y))
            with self.profile_lock:
                if stage == "start":
                    self.profile.start_marker = marker
                else:
                    setattr(self.profile.laboratory, stage, marker)
            self._save_profile(force=True)
            self._refresh_rows()
            self._set_status("Ориентир сохранён.")
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showwarning("Ориентир не сохранён", str(exc), parent=self.root)

    def _verify_recorded_start_and_end(self, route: RecordedRoute) -> None:
        marker = self.profile.start_marker
        for title, filename in (
            ("Начало", route.start_capture_template or route.start_anchor_template),
            ("Конец", route.end_capture_template or route.end_anchor_template),
        ):
            if not filename:
                raise ValueError(f"{title} записи не содержит снимка.")
            frame = self.vision.load_rgb_image(filename)
            if not self.vision.match_template(frame, marker.template, marker.region,
                                              self.profile.interaction.anchor_threshold).found:
                raise ValueError(f"{title} записи не совпадает с общим стартом.")

    def _notify(self, kind: str, title: str, message: str, from_hotkey: bool) -> None:
        """Show a dialog for button clicks; from a hotkey only update the status line.

        A modal dialog opened while the game is fullscreen hides behind the game
        and silently blocks the app, so hotkeys must never open one.
        """
        self.logger.warning("%s: %s", title, message)
        if from_hotkey:
            self._set_status(f"{title}: {message}".replace("\n\n• ", " — ").replace("\n• ", "; "))
            return
        show = {"error": messagebox.showerror, "info": messagebox.showinfo}.get(kind, messagebox.showwarning)
        show(title, message, parent=self.root)

    def _focus_game_for_run(self, from_hotkey: bool, *, force_focus: bool = False) -> bool:
        """Validate the game window, focusing it only in the ordinary control mode."""
        if self.screen is None:
            return False
        if self.profile.background_control and not force_focus:
            if not self.screen.is_automation_ready():
                self._notify(
                    "warning",
                    "Игровое окно недоступно",
                    "Оставь игру открытой, видимой и не свёрнутой. Для фонового режима удобно держать её на втором мониторе.",
                    from_hotkey,
                )
                return False
            return True
        try:
            self.screen.focus_target()
        except Exception as exc:
            self._notify(
                "warning",
                "Не удалось переключиться в игру",
                f"{exc}. Открой игру и повтори (F8 в игре).",
                from_hotkey,
            )
            return False
        return True

    def _start_automation(self, from_hotkey: bool = False) -> None:
        self.logger.info("Automation start requested (%s)", "hotkey" if from_hotkey else "button")
        if self.autocop_runner is not None and self.autocop_runner.worker_alive:
            self._notify("warning", "Автокоп запущен", "Сначала останови автокоп.", from_hotkey)
            return
        if self.screen is None:
            self._notify("warning", "Нет окна игры", "Сначала привяжи игровое окно.", from_hotkey)
            return
        if self._route_recording:
            self._notify("warning", "Идёт запись маршрута", "Заверши запись маршрута клавишей F5.", from_hotkey)
            return
        if (
            self.runner is not None
            and not self.runner.stop_event.is_set()
            and self.runner.snapshot().state == RunState.PAUSED
        ):
            self._resume_automation(from_hotkey=from_hotkey)
            return
        if self.runner is not None and not self.runner.stop_event.is_set():
            self._set_status("Автоматизация уже запущена.")
            return
        if self.runner is not None and self.runner.worker_alive:
            self._notify(
                "warning",
                "Маршрут ещё останавливается",
                "Подожди завершения текущего потока маршрута, прежде чем запускать новый.",
                from_hotkey,
            )
            return
        self.runner = None
        if self.profile.vision_harvest and self.profile.runs_plantation:
            self._start_vision_cycle(from_hotkey)
            return
        self.logger.info("Checking automation preconditions")
        try:
            setup_errors = self._automation_setup_errors()
        except Exception as exc:
            self.logger.exception("Automation preflight failed")
            self._notify("error", "Ошибка проверки запуска", str(exc), from_hotkey)
            return
        if setup_errors:
            self._notify(
                "warning",
                "Сначала закончи настройку",
                "Перед запуском проверь:\n\n• " + "\n• ".join(setup_errors),
                from_hotkey,
            )
            return
        # The selected control mode decides whether the game needs foreground focus.
        if not self._focus_game_for_run(from_hotkey):
            return
        self.logger.info("Automation preconditions passed; creating route runner")
        try:
            stop_event = threading.Event()
            driver = InputDriver(self.screen, stop_event)
            self.runner = self.runner_factory(
                self.profile,
                self.screen,
                self.vision,
                driver,
                self._on_runner_snapshot,
                profile_lock=self.profile_lock,
                loot_reader=self.loot_reader,
                on_loot=self._on_loot,
                on_cycle=self._on_cycle,
                on_harvest_view=self._remember_table_view,
            )
            if self.profile.runs_plantation:
                self.stats.begin_session()
            self.runner.start()
            self.logger.info("Route runner started")
        except Exception as exc:
            self.logger.exception("Could not start route runner")
            self.runner = None
            self._notify("error", "Не удалось запустить маршрут", str(exc), from_hotkey)

    def _start_autocop(self, selected_class: str, from_hotkey: bool = False) -> None:
        if self.screen is None:
            self._set_autocop_status("Сначала привяжи игровое окно в настройках.")
            return
        if self.runner is not None and self.runner.worker_alive:
            self._set_autocop_status("Сначала останови текущий маршрут или автопилот.")
            return
        if self.autocop_runner is not None and self.autocop_runner.worker_alive:
            self._set_autocop_status("Автокоп уже работает.")
            return
        if not self._focus_game_for_run(from_hotkey):
            self._set_autocop_status("Не удалось открыть игровое окно.")
            return
        try:
            if self._route_recording:
                if self.recorder is not None:
                    self.recorder.cancel()
                self._route_recording = False
                self._recording_waiting = False
                self._set_autocop_status("Незавершённая запись F5 отменена. Запускаю автокоп.")
            runner_ref: list[AutocopRunner | None] = [None]

            def on_status(text: str) -> None:
                self._post_to_ui(lambda: self._set_autocop_status(text))

            def on_finished(text: str) -> None:
                runner = runner_ref[0]
                if runner is not None:
                    self._post_to_ui(lambda r=runner, message=text: self._finish_autocop(r, message))

            from .overlay_bridge import write_autocop

            def on_tokens(found: dict) -> None:
                self._post_to_ui(lambda value=dict(found): self._remember_autocop_tokens(value))

            runner = AutocopRunner(self.screen, selected_class, on_status, on_finished,
                                   on_feed=write_autocop, hotkey=str(self.profile.autocop_hotkey or "").upper(),
                                   on_tokens=on_tokens)
            runner_ref[0] = runner
            self.autocop_runner = runner
            self.autocop_page.set_running(True)
            self._set_autocop_status("Автокоп запущен. Ищу активную кнопку службы.")
            runner.start()
        except Exception as exc:
            self.logger.exception("Could not start the police-service helper")
            self.autocop_page.set_running(False)
            self._set_autocop_status(f"Не удалось запустить автокоп: {exc}")

    def _set_autocop_status(self, text: str) -> None:
        self.autocop_page.set_status(text)
        if getattr(self, "_current_page", None) == "autocop":
            self._set_status(text)

    def _select_autocop_class(self, selected_class: str) -> None:
        self.profile.autocop_class = selected_class
        self._save_profile(force=True)

    def _stop_autocop(self) -> None:
        runner = self.autocop_runner
        if runner is None:
            self.autocop_page.set_running(False)
            self._set_autocop_status("Автокоп остановлен.")
            return
        self._set_autocop_status("Останавливаю автокоп…")
        if runner.stop(timeout=1.5):
            self._finish_autocop(runner, "Автокоп остановлен.")
        else:
            self._set_autocop_status("Остановка запрошена; жду завершения распознавания.")

    def _autocop_memory_path(self) -> Path:
        return self.profile_path.parent / "autocop.json"

    def _autocop_memory(self) -> dict:
        from .autocop import load_memory

        return load_memory(self._autocop_memory_path())

    def _remember_autocop_tokens(self, found: dict) -> None:
        from .autocop import save_memory

        memory = self._autocop_memory()
        memory.setdefault("tokens", {}).update(found)
        try:
            save_memory(self._autocop_memory_path(), memory)
        except OSError:
            self.logger.debug("autocop memory not saved", exc_info=True)
        self.autocop_page.set_tokens(found)

    def _finish_autocop(self, runner: AutocopRunner, message: str) -> None:
        if runner.state == "done" and runner.ended_at:
            from .autocop import save_memory

            result = {"class": runner.selected_class, "tries": runner.tries,
                      "seconds": round(runner.ended_at - runner.started_at), "at": runner.ended_at}
            memory = self._autocop_memory()
            memory["last"] = result
            try:
                save_memory(self._autocop_memory_path(), memory)
            except OSError:
                self.logger.debug("autocop memory not saved", exc_info=True)
            self.autocop_page.set_last_result(result)
        if runner.state != "done":
            # stopped by hand or by an error: the panel goes away (on duty, it stays to show the result)
            from .overlay_bridge import clear_autocop

            try:
                clear_autocop()
            except OSError:
                pass
        if self.autocop_runner is runner:
            self.autocop_runner = None
            self.autocop_page.set_running(False)
            self._set_autocop_status(message)

    def _vision_setup_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.screen or not self.screen.is_target_open():
            errors.append("игровое окно не найдено")
        if not self.profile.tables:
            errors.append("добавь хотя бы один стол плантации")
        if not self.taught_store.load_runs():
            errors.append("запиши хотя бы один заход от спавна до плантации (F2) в «Настройки → Путь до столов»")
        return errors

    def _start_vision_cycle(self, from_hotkey: bool) -> None:
        """The whole job by sight: reconnect -> way -> tables -> harvest -> wait -> again."""
        if len(self.profile.tables) > self.MAX_TABLES:
            with self.profile_lock:   # two extra tables were made when the room looked like having eight
                del self.profile.tables[self.MAX_TABLES:]
            self._save_profile(force=True)
            self._refresh_rows()
        if len(self.profile.tables) < self.MAX_TABLES:
            # by sight the room has its places and the bot finds the tables itself: no hand setup is needed
            with self.profile_lock:
                for number in range(len(self.profile.tables) + 1, self.MAX_TABLES + 1):
                    self.profile.tables.append(TableProfile(
                        table_id=self._next_table_id(), name=f"Стол {number}", route_index=number - 1,
                        interval_seconds=DEFAULT_INTERVAL_SECONDS))
            self._save_profile(force=True)
            self._refresh_rows()
        errors = self._vision_setup_errors()
        if errors:
            self._notify("warning", "Сначала закончи настройку", "Перед запуском проверь:\n\n• " + "\n• ".join(errors), from_hotkey)
            return
        if not self._focus_game_for_run(from_hotkey):
            return
        try:
            driver = InputDriver(self.screen, threading.Event())
            self.runner = VisionCycleRunner(
                self.screen,
                driver,
                Path(__file__).resolve().parent / "visual_assets",
                self._on_runner_snapshot,
                failure_dir=self.profile_path.parent / "logs",
                taught_runs=self.taught_store.load_runs(),
                profile=self.profile,
                profile_lock=self.profile_lock,
                loot_reader=self.loot_reader,
                on_loot=self._on_loot,
                on_cycle=self._on_cycle,
                on_harvest_view=self._remember_table_view,
                memory=self.table_memory,
                on_new_table=self._auto_add_table,
            )
            self.runner.vision_sink = overlay_bridge.write_vision
            self.stats.begin_session()
            self.runner.start()
            self.logger.info("Vision harvest cycle started")
        except Exception as exc:
            self.logger.exception("Could not start the vision harvest cycle")
            self.runner = None
            self._notify("error", "Не удалось запустить сбор по зрению", str(exc), from_hotkey)

    def _hotkey_approach(self) -> None:
        self._post_to_ui(self._toggle_approach_recording)

    def _hotkey_autocop(self) -> None:
        self._post_to_ui(self._toggle_autocop)

    def _toggle_autocop(self) -> None:
        if self.autocop_runner is not None and self.autocop_runner.worker_alive:
            self._stop_autocop()
            return
        self._start_autocop(self.autocop_page.selected.get(), from_hotkey=True)

    def _begin_autocop_hotkey_capture(self) -> None:
        if self.autocop_runner is not None and self.autocop_runner.worker_alive:
            self.autocop_page.set_status("Сначала останови автокоп, затем меняй бинд.")
            return
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
                self._hotkey_listener.join(timeout=0.5)
            except Exception:
                pass
            self._hotkey_listener = None
        self.autocop_page.set_hotkey_capture(True)
        try:
            self._autocop_bind_listener = keyboard.Listener(on_press=self._capture_autocop_hotkey)
            self._autocop_bind_listener.start()
        except Exception as exc:
            self._autocop_bind_listener = None
            self._cancel_autocop_hotkey_capture()
            self.autocop_page.set_status(f"Не удалось включить захват клавиши: {exc}")

    def _capture_autocop_hotkey(self, key):
        name = getattr(key, "char", None) or getattr(key, "name", "")
        name = str(name).strip().lower()
        if name in {"ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r", "alt_l", "alt_r", "cmd", "cmd_l", "cmd_r"}:
            return None
        if name in {"esc", "escape"}:
            self._post_to_ui(self._cancel_autocop_hotkey_capture)
            return False
        if not name:
            return None
        valid_keys = {"space", "tab", "enter", "backspace", "delete", "insert", "home", "end", "page_up", "page_down", "up", "down", "left", "right"}
        valid_keys.update(f"f{index}" for index in range(1, 25))
        if not (len(name) == 1 and name.isascii() and name.isalnum()) and name not in valid_keys:
            self._post_to_ui(lambda: self._reject_autocop_hotkey("Эту клавишу нельзя назначить для автокопа."))
            return False
        self._post_to_ui(lambda value=name: self._apply_autocop_hotkey(value))
        return False

    def _reject_autocop_hotkey(self, message: str) -> None:
        self._cancel_autocop_hotkey_capture()
        self.autocop_page.set_status(message)

    def _apply_autocop_hotkey(self, value: str) -> None:
        candidate = self._pynput_hotkey(value)
        other_hotkeys = (
            self.profile.record_route_hotkey,
            self.profile.mark_waypoint_hotkey,
            self.profile.pause_hotkey,
            self.profile.stop_hotkey,
            self.profile.loot_hotkey,
            self.profile.auto_hotkey,
            self.profile.visual_hotkey,
            self.profile.approach_hotkey,
        )
        if candidate in {self._pynput_hotkey(item) for item in other_hotkeys}:
            self.autocop_page.set_status("Эта клавиша уже назначена другой команде.")
            self._cancel_autocop_hotkey_capture()
            return
        self.profile.autocop_hotkey = value
        self._save_profile(force=True)
        self.autocop_page.set_autocop_hotkey(value)
        self._cancel_autocop_hotkey_capture()
        self.autocop_page.set_status(f"Бинд автокопа сохранён: {value.upper()}.")

    def _cancel_autocop_hotkey_capture(self, *, restart_hotkeys: bool = True) -> None:
        listener = self._autocop_bind_listener
        self._autocop_bind_listener = None
        if listener is not None:
            try:
                listener.stop()
                if listener is not threading.current_thread():
                    listener.join(timeout=0.3)
            except Exception:
                pass
        self.autocop_page.set_hotkey_capture(False)
        if restart_hotkeys and not self._closing:
            self._restart_hotkeys()

    def _toggle_approach_recording(self) -> None:
        """F2: record the way from the spawn to the grow room; press again to save it."""
        recorder = getattr(self, "approach_recorder", None)
        if recorder is not None and recorder.recording:
            try:
                route = recorder.finish()
            except Exception as exc:
                self._set_status(f"Запись пути не сохранена: {exc}")
                return
            frames = recorder.frames
            self.approach_recorder = None
            self.settings_page.approach_button.configure(text="Записать заход (F2)")
            self._set_status("Сохраняю заход: считаю опорные точки кадров…")

            def save() -> None:
                run = self.taught_store.add_run(route.events, frames, route.duration_seconds)
                doors = sum(1 for event in route.events if event.kind == "interact")
                text = f"Заход сохранён: {route.duration_seconds:.0f} с, опорных кадров {len(run.keyframes)} из {len(frames)}, E нажато {doors}."
                self._post_to_ui(lambda: (self._set_status(text), self.settings_page.render_taught()))

            threading.Thread(target=save, name="save-taught-run", daemon=True).start()
            return
        if self.screen is None:
            self._set_status("Сначала привяжи игровое окно.")
            return
        if self.runner is not None and self.runner.worker_alive:
            self._set_status("Сначала останови текущий маршрут.")
            return
        if not self._focus_game_for_run(False, force_focus=True):
            return
        from .visual_route import PromptReader
        try:
            self.approach_recorder = ApproachRecorder(self.screen, PromptReader())
            self.approach_recorder.start()
        except Exception as exc:
            self.approach_recorder = None
            self._set_status(f"Не удалось начать запись пути: {exc}")
            return
        self.settings_page.approach_button.configure(text="Сохранить заход (F2)")
        self._set_status("Идёт запись захода: у дверей жми E, у каждого стола жми F6 (E не нужно), в конце нажми F2.")

    def _delete_taught_run(self, run_id: str) -> None:
        if not messagebox.askyesno("Удалить заход?", "Заход обучения будет удалён. Остальные заходы останутся.", parent=self.root):
            return
        self.taught_store.delete_run(run_id)
        self.settings_page.render_taught()

    def _hotkey_visual(self) -> None:
        self._post_to_ui(self._toggle_visual_test)

    def _toggle_visual_test(self) -> None:
        """F3: start the visual route, or stop it if it is already running."""
        if isinstance(self.runner, VisualRouteTestRunner) and self.runner.worker_alive:
            self.runner.stop()
            self._set_status("Зрительный маршрут остановлен.")
            return
        self._start_visual_test(from_hotkey=True)

    def _start_visual_test(self, from_hotkey: bool = False) -> None:
        """Run a separate, bounded vision pilot without touching timers or production routes."""
        if self.screen is None:
            self._notify("warning", "Нет окна игры", "Сначала привяжи игровое окно.", from_hotkey)
            return
        if self._route_recording:
            self._notify("warning", "Идёт запись", "Заверши запись перед зрительным тестом.", from_hotkey)
            return
        if self.runner is not None and self.runner.worker_alive:
            self._set_status("Сначала останови текущий маршрут.")
            return
        if not self._focus_game_for_run(from_hotkey):
            return
        try:
            stop_event = threading.Event()
            driver = InputDriver(self.screen, stop_event)
            assets = Path(__file__).resolve().parent / "visual_assets"
            self.runner = VisualRouteTestRunner(
                self.screen,
                driver,
                assets,
                self._on_runner_snapshot,
                failure_dir=self.profile_path.parent / "logs",
                taught_runs=self.taught_store.load_runs(),
            )
            self.runner.vision_sink = overlay_bridge.write_vision
            self.runner.start()
            self.logger.info("Visual route pilot started")
        except Exception as exc:
            self.logger.exception("Could not start visual route pilot")
            self.runner = None
            self._notify("error", "Не удалось запустить зрительный тест", str(exc), from_hotkey)

    def _pause_automation(self) -> None:
        if self.runner is None:
            self._set_status("Автоматизация ещё не запущена.")
            return
        self.runner.pause("Пауза по кнопке пользователя.")

    def _resume_automation(self, from_hotkey: bool = False) -> None:
        if self.runner is None:
            return
        # Resume re-checks the start landmark on screen, which needs the game in front.
        if not self._focus_game_for_run(from_hotkey):
            return
        try:
            self.runner.resume()
        except Exception as exc:
            self._notify("warning", "Маршрут не возобновлён", str(exc), from_hotkey)

    def _toggle_runner_pause(self) -> None:
        """F8: start when idle/stopped, pause when running, resume when paused."""
        runner = self.runner
        self.logger.info("Pause hotkey pressed (runner=%s)", "none" if runner is None else runner.snapshot().state.value)
        if runner is None or runner.stop_event.is_set():
            # A stopped runner can never be paused or resumed; F8 must start a new run.
            self._start_automation(from_hotkey=True)
        elif runner.snapshot().state == RunState.PAUSED:
            self._resume_automation(from_hotkey=True)
        else:
            self._pause_automation()

    def _stop_automation(self) -> None:
        if self.runner is not None:
            if not self.runner.stop():
                self._set_status("Остановка запрошена. Поток ещё завершает текущую операцию.")
                return
        self._set_status("Автоматизация остановлена.")

    def _on_loot(self, table: TableProfile, items: list[LootItem]) -> None:
        """Called from the route thread after every harvest."""
        for item in items:
            group = overlay_bridge.COUNTER_GROUPS.get(item.color)
            if group:
                self.stats.add_loot(group, item.count, table.name)
            else:
                self.logger.warning("Package colour not recognised, not counted: %s x%d", item.color, item.count)
        self.logger.info("Harvest %s: %s", table.name, summarize(items))
        self._post_to_ui(lambda: self._set_status(f"{table.name}: {summarize(items)}."))
        self._post_to_ui(self._publish_overlay_feed)

    def _on_cycle(self) -> None:
        """A full cycle is done: route start -> every due harvest -> back at the start point."""
        self.stats.add_cycle()
        self.logger.info("Cycle finished (session total %d)", self.stats.session_cycles())
        self._post_to_ui(self._publish_overlay_feed)

    def _end_session(self) -> None:
        """Stop button: stop the route, archive the session into statistics and reset its counters."""
        self._stop_automation()
        self.stats.end_session()
        self._publish_overlay_feed()
        self._set_status("Сессия завершена и сохранена в статистике.")

    def _read_loot_now(self) -> None:
        """Hotkey / button: read the pick-up list on screen once, for checking the recognition."""
        if self.screen is None:
            self._set_status("Сначала привяжи игровое окно.")
            return

        def work() -> None:
            try:
                frame = self.screen.capture()
            except Exception as exc:
                self._post_to_ui(lambda: self.wizard.show_loot([], None, f"игра не в фокусе ({exc})"))
                return
            items = self.loot_reader.read(frame)
            crop, error = self.loot_reader.last_crop, self.loot_reader.error
            self.logger.info("Loot test read: %s%s", summarize(items), f" (error: {error})" if error else "")
            self._post_to_ui(lambda: self.wizard.show_loot(items, crop, error))
            self._post_to_ui(lambda: self._set_status(f"Пакеты на экране: {summarize(items)}."))

        threading.Thread(target=work, name="loot-read", daemon=True).start()

    def _emergency_stop(self) -> None:
        recorder = getattr(self, "approach_recorder", None)
        if recorder is not None and recorder.recording:
            recorder.cancel()
            self.approach_recorder = None
        self.logger.warning("Emergency stop requested")
        if self._route_recording and self.recorder is not None:
            self.recorder.cancel()
            self._route_recording = False
        if self.runner is not None:
            self.runner.stop()
        self._post_to_ui(lambda: self._set_status("Аварийный стоп: ввод отключён."))

    MAX_TABLES = MAX_TABLES

    def _auto_add_table(self) -> TableProfile | None:
        """The pilot found a ripe table the profile does not know (it was planted after the profile was made): add it,
        up to the six places the room has. Its timer starts with the harvest, as for every table."""
        with self.profile_lock:
            if len(self.profile.tables) >= self.MAX_TABLES:
                return None
            number = len(self.profile.tables) + 1
            table = TableProfile(
                table_id=self._next_table_id(), name=f"Стол {number}",
                route_index=max((item.route_index for item in self.profile.tables), default=-1) + 1,
                interval_seconds=self.profile.tables[0].interval_seconds if self.profile.tables else DEFAULT_INTERVAL_SECONDS,
            )
            self.profile.tables.append(table)
        self._save_profile(force=True)
        self._post_to_ui(self._refresh_rows)
        self.logger.info("A new planted table was found and added: %s", table.name)
        return table

    def _add_table(self) -> None:
        if len(self.profile.tables) >= self.MAX_TABLES:
            self._set_status(f"Максимум {self.MAX_TABLES} столов.")
            return
        number = len(self.profile.tables) + 1
        values = TableDialogs.add(self.root, f"Стол {number}", (self.profile.tables[0].interval_seconds if self.profile.tables else DEFAULT_INTERVAL_SECONDS) // 60)
        if values is None:
            return
        name, remaining_minutes = values
        if not name:
            self._set_status("Укажи название стола.")
            return
        if not self._stop_runner_for_profile_edit():
            return
        table_id = self._next_table_id()
        table = TableProfile(
            table_id=table_id,
            name=name.strip(),
            route_index=max((item.route_index for item in self.profile.tables), default=-1) + 1,
            interval_seconds=self.profile.tables[0].interval_seconds if self.profile.tables else DEFAULT_INTERVAL_SECONDS,
        )
        with self.profile_lock:
            self.profile.tables.append(table)
            start_table_timer(table, self.profile, remaining_minutes * 60, time.time())
        self._selected_table_id = table.table_id
        self._save_profile(force=True)
        self._refresh_rows()

    def _rename_table(self) -> None:
        table = self._selected_table()
        if table is None:
            return
        name = TableDialogs.rename(self.root, table.name)
        if name:
            if not self._stop_runner_for_profile_edit():
                return
            with self.profile_lock:
                table.name = name.strip()
            self._save_profile(force=True)
            self._refresh_rows()

    def _remove_table(self) -> None:
        table = self._selected_table()
        if table is None:
            return
        if not TableDialogs.confirm_remove(self.root, table.name):
            return
        if not self._stop_runner_for_profile_edit():
            return
        self._archive_route("до удаления стола")
        with self.profile_lock:
            self.profile.tables = [item for item in self.profile.tables if item.table_id != table.table_id]
            self.profile.waypoint_calibrations.pop(table.table_id, None)
            # Only this table's point leaves the route; the recorded walk and everything else stay.
            self.profile.route.waypoints = [w for w in self.profile.route.waypoints if w.table_id != table.table_id]
            for index, waypoint in enumerate(self.profile.route.waypoints):
                self._table_by_id(waypoint.table_id).route_index = index
        self._selected_table_id = None
        self._save_profile(force=True)
        self._refresh_rows()
        self._set_status(f"«{table.name}» удалён. Маршрут сохранён, из него убрана только точка этого стола.")

    def _set_selected_timer(self) -> None:
        table = self._selected_table()
        if table is None:
            return
        current = remaining_seconds(table, self.profile, time.time())
        initial_minutes = table.interval_seconds // 60 if current is None else round(current / 60)
        minutes = TableDialogs.set_initial_time(self.root, table.name, initial_minutes)
        if minutes is None:
            return
        if not self._stop_runner_for_profile_edit():
            return
        with self.profile_lock:
            start_table_timer(table, self.profile, minutes * 60, time.time())
        self._save_profile(force=True)
        self._refresh_rows()

    def _calibrate_selected(self) -> None:
        self._show_page("settings")

    def _open_wizard(self) -> bool:
        self._show_page("settings")
        return True

    def _prepare_calibration_edit(self) -> bool:
        if self._route_recording:
            self._set_status("Заверши запись маршрута клавишей F5 перед калибровкой.")
            return False
        if self.screen is None or not self.screen.is_target_open():
            self._set_status("Открой привязанную игру перед калибровкой.")
            return False
        return self._stop_runner_for_profile_edit()

    def _apply_settings(self) -> None:
        settings = self.settings_page.read_values()
        new_mode = settings["clock_mode"]
        entered_hotkeys = {attribute: settings[attribute] for attribute in self.hotkey_vars}
        if any(not value for value in entered_hotkeys.values()):
            messagebox.showwarning("Нужны клавиши", "Заполни все горячие клавиши.", parent=self.root)
            return
        normalized = [self._pynput_hotkey(value) for value in entered_hotkeys.values()]
        if len(set(normalized)) != len(normalized):
            messagebox.showwarning("Клавиши совпадают", "У каждой команды должна быть своя горячая клавиша.", parent=self.root)
            return
        if "<ctrl>+<f10>" in normalized or any("f3" in self._hotkey_parts(value) for value in entered_hotkeys.values()):
            messagebox.showwarning("Клавиша зарезервирована", "Ctrl+F10 — привязка игры, Ctrl+F3 — отметка лаборатории.", parent=self.root)
            return
        movement_keys = {"w", "a", "s", "d", "e", "shift"}
        if any(movement_keys.intersection(self._hotkey_parts(value)) for value in entered_hotkeys.values()):
            messagebox.showwarning(
                "Клавиша занята маршрутом",
                "Не назначай WASD, E или Shift на глобальные команды: они нужны игровому маршруту.",
                parent=self.root,
            )
            return
        now = time.time()
        with self.profile_lock:
            if new_mode != self.profile.clock_mode:
                remaining = {
                    table.table_id: remaining_seconds(table, self.profile, now)
                    for table in self.profile.production_timers()
                }
                self.profile.clock_mode = new_mode  # type: ignore[assignment]
                for table in self.profile.production_timers():
                    seconds = remaining[table.table_id]
                    if seconds is not None:
                        start_table_timer(table, self.profile, seconds, now)
            for attribute, value in entered_hotkeys.items():
                setattr(self.profile, attribute, value)
        self._save_profile(force=True)
        self._restart_hotkeys()
        self._update_hotkey_help()
        self._refresh_rows()

    def _automation_setup_errors(self) -> list[str]:
        errors: list[str] = []
        profile = self.profile
        route = profile.route
        now = time.time()
        table_ids = {table.table_id for table in profile.tables}
        waypoint_ids = [waypoint.table_id for waypoint in route.waypoints]
        if not self.screen or not self.screen.is_target_open():
            errors.append("игровое окно не найдено")
        if route.duration_seconds <= 0:
            errors.append("запиши основной маршрут")
        if profile.runs_plantation:
            if not profile.tables:
                errors.append("добавь хотя бы один стол плантации")
            if duplicates := sorted({table_id for table_id in waypoint_ids if waypoint_ids.count(table_id) > 1}):
                errors.append(f"в маршруте повторяются столы: {', '.join(duplicates)}")
            if missing := sorted(table_ids - set(waypoint_ids)):
                errors.append(f"нет точек маршрута для столов: {', '.join(missing)}. Отметь их клавишей записи точки (обычно F6)")
            if stale := sorted(set(waypoint_ids) - table_ids):
                errors.append(f"в маршруте есть удалённые столы: {', '.join(stale)}")
            for table in profile.tables:
                if remaining_seconds(table, profile, now) is None:
                    errors.append(f"задай начальное время для «{table.name}»")
        if profile.runs_laboratory:
            if route.lab_offset is None or not 0 <= route.lab_offset <= route.duration_seconds:
                errors.append("отметь лабораторию в основном маршруте через Ctrl+F3")
            if remaining_seconds(profile.laboratory.timer, profile, now) is None:
                errors.append("задай остаток времени текущей партии на вкладке «Лаборатория»")
            errors.extend(laboratory_errors(profile.laboratory, self.vision))
        if profile.waiting_route.events or profile.runs_laboratory or profile.start_marker.template:
            if not marker_available(profile.start_marker, self.vision):
                errors.append("выбери общий старт в «Настройки → Маршрут»")
            else:
                routes = [("Основной маршрут", route)]
                if profile.waiting_route.events:
                    routes.append(("Маршрут ожидания", profile.waiting_route))
                for title, recorded in routes:
                    if recorded.duration_seconds <= 0:
                        continue
                    try:
                        self._verify_recorded_start_and_end(recorded)
                    except (RuntimeError, ValueError, OSError) as exc:
                        errors.append(f"{title}: {exc}")
        return errors

    def _toggle_timer_pause(self) -> None:
        with self.profile_lock:
            set_timer_paused(self.profile, self.timer_pause_var.get(), time.time())
        self._save_profile(force=True)
        self._refresh_rows()

    def _stop_runner_for_profile_edit(self) -> bool:
        if self.autocop_runner is not None:
            if not self.autocop_runner.stop():
                messagebox.showwarning(
                    "Автокоп ещё останавливается",
                    "Профиль не изменён. Подожди завершения распознавания и повтори действие.",
                    parent=self.root,
                )
                return False
            self.autocop_runner = None
            self.autocop_page.set_running(False)
            self.autocop_page.set_status("Автокоп остановлен из-за изменения настроек.")
        if self.runner is not None:
            if not self.runner.stop():
                messagebox.showwarning(
                    "Маршрут ещё останавливается",
                    "Профиль не изменён. Подожди завершения текущей операции и повтори действие.",
                    parent=self.root,
                )
                return False
            self.runner = None
        if self._route_recording:
            if self.recorder is not None:
                self.recorder.cancel()
            self._route_recording = False
            self._set_status("Запись маршрута отменена из-за изменения профиля. Запиши маршрут заново.")
        return True

    def _on_table_selected(self, table_id: str | None) -> None:
        self._selected_table_id = table_id
        self._refresh_rows()

    def _selected_table(self) -> TableProfile | None:
        if not self._selected_table_id:
            messagebox.showinfo("Выбери стол", "Сначала выбери стол в списке.", parent=self.root)
            return None
        return next((table for table in self.profile.tables if table.table_id == self._selected_table_id), None)

    def _table_by_id(self, table_id: str) -> TableProfile:
        for table in self.profile.tables:
            if table.table_id == table_id:
                return table
        raise KeyError(table_id)

    def _next_table_id(self) -> str:
        existing = {table.table_id for table in self.profile.tables}
        number = 1
        while f"table_{number}" in existing:
            number += 1
        return f"table_{number}"

    def _tick(self) -> None:
        now = time.time()
        game_running = bool(self.screen and self.screen.is_target_open())
        with self.profile_lock:
            advance_game_clock(self.profile, game_running, now)
        self._save_profile(force=False)
        self._refresh_rows()
        self._publish_overlay_feed()
        self._sync_overlay_edit_mode()
        if getattr(self, "_current_page", "") == "stats":
            self.stats_page.refresh()
        self.overlay_page.refresh(overlay_bridge.running_overlay_pid() is not None, self.profile)
        self.root.after(1000, self._tick)

    def _refresh_rows(self) -> None:
        if self.runner is not None:
            snapshot = self.runner.snapshot()
        else:
            snapshot = self._idle_snapshot()
        previous_reason = getattr(self, "_last_runner_reason", self._last_snapshot.reason)
        self._last_snapshot = snapshot
        self._last_runner_reason = snapshot.reason
        if snapshot.reason and snapshot.reason != previous_reason and snapshot.state != RunState.STOPPED:
            self._set_status(snapshot.reason)
        self.run_state_var.set("Запись маршрута" if self._route_recording else self._state_label(snapshot.state.value))
        game_open = bool(self.screen and self.screen.is_target_open())
        if self.screen is None:
            window_status = "Окно не привязано"
        elif game_open:
            window_status = "Игра найдена"
        else:
            window_status = "Открой игру и проверь заголовок"
        self.window_status_var.set(window_status)
        self.game_connection_var.set(window_status)
        self.dashboard_page.refresh(
            snapshot,
            self.profile.tables,
            self._selected_table_id,
            self.profile.manual_timer_paused,
            game_open,
        )
        main_recording = self._route_recording and not self._recording_waiting
        marked_ids = self.recorder.marked_table_ids if main_recording and self.recorder is not None else None
        self.route_page.refresh(
            self.profile,
            main_recording,
            bool(self.recorder and self.recorder.mouse_trace_usable),
            game_open,
            marked_ids,
        )
        self.settings_page.refresh_waiting(self._route_recording and self._recording_waiting, game_open)
        self.lab_page.refresh(self.profile, self.vision)
        self.wizard.refresh(
            automation_active=self._route_recording or snapshot.state not in (RunState.STOPPED, RunState.ERROR)
        )
        self.settings_page.refresh(self.profile, window_status)

    def _idle_snapshot(self) -> RunSnapshot:
        now = time.time()
        statuses = []
        for table in sorted(self.profile.tables, key=lambda item: item.route_index):
            calibrated = any(wp.table_id == table.table_id for wp in self.profile.route.waypoints)
            remaining = remaining_seconds(table, self.profile, now)
            if not calibrated:
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
            state=RunState.STOPPED,
            table_statuses=statuses,
            reason="Автосбор остановлен. Таймеры продолжают отсчёт согласно выбранному режиму.",
        )

    def _on_runner_snapshot(self, snapshot: RunSnapshot) -> None:
        # Tk calls are made only by the main thread's dispatcher. Calling
        # root.after() here can deadlock against RouteRunner's state lock.
        with self._runner_snapshot_lock:
            self._pending_runner_snapshot = snapshot

    def _apply_snapshot(self, snapshot: RunSnapshot) -> None:
        self._last_snapshot = snapshot
        self._refresh_rows()
        self._save_profile(force=False)

    def _format_time(self, seconds: int | None) -> str:
        if seconds is None:
            return "—"
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _state_label(state: str) -> str:
        return {
            "growing": "Растёт",
            "ready": "Готов",
            "collecting": "Собирается",
            "needs calibration": "Нужна калибровка",
            "set timer": "Задай таймер",
            "stopped": "Остановлено",
            "waiting": "Ожидание",
            "wait_route": "Маршрут ожидания",
            "routing": "Маршрут",
            "paused": "Пауза",
            "error": "Ошибка",
        }.get(state, state)

    def _set_status(self, text: str) -> None:
        self._status_text = text
        self.status_message_var.set(text)

    def _on_calibration_profile_changed(self) -> None:
        self._save_profile(force=True)
        self._refresh_rows()

    def _save_profile(self, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_saved < 10:
            return
        with self.profile_lock:
            save_profile(self.profile, self.profile_path)
        self._last_saved = now

    def _on_close(self) -> None:
        if self._route_recording and self.recorder is not None:
            self.recorder.cancel()
        self._cancel_autocop_hotkey_capture(restart_hotkeys=False)
        if self.autocop_runner is not None:
            if not self.autocop_runner.stop():
                messagebox.showwarning(
                    "Автокоп ещё останавливается",
                    "Команда остановки отправлена. Закрой окно ещё раз после завершения распознавания.",
                    parent=self.root,
                )
                return
            self.autocop_runner = None
        if self.runner is not None:
            if not self.runner.stop():
                messagebox.showwarning(
                    "Маршрут ещё останавливается",
                    "Команда остановки отправлена. Закрой окно ещё раз после завершения операции.",
                    parent=self.root,
                )
                return
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        self._save_profile(force=True)
        overlay_bridge.set_edit_mode(False)
        self.reconnector.stop()
        self.table_watcher.stop()
        self.logger.info("Application closed")
        self._closing = True
        self.root.destroy()
