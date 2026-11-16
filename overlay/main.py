from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

import autocop_panel
import rr_style as rr
from combat_log import CombatLog


APP_DIR = Path(__file__).resolve().parent
STATS_PATH = APP_DIR / "stats.json"
SETTINGS_PATH = APP_DIR / "overlay_settings.json"
LOG_PATH = APP_DIR / "overlay.log"
FEED_PATH = APP_DIR / "farm_feed.json"  # written by Harvest Assistant: next harvest timer + package counters
HEARTBEAT_PATH = APP_DIR / "overlay_heartbeat.json"  # lets Harvest Assistant see/stop this overlay
EDIT_PATH = APP_DIR / "overlay_edit.json"  # {"ts": ...} refreshed by Hidden pool while its Overlay tab is open
COMMAND_PATH = APP_DIR / "overlay_command.json"  # {"action": "exit"} from Harvest Assistant
VISION_PATH = APP_DIR / "vision_feed.json"  # what the autopilot sees: place, target, heading (Harvest Assistant)
AUTOCOP_PATH = APP_DIR / "autocop_feed.json"  # the police-service helper: on/off, attempts, time (Harvest Assistant)
_INSTANCE_MUTEX_HANDLE: tuple[object, int] | None = None
KEY_COLOR = "#010203"
DEFAULT_OVERLAY_SETTINGS = {
    "scale": 1.0,
    "opacity": 0.9,
    "x_percent": 2,
    "y_percent": 4,
}
DEFAULT_BLOCKS = {
    "stats": {"enabled": True, "kills": True, "knocks": True, "deaths": True, "arrests": True,
              "playtime": True, "lifetime": True, "history": True},
    "plantation": {"enabled": True, "timer": True, "yellow": True, "purple": True, "green": True},
    "laboratory": {"enabled": False},
}
PERSONAL_COUNTERS = (
    ("knocks", "Ноки", "#d3b2ff"),
    ("deaths", "Смерти", "#ff927c"),
    ("arrests", "Аресты", "#79cfff"),
)
DEFAULT_HOTKEYS = {
    "toggle": {"modifiers": "Ctrl+Alt", "key": "F12"},
    "exit": {"modifiers": "Ctrl+Alt", "key": "F11"},
    "settings": {"modifiers": "Ctrl+Alt", "key": "F10"},
}
HOTKEY_MODIFIERS = {
    "Ctrl": 0x0002,
    "Alt": 0x0001,
    "Shift": 0x0004,
}
HOTKEY_MODIFIER_CHOICES = ("Ctrl+Alt", "Ctrl+Shift", "Alt+Shift", "Ctrl+Alt+Shift")
HOTKEY_VIRTUAL_KEYS = {
    **{f"F{number}": 0x70 + number - 1 for number in range(1, 13)},
    "Insert": 0x2D,
    "Home": 0x24,
    "End": 0x23,
    "Pause": 0x13,
}

try:
    import psutil
    import pystray
    import win32gui
    import win32process
    from PIL import Image, ImageDraw
except ImportError as import_error:
    _root = tk.Tk()
    _root.withdraw()
    messagebox.showerror(
        "Hidden pool overlay",
        "Не установлены зависимости. Запусти start.vbs из папки overlay.\n\n"
        f"Подробность: {import_error}",
    )
    raise SystemExit(1)


def log_error(message: str) -> None:
    try:
        with LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_overlay_settings() -> dict:
    settings = dict(DEFAULT_OVERLAY_SETTINGS)
    settings["hotkeys"] = {name: dict(binding) for name, binding in DEFAULT_HOTKEYS.items()}
    settings["blocks"] = {name: dict(values) for name, values in DEFAULT_BLOCKS.items()}
    if not SETTINGS_PATH.exists():
        return settings
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        settings["scale"] = min(1.6, max(0.7, float(saved.get("scale", settings["scale"]))))
        settings["opacity"] = min(1.0, max(0.35, float(saved.get("opacity", settings["opacity"]))))
        settings["x_percent"] = min(100, max(0, int(saved.get("x_percent", settings["x_percent"]))))
        settings["y_percent"] = min(100, max(0, int(saved.get("y_percent", settings["y_percent"]))))
        saved_blocks = saved.get("blocks", {})
        if isinstance(saved_blocks, dict):
            for block, defaults in DEFAULT_BLOCKS.items():
                values = saved_blocks.get(block, {})
                if isinstance(values, dict):
                    for key in defaults:
                        if isinstance(values.get(key), bool):
                            settings["blocks"][block][key] = values[key]
        saved_hotkeys = saved.get("hotkeys", {})
        if isinstance(saved_hotkeys, dict):
            for name, default in DEFAULT_HOTKEYS.items():
                binding = saved_hotkeys.get(name, {})
                if not isinstance(binding, dict):
                    continue
                modifiers = binding.get("modifiers", default["modifiers"])
                key = binding.get("key", default["key"])
                if modifiers in HOTKEY_MODIFIER_CHOICES and key in HOTKEY_VIRTUAL_KEYS:
                    settings["hotkeys"][name] = {"modifiers": modifiers, "key": key}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        log_error(f"Не удалось прочитать настройки оверлея: {error}")
    return settings


def save_overlay_settings(settings: dict) -> None:
    temp_path = SETTINGS_PATH.with_suffix(".tmp")
    try:
        temp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(SETTINGS_PATH)
    except OSError as error:
        log_error(f"Не удалось сохранить настройки оверлея: {error}")


def acquire_single_instance_mutex() -> bool:
    global _INSTANCE_MUTEX_HANDLE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    suffix = hashlib.sha256(os.path.normcase(str(APP_DIR)).encode("utf-8")).hexdigest()[:20]
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, f"Local\\HiddenPoolOverlay-{suffix}")
    if not handle:
        log_error(f"Не удалось создать mutex экземпляра; WinError={ctypes.get_last_error()}")
        return False
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX_HANDLE = (kernel32, handle)
    return True


def find_existing_overlay_instances() -> list[int]:
    """Other overlay processes started from this folder.

    A venv's pythonw.exe is only a launcher that spawns the real interpreter, so our own
    parent/children are not copies and must be skipped.
    """
    current = psutil.Process()
    own_family = {current.pid}
    try:
        own_family.update(parent.pid for parent in current.parents())
        own_family.update(child.pid for child in current.children(recursive=True))
    except psutil.Error:
        pass
    expected_cwd = os.path.normcase(os.path.abspath(str(APP_DIR)))
    pids: list[int] = []
    for process in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            info = process.info
            if info["pid"] in own_family or (info.get("name") or "").casefold() not in {"python.exe", "pythonw.exe"}:
                continue
            if os.path.normcase(os.path.abspath(info.get("cwd") or "")) != expected_cwd:
                continue
            args = info.get("cmdline") or []
            if any(Path(argument.strip('"')).name.casefold() == "main.py" for argument in args):
                pids.append(int(info["pid"]))
        except (psutil.Error, OSError, TypeError):
            continue
    return pids


def close_other_instances() -> int:
    """Terminate every other copy of the overlay so a fresh start always wins."""
    pids = find_existing_overlay_instances()
    processes = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.terminate()
            processes.append(process)
        except psutil.Error:
            continue
    _gone, alive = psutil.wait_procs(processes, timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    if processes:
        log_error(f"Закрыты старые копии оверлея: {pids}")
        time.sleep(0.4)  # let Windows release the single-instance mutex
    return len(processes)


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    pid: int
    process_name: str
    title: str
    created_at: float
    left: int
    top: int
    width: int
    height: int

    @property
    def display_name(self) -> str:
        return f"{self.title or self.process_name}   ·   {self.process_name}   ·   {format_screen(self.width, self.height)}"


def get_client_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Return client-area left, top, width, and height in desktop pixels."""
    client = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (client[0], client[1]))
    right, bottom = win32gui.ClientToScreen(hwnd, (client[2], client[3]))
    return left, top, max(0, right - left), max(0, bottom - top)


def format_aspect(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "—"
    ratio = width / height
    common = (
        (4 / 3, "4:3"),
        (5 / 4, "5:4"),
        (16 / 10, "16:10"),
        (16 / 9, "16:9"),
        (21 / 9, "21:9"),
        (32 / 9, "32:9"),
    )
    for expected, label in common:
        if abs(ratio - expected) < 0.018:
            return label
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def format_screen(width: int, height: int) -> str:
    return f"{width}×{height} · {format_aspect(width, height)}"


def is_rob_run_process(process_name: str) -> bool:
    normalized = process_name.strip().casefold()
    return normalized == "rob run.exe" or Path(normalized).stem.replace(" ", "") == "robrun"


def enumerate_game_windows() -> list[GameWindow]:
    windows: list[GameWindow] = []
    own_pid = os.getpid()

    def visit(hwnd: int, _extra: object) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetParent(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == own_pid:
                return True
            process = psutil.Process(pid)
            process_name = process.name()
            if process_name.lower() in {"python.exe", "pythonw.exe"}:
                return True
            target_process = is_rob_run_process(process_name)
            if not title and not target_process:
                return True
            left, top, width, height = get_client_bounds(hwnd)
            if (width < 480 or height < 300) and not target_process:
                return True
            windows.append(
                GameWindow(
                    hwnd=hwnd,
                    pid=pid,
                    process_name=process_name,
                    title=title,
                    created_at=process.create_time(),
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                )
            )
        except (psutil.Error, OSError, win32gui.error):
            return True
        return True

    win32gui.EnumWindows(visit, None)
    windows.sort(key=lambda item: item.width * item.height, reverse=True)
    return windows


def find_rob_run_window(windows: list[GameWindow]) -> GameWindow | None:
    candidates = [window for window in windows if is_rob_run_process(window.process_name)]
    return candidates[0] if candidates else None


class ProcessChooser:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Hidden pool · выбор игры")
        self.root.geometry("680x410")
        self.root.minsize(560, 330)
        self.root.configure(bg="#111719")
        self.selected: GameWindow | None = None
        self.windows: list[GameWindow] = []
        self.closed = False
        self._build()
        self.refresh()
        self.root.after(700, self._auto_search)

    def _build(self) -> None:
        title = tk.Label(
            self.root,
            text="Hidden pool  /  поиск ROB RUN.exe",
            bg="#111719",
            fg="#edf1ed",
            font=("Segoe UI", 15, "bold"),
            anchor="w",
        )
        title.pack(fill="x", padx=20, pady=(18, 2))
        tk.Label(
            self.root,
            text="Оверлей автоматически подключится к ROB RUN.exe. Если процесс пока не запущен, оставь это окно открытым — поиск продолжится.",
            bg="#111719",
            fg="#9da8a3",
            font=("Segoe UI", 9),
            anchor="w",
            wraplength=620,
        ).pack(fill="x", padx=20, pady=(0, 14))

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#192022",
            foreground="#e6ece6",
            fieldbackground="#192022",
            rowheight=28,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#20292a",
            foreground="#9eaaa3",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
        )
        style.map("Treeview", background=[("selected", "#33412e")])

        table_frame = tk.Frame(self.root, bg="#111719")
        table_frame.pack(fill="both", expand=True, padx=20)
        self.table = ttk.Treeview(
            table_frame,
            columns=("process", "size"),
            show="tree headings",
            selectmode="browse",
        )
        self.table.heading("#0", text="ОКНО")
        self.table.heading("process", text="ПРОЦЕСС")
        self.table.heading("size", text="РАЗРЕШЕНИЕ · ФОРМАТ")
        self.table.column("#0", width=350, minwidth=210)
        self.table.column("process", width=150, minwidth=110)
        self.table.column("size", width=145, minwidth=125, anchor="center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.table.bind("<Double-1>", lambda _event: self.choose())

        self.status = tk.Label(
            self.root,
            text="",
            bg="#111719",
            fg="#929e97",
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status.pack(fill="x", padx=20, pady=(9, 0))
        buttons = tk.Frame(self.root, bg="#111719")
        buttons.pack(fill="x", padx=20, pady=14)
        tk.Button(
            buttons,
            text="Обновить список",
            command=self.refresh,
            bg="#20292a",
            fg="#dbe2db",
            activebackground="#2d3938",
            activeforeground="#ffffff",
            relief="flat",
            padx=13,
            pady=8,
            font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Button(
            buttons,
            text="Запустить оверлей",
            command=self.choose,
            bg="#c7f36a",
            fg="#111719",
            activebackground="#d6ff82",
            activeforeground="#111719",
            relief="flat",
            padx=16,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

    def refresh(self) -> None:
        self.windows = enumerate_game_windows()
        for item in self.table.get_children():
            self.table.delete(item)
        for index, window in enumerate(self.windows):
            self.table.insert(
                "",
                "end",
                iid=str(index),
                text=window.title or window.process_name,
                values=(window.process_name, format_screen(window.width, window.height)),
            )
        if self.windows:
            self.status.configure(text=f"Найдено окон: {len(self.windows)}. ROB RUN.exe подключится автоматически; можно выбрать окно вручную.")
            self.table.selection_set("0")
        else:
            self.status.configure(text="Окна пока не найдены. Автопоиск ROB RUN.exe продолжается.")

    def _auto_search(self) -> None:
        if self.closed:
            return
        try:
            target = find_rob_run_window(enumerate_game_windows())
            if target is not None:
                self.selected = target
                self.status.configure(
                    text=f"Найден {target.process_name} · {format_screen(target.width, target.height)}. Подключаюсь…"
                )
                self.root.after(350, self._close_after_auto_select)
                return
            self.status.configure(text="Ищу ROB RUN.exe… Если игра уже открыта и не находится, выбери окно в списке.")
            self.root.after(1200, self._auto_search)
        except tk.TclError:
            return
        except Exception as error:
            log_error(f"Ошибка автопоиска ROB RUN.exe: {error}")
            try:
                self.root.after(1500, self._auto_search)
            except tk.TclError:
                pass

    def _close_after_auto_select(self) -> None:
        self.closed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def choose(self) -> None:
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo("Hidden pool", "Сначала выбери окно игры.", parent=self.root)
            return
        index = int(selection[0])
        self.selected = self.windows[index]
        self.closed = True
        self.root.destroy()

    def run(self) -> GameWindow | None:
        self.root.mainloop()
        return self.selected


def format_personal_counts(record: dict) -> str:
    return "   ".join(f"{label} {record.get(key, 0)}" for key, label, _color in PERSONAL_COUNTERS)


class StatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data: dict = {"version": 1, "sessions": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("sessions"), dict):
                self.data = loaded
        except (OSError, json.JSONDecodeError) as error:
            log_error(f"Не удалось прочитать stats.json: {error}")

    @staticmethod
    def session_key(window: GameWindow) -> str:
        return f"{window.pid}-{int(window.created_at * 1000)}"

    def get_or_create(self, window: GameWindow) -> tuple[str, dict]:
        with self.lock:
            key = self.session_key(window)
            sessions = self.data.setdefault("sessions", {})
            record = sessions.get(key)
            if record is None:
                record = {
                    "pid": window.pid,
                    "process_name": window.process_name,
                    "window_title": window.title,
                    "started_at": window.created_at,
                    "tracking_started_at": time.time(),
                    "ended_at": None,
                    "duration_seconds": 0,
                    "knocks": 0,
                    "deaths": 0,
                    "arrests": 0,
                }
                sessions[key] = record
            else:
                record["window_title"] = window.title
                record["last_tracking_started_at"] = time.time()
                record["ended_at"] = None
            self._save_locked()
            return key, record

    def record_combat_events(self, key: str, events: list[str]) -> dict:
        with self.lock:
            record = self.data["sessions"][key]
            for event in events:
                record[event] = int(record.get(event, 0)) + 1
            self._save_locked()
            return dict(record)

    def update_duration(self, key: str, duration: float, *, ended_at: float | None = None) -> None:
        with self.lock:
            record = self.data["sessions"].get(key)
            if not record:
                return
            record["duration_seconds"] = max(float(record.get("duration_seconds", 0)), duration)
            record["last_seen_at"] = time.time()
            if ended_at is not None:
                record["ended_at"] = ended_at
            self._save_locked()

    def totals(self) -> dict[str, int]:
        with self.lock:
            records = list(self.data.get("sessions", {}).values())
            return {
                "sessions": len(records),
                **{
                    key: sum(int(record.get(key, 0)) for record in records)
                    for key, _label, _color in PERSONAL_COUNTERS
                },
            }

    def recent_sessions(self, limit: int = 30) -> list[dict]:
        with self.lock:
            records = [dict(record) for record in self.data.get("sessions", {}).values()]
        records.sort(key=lambda record: float(record.get("started_at", 0)), reverse=True)
        return records[:limit]

    @staticmethod
    def session_key_for_record(record: dict) -> str:
        return f"{record.get('pid')}-{int(float(record.get('started_at', 0)) * 1000)}"

    def _save_locked(self) -> None:
        temp_path = self.path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.path)
        except OSError as error:
            log_error(f"Не удалось сохранить stats.json: {error}")


class HotkeyListener(threading.Thread):
    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001
    MOD_NOREPEAT = 0x4000
    ACTION_IDS = {1: "exit", 2: "toggle", 3: "settings"}

    def __init__(
        self,
        action_queue: queue.Queue[str],
        bindings: dict,
    ) -> None:
        super().__init__(name="HiddenPoolHotkeys", daemon=True)
        self.action_queue = action_queue
        self.stop_event = threading.Event()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.bindings_updates: queue.Queue[dict] = queue.Queue()
        self.bindings_updates.put(bindings)
        self.registered_ids: set[int] = set()

    def update_bindings(self, bindings: dict) -> None:
        self.bindings_updates.put(bindings)

    def _register_bindings(self, bindings: dict) -> None:
        for hotkey_id in self.registered_ids:
            self.user32.UnregisterHotKey(None, hotkey_id)
        self.registered_ids.clear()

        for hotkey_id, action in self.ACTION_IDS.items():
            binding = bindings.get(action, {})
            key = binding.get("key", DEFAULT_HOTKEYS[action]["key"])
            modifiers = binding.get("modifiers", DEFAULT_HOTKEYS[action]["modifiers"])
            virtual_key = HOTKEY_VIRTUAL_KEYS.get(key)
            if virtual_key is None:
                continue
            modifier_flags = 0
            for modifier in modifiers.split("+"):
                modifier_flags |= HOTKEY_MODIFIERS.get(modifier, 0)
            ctypes.set_last_error(0)
            if self.user32.RegisterHotKey(None, hotkey_id, modifier_flags | self.MOD_NOREPEAT, virtual_key):
                self.registered_ids.add(hotkey_id)
                log_error(f"Зарегистрировано сочетание {modifiers}+{key} ({action})")
            else:
                error_code = ctypes.get_last_error()
                log_error(
                    f"Не удалось зарегистрировать сочетание {modifiers}+{key} ({action}); "
                    f"WinError={error_code}"
                )

    def run(self) -> None:
        message = wintypes.MSG()
        # Create this thread's Windows message queue before registering global hotkeys.
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, self.PM_REMOVE)
        while not self.stop_event.is_set():
            try:
                bindings = self.bindings_updates.get_nowait()
            except queue.Empty:
                pass
            else:
                self._register_bindings(bindings)
            while self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, self.PM_REMOVE):
                if message.message == self.WM_HOTKEY:
                    action = self.ACTION_IDS.get(message.wParam)
                    if action is not None:
                        self.action_queue.put(action)
            self.stop_event.wait(0.05)
        for hotkey_id in self.registered_ids:
            self.user32.UnregisterHotKey(None, hotkey_id)


class OverlayApp:
    BASE_WIDTH = 294
    BASE_HEIGHT = 388
    FARM_WIDTH = 294
    FARM_HEIGHT = 86
    LAB_HEIGHT = 66

    def __init__(self, window: GameWindow) -> None:
        self.window = window
        self.store = StatsStore(STATS_PATH)
        self.session_key, self.session = self.store.get_or_create(window)
        self.settings = load_overlay_settings()
        self.farm_items: dict[str, int] = {}
        self._hwnds: dict = {}
        self._block_positions: dict[tk.Misc, tuple[int, int]] = {}
        self._screen_size = (window.width, window.height)
        self.stats_base_height = self.BASE_HEIGHT
        self.farm_base_width = self.FARM_WIDTH
        self._feed: dict = {}
        self._feed_mtime = 0.0
        self._next_feed_read = 0.0
        self._next_heartbeat = 0.0
        self._next_game_check = 0.0
        self._next_combat_check = 0.0
        self._combat_log = CombatLog() if is_rob_run_process(window.process_name) else None
        self._combat_log_error: str | None = None
        self._stats_dirty = True
        self._settings_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else 0.0
        self._settings_window: tk.Toplevel | None = None
        self._hotkeys_window: tk.Toplevel | None = None
        self.stop_event = threading.Event()
        self._user_hidden = False
        self.status_text = ""
        self.game_ended_at: float | None = None
        self.last_persisted_minute = -1
        self.hotkey_actions: queue.Queue[str] = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Hidden pool")
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY_COLOR)
        try:
            self.root.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError as error:
            log_error(f"Прозрачный фон по цвету недоступен: {error}")
        self._build_overlay()
        self._build_farm_overlay()
        self._build_vision_overlay()
        self._build_autocop_overlay()
        self._apply_geometry()
        self._apply_opacity()
        for window in self._block_windows():
            window.update_idletasks()
            self._make_click_through(window)
        self._sync_game_visibility()
        self._restore_game_focus()

        self.hotkeys = HotkeyListener(
            self.hotkey_actions,
            self.settings["hotkeys"],
        )
        self.hotkeys.start()
        self._start_tray_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.after(150, self._tick)

    def _farm_y_below_card(self, screen_height: int, scale: float, main_y_percent: int) -> int:
        main_height = int(round(self.BASE_HEIGHT * scale))
        farm_height = int(round(self.FARM_HEIGHT * scale))
        main_y = round(max(0, screen_height - main_height) * main_y_percent / 100)
        farm_y = main_y + main_height + round(12 * scale)
        farm_usable_height = max(1, screen_height - farm_height)
        return min(100, max(0, round(farm_y * 100 / farm_usable_height)))

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), "#101719")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 62, 62), radius=13, fill="#101719", outline="#465443", width=2)
        draw.polygon(((19, 32), (31, 19), (45, 32), (31, 45)), fill="#c7f36a")
        draw.ellipse((28, 29, 34, 35), fill="#101719")
        return image

    def _post_to_ui(self, callback: Callable[[], None]) -> None:
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def _start_tray_icon(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem(
                "Показать / скрыть оверлей",
                lambda _icon, _item: self._post_to_ui(self.toggle_visibility),
            ),
            pystray.MenuItem(
                "Настройки размера и положения...",
                lambda _icon, _item: self._post_to_ui(self.open_settings),
            ),
            pystray.MenuItem(
                "История сессий...",
                lambda _icon, _item: self._post_to_ui(self.open_session_history),
            ),
            pystray.MenuItem(
                "Сочетания клавиш...",
                lambda _icon, _item: self._post_to_ui(self.open_hotkey_settings),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Закрыть Hidden pool",
                lambda _icon, _item: self._post_to_ui(self.close),
            ),
        )
        self.tray_icon = pystray.Icon(
            "Hidden pool",
            self._create_tray_image(),
            title="Hidden pool · игровой оверлей",
            menu=menu,
        )
        self.tray_thread = threading.Thread(
            target=self.tray_icon.run,
            name="HiddenPoolTray",
            daemon=True,
        )
        self.tray_thread.start()

    def _build_overlay(self) -> None:
        self.canvas_items: dict[str, int] = {}
        self.canvas = tk.Canvas(
            self.root,
            width=self.BASE_WIDTH,
            height=self.BASE_HEIGHT,
            bg=KEY_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self._draw_overlay()

    def _make_block_window(self, title: str) -> tuple[tk.Toplevel, tk.Canvas]:
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title(title)
        window.overrideredirect(True)
        window.configure(bg=KEY_COLOR)
        try:
            window.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError as error:
            log_error(f"Прозрачный фон блока по цвету недоступен: {error}")
        canvas = tk.Canvas(window, width=10, height=10, bg=KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack()
        return window, canvas

    def _build_farm_overlay(self) -> None:
        self.farm_window, self.farm_canvas = self._make_block_window("Hidden pool · плантация")
        self.lab_window, self.lab_canvas = self._make_block_window("Hidden pool · лаборатория")
        self._draw_farm_overlay()
        self._draw_lab_overlay()

    def _block_windows(self) -> list[tk.Misc]:
        return [self.root, self.farm_window, self.lab_window]

    def _block_specs(self) -> list[tuple[str, tk.Misc, int, int]]:
        """Enabled blocks in stacking order: (name, window, base width, base height)."""
        blocks = self.settings["blocks"]
        specs: list[tuple[str, tk.Misc, int, int]] = []
        if blocks["stats"]["enabled"]:
            specs.append(("stats", self.root, self.BASE_WIDTH, self.stats_base_height))
        plantation = blocks["plantation"]
        if plantation["enabled"] and (plantation["timer"] or plantation["yellow"] or plantation["purple"] or plantation["green"]):
            specs.append(("plantation", self.farm_window, self.farm_base_width, self.FARM_HEIGHT))
        if blocks["laboratory"]["enabled"]:
            specs.append(("laboratory", self.lab_window, self.LAB_WIDTH, self.LAB_HEIGHT))
        return specs

    @staticmethod
    def _rounded_points(width: int, height: int, radius: int) -> list[float]:
        points: list[float] = []
        arcs = (
            (width - radius, radius, -90, 0),
            (width - radius, height - radius, 0, 90),
            (radius, height - radius, 90, 180),
            (radius, radius, 180, 270),
        )
        for center_x, center_y, start_angle, end_angle in arcs:
            for step in range(9):
                angle = math.radians(start_angle + (end_angle - start_angle) * step / 8)
                points.extend((center_x + radius * math.cos(angle), center_y + radius * math.sin(angle)))
        return points

    LAB_WIDTH = 294

    FARM_COUNTERS = (
        ("yellow", "#f5c542"),
        ("purple", "#b06cf0"),
        ("green", "#46c46a"),
    )

    def _draw_farm_overlay(self) -> None:
        scale = float(self.settings["scale"])
        px = lambda value: int(round(value * scale))
        cfg = self.settings["blocks"]["plantation"]
        counters = [(name, color) for name, color in self.FARM_COUNTERS if cfg.get(name, True)]
        show_timer = bool(cfg.get("timer", True))
        self.farm_base_width = self.FARM_WIDTH
        width, height = px(self.FARM_WIDTH), px(self.FARM_HEIGHT)
        canvas = self.farm_canvas
        canvas.delete("all")
        self.farm_items.clear()
        canvas.configure(width=width, height=height)
        canvas.create_rectangle(0, 0, width - 1, height - 1, fill=rr.PANEL, outline=rr.EDGE)
        rr.header(canvas, width, scale, "ПЛАНТАЦИЯ", rr.GREEN, rr.GREEN_INK, 128)
        self.farm_items["state"] = canvas.create_text(width - px(10), px(15), text="", anchor="e", fill=rr.MUTED,
                                                      font=rr.font(rr.BODY, 10, scale))
        if show_timer:
            canvas.create_text(px(12), px(44), text="СБОР ЧЕРЕЗ", anchor="w", fill=rr.MUTED, font=rr.font(rr.BODY, 9, scale))
            self.farm_items["timer"] = canvas.create_text(px(11), px(65), text="— : —", anchor="w", fill=rr.DIM,
                                                          font=rr.font(rr.HEAD, 24, scale))
        # the package counters: a colour bar on the left, like the game's pick-up notification
        cell, gap = 56, 3
        left = (self.FARM_WIDTH - 12 - len(counters) * cell - (len(counters) - 1) * gap) if show_timer else 12
        for index, (name, color) in enumerate(counters):
            x0 = left + index * (cell + gap)
            canvas.create_rectangle(px(x0), px(40), px(x0 + cell), px(74), fill=rr.STRIP, outline="")
            canvas.create_rectangle(px(x0), px(40), px(x0 + 4), px(74), fill=color, outline="")
            self.farm_items[name] = canvas.create_text(px(x0 + 12), px(57), text="0", anchor="w", fill=rr.TEXT,
                                                       font=rr.font(rr.HEAD, 17, scale))
        canvas.create_rectangle(0, height - px(3), width, height, fill=rr.STRIP, outline="")
        self.farm_items["strip"] = canvas.create_rectangle(0, height - px(3), width, height, fill=rr.GREEN, outline="")
        self._refresh_farm()

    def _draw_lab_overlay(self) -> None:
        scale = float(self.settings["scale"])
        px = lambda value: int(round(value * scale))
        width, height = px(self.LAB_WIDTH), px(self.LAB_HEIGHT)
        canvas = self.lab_canvas
        canvas.delete("all")
        canvas.configure(width=width, height=height)
        canvas.create_rectangle(0, 0, width - 1, height - 1, fill=rr.PANEL, outline=rr.EDGE)
        rr.header(canvas, width, scale, "ЛАБОРАТОРИЯ", rr.AMBER, rr.INK, 140)
        self.lab_state_item = canvas.create_text(width - px(10), px(15), text="", anchor="e", fill=rr.MUTED,
                                                 font=rr.font(rr.BODY, 10, scale))
        canvas.create_text(px(12), px(48), text="ГОТОВО ЧЕРЕЗ", anchor="w", fill=rr.MUTED, font=rr.font(rr.BODY, 9, scale))
        self.lab_timer_item = canvas.create_text(width - px(12), px(48), text="— : —", anchor="e", fill=rr.DIM,
                                                 font=rr.font(rr.HEAD, 20, scale))

    @staticmethod
    def _format_countdown(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

    def _read_feed(self) -> None:
        now = time.monotonic()
        if now < self._next_feed_read:
            return
        self._next_feed_read = now + 0.5
        try:
            mtime = FEED_PATH.stat().st_mtime
        except OSError:
            self._feed = {}
            self._feed_mtime = 0.0
            return
        if mtime == self._feed_mtime:
            return
        try:
            data = json.loads(FEED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._feed = data
                self._feed_mtime = mtime
        except (OSError, ValueError):
            pass  # half-written file: keep the previous values, retry next tick

    def _refresh_farm(self) -> None:
        if not self.farm_items:
            return
        self._read_feed()
        feed = self._feed
        wall_now = time.time()
        age = wall_now - float(feed.get("updated_at", 0) or 0)
        remaining: float | None = None
        if feed.get("paused"):
            value = feed.get("remaining_seconds")
            remaining = float(value) if value is not None else None
        elif feed.get("next_due_at") is not None:
            remaining = float(feed["next_due_at"]) - wall_now
        elif feed.get("remaining_seconds") is not None and age < 6:
            remaining = float(feed["remaining_seconds"]) - age
        if remaining is None:
            text, color, state, strip = "— : —", rr.DIM, "нет данных", rr.STRIP
        elif remaining <= 0:
            text, color, state, strip = "СБОР", rr.LIME, "пора собирать", rr.LIME
        elif feed.get("paused"):
            text, color, state, strip = self._format_countdown(remaining), rr.AMBER_TEXT, "пауза", rr.AMBER
        else:
            text, color, state, strip = self._format_countdown(remaining), rr.LIME, "все столы", rr.GREEN
        canvas = self.farm_canvas
        if "timer" in self.farm_items:
            canvas.itemconfigure(self.farm_items["timer"], text=text, fill=color)
        canvas.itemconfigure(self.farm_items["state"], text=state)
        canvas.itemconfigure(self.farm_items["strip"], fill=strip)
        counts = feed.get("counts", {}) if isinstance(feed.get("counts"), dict) else {}
        for name, _color in self.FARM_COUNTERS:
            if name in self.farm_items:
                canvas.itemconfigure(self.farm_items[name], text=str(int(counts.get(name, 0) or 0)))

    # ---- autopilot "vision": boxes over the game showing what the bot sees and where it is going -----------

    VISION_ACCENT = "#c6fb33"
    VISION_PLACE = "#6cc4ff"
    VISION_SEARCH = "#e9b93a"
    VISION_LOST = "#ff7a70"

    # ---- the police-service helper («Автокоп»): its own panel at the right edge of the game ------------------

    AUTOCOP_DONE_SHOWN = 15 * 60   # on duty: the result stays this long

    def _build_autocop_overlay(self) -> None:
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Hidden pool · автокоп")
        window.overrideredirect(True)
        window.configure(bg=KEY_COLOR)
        try:
            window.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError as error:
            log_error(f"Прозрачный фон автокопа недоступен: {error}")
        canvas = tk.Canvas(window, width=10, height=10, bg=KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack()
        self.autocop_window, self.autocop_canvas = window, canvas
        self._autocop: dict = {}
        self._autocop_mtime = 0.0
        self._autocop_shown = False
        self._autocop_items: dict = {}
        self._autocop_drawn: tuple | None = None
        self._autocop_second = -1
        self._autocop_animating = False

    def _read_autocop(self) -> None:
        try:
            mtime = AUTOCOP_PATH.stat().st_mtime
        except OSError:
            self._autocop, self._autocop_mtime = {}, 0.0
            return
        if mtime == self._autocop_mtime:
            return
        try:
            data = json.loads(AUTOCOP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._autocop, self._autocop_mtime = data, mtime
        except (OSError, ValueError):
            pass  # half-written file: keep what we had

    def _refresh_autocop(self) -> None:
        self._read_autocop()
        data = self._autocop
        now = time.time()
        if data.get("state") == "done":
            fresh = bool(data.get("active")) and now - float(data.get("ended_at") or 0) < self.AUTOCOP_DONE_SHOWN
        else:
            fresh = bool(data.get("active")) and now - float(data.get("ts", 0) or 0) < 4.0
        visible = fresh and not self._user_hidden and self._game_or_settings_has_focus()
        if not visible:
            if self._autocop_shown:
                self.autocop_window.withdraw()
                self._autocop_shown = False
            return
        try:
            left, top, width, height = get_client_bounds(self.window.hwnd)
        except (win32gui.error, OSError):
            return
        if width <= 0 or height <= 0:
            return
        scale = float(self.settings["scale"])
        stamp = (self._autocop_mtime, scale)
        if stamp != self._autocop_drawn:
            self._autocop_drawn = stamp
            self._autocop_items = autocop_panel.draw(self.autocop_canvas, data, scale)
            self._autocop_second = -1
        second = autocop_panel.elapsed(data)
        if second != self._autocop_second and self._autocop_items:
            self._autocop_second = second
            autocop_panel.tick_clock(self.autocop_canvas, self._autocop_items, data)
        if not self._autocop_shown:
            self.autocop_window.deiconify()
            self.autocop_window.update_idletasks()
            self._make_click_through(self.autocop_window)
            self._autocop_shown = True
            try:
                self.autocop_window.wm_attributes("-alpha", float(self.settings["opacity"]))
            except tk.TclError:
                pass
            if not self._autocop_animating:
                self._autocop_animating = True
                self.root.after(60, self._animate_autocop)
        # the right edge of the game, below its money counter
        panel_w = round(autocop_panel.BASE_WIDTH * scale)
        panel_h = round(autocop_panel.BASE_HEIGHT * scale)
        x = left + width - panel_w - round(width * 0.012)
        y = top + round(height * 0.11)
        try:
            ctypes.windll.user32.SetWindowPos(
                self._hwnds[self.autocop_window], ctypes.c_void_p(0), x, y, panel_w, panel_h, 0x0010 | 0x0004,
            )
        except (KeyError, OSError):
            pass

    def _animate_autocop(self) -> None:
        if self.stop_event.is_set() or not self._autocop_shown:
            self._autocop_animating = False
            return
        try:
            autocop_panel.animate(self.autocop_canvas, self._autocop_items)
        except tk.TclError:
            pass
        self.root.after(60, self._animate_autocop)

    def _build_vision_overlay(self) -> None:
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("Hidden pool · зрение")
        window.overrideredirect(True)
        window.configure(bg=KEY_COLOR)
        try:
            window.wm_attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError as error:
            log_error(f"Прозрачный фон слоя зрения недоступен: {error}")
        canvas = tk.Canvas(window, width=10, height=10, bg=KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack()
        self.vision_window, self.vision_canvas = window, canvas
        self._vision: dict = {}
        self._vision_mtime = 0.0
        self._vision_shown = False
        self._vision_size = (0, 0)
        self._vision_drawn: tuple | None = None
        self._next_vision_read = 0.0

    def _read_vision(self) -> None:
        try:
            mtime = VISION_PATH.stat().st_mtime
        except OSError:
            self._vision = {}
            self._vision_mtime = 0.0
            return
        if mtime == self._vision_mtime:
            return
        try:
            data = json.loads(VISION_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._vision = data
                self._vision_mtime = mtime
        except (OSError, ValueError):
            pass  # half-written file: keep what we had

    def _refresh_vision(self) -> None:
        now = time.monotonic()
        if now < self._next_vision_read:
            return
        self._next_vision_read = now + 0.12
        self._read_vision()
        data = self._vision
        fresh = bool(data.get("active")) and time.time() - float(data.get("ts", 0) or 0) < 3.0
        visible = fresh and not self._user_hidden and self._game_or_settings_has_focus()
        if not visible:
            if self._vision_shown:
                self.vision_window.withdraw()
                self._vision_shown = False
            return
        try:
            left, top, width, height = get_client_bounds(self.window.hwnd)
        except (win32gui.error, OSError):
            return
        if width <= 0 or height <= 0:
            return
        if not self._vision_shown:
            self.vision_window.deiconify()
            self.vision_window.update_idletasks()
            self._make_click_through(self.vision_window)
            self._vision_shown = True
            self._vision_size = (0, 0)
            self._vision_drawn = None
            try:
                self.vision_window.wm_attributes("-alpha", float(self.settings["opacity"]))
            except tk.TclError:
                pass
        if self._vision_size != (width, height):
            self.vision_canvas.configure(width=width, height=height)
            self._vision_size = (width, height)
            self._vision_drawn = None
        try:
            ctypes.windll.user32.SetWindowPos(
                self._hwnds[self.vision_window], ctypes.c_void_p(0), left, top, width, height, 0x0010 | 0x0004,
            )
        except (KeyError, OSError):
            pass
        stamp = (self._vision_mtime, width, height, float(self.settings["scale"]))
        if stamp != self._vision_drawn:
            self._vision_drawn = stamp
            self._draw_vision(data, width, height)

    def _draw_vision(self, data: dict, width: int, height: int) -> None:
        canvas = self.vision_canvas
        canvas.delete("all")
        scale = float(self.settings["scale"])
        px = lambda value: int(round(value * scale))
        frame_w, frame_h = (data.get("frame") or [width, height])[:2]
        fx, fy = width / max(1, frame_w), height / max(1, frame_h)

        def point(item: object) -> tuple[float, float]:
            return float(item[0]) * fx, float(item[1]) * fy

        def corners(x1: float, y1: float, x2: float, y2: float, color: str, size: int, weight: int) -> None:
            for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x2, y2, -1, -1), (x1, y2, 1, -1)):
                canvas.create_line(cx, cy + dy * size, cx, cy, cx + dx * size, cy, fill=color, width=weight)

        def tag(x: float, y: float, text: str, color: str) -> None:
            """A slanted colour tab with the name on it, like the headers of the overlay blocks."""
            text_item = canvas.create_text(x + px(8), y, text=text.upper(), anchor="w", fill=rr.INK,
                                           font=rr.font(rr.HEAD, 11, scale))
            x1, y1, x2, y2 = canvas.bbox(text_item)
            plate = canvas.create_polygon(x1 - px(7), y1 - px(2), x2 + px(14), y1 - px(2), x2 + px(7), y2 + px(2),
                                          x1 - px(7), y2 + px(2), fill=color, outline="")
            canvas.tag_raise(text_item, plate)

        mode = str(data.get("mode", ""))
        accent = {"lost": self.VISION_LOST, "search": self.VISION_SEARCH, "tables": self.VISION_ACCENT if data.get("ready") else self.VISION_SEARCH}.get(mode, self.VISION_ACCENT)

        # where the bot thinks it is: the matched points and the box around them
        place = data.get("place") or {}
        points = [point(p) for p in place.get("points", [])]
        for x, y in points:
            canvas.create_rectangle(x - 2, y - 2, x + 2, y + 2, fill=self.VISION_PLACE, outline="")
        if len(points) >= 4:
            xs, ys = sorted(p[0] for p in points), sorted(p[1] for p in points)
            low, high = len(points) // 10, len(points) - 1 - len(points) // 10  # ignore stray points
            x1, y1, x2, y2 = xs[low] - 10, ys[low] - 10, xs[high] + 10, ys[high] + 10
            corners(x1, y1, x2, y2, self.VISION_PLACE, px(12), max(1, px(2)))
            tag(x1, max(px(12), y1 - px(12)), str(place.get("label", "Узнаю место")), self.VISION_PLACE)

        # the target: found on the screen (box) or still being searched (arrow towards it)
        target = data.get("target") or {}
        quad = target.get("quad")
        if quad:
            pts = [point(p) for p in quad]
            canvas.create_polygon(*[c for p in pts for c in p], fill="", outline=self.VISION_ACCENT, width=max(2, px(2)))
            for x, y in pts:
                canvas.create_rectangle(x - px(4), y - px(4), x + px(4), y + px(4), fill=self.VISION_ACCENT, outline="")
            cx, cy = sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4
            canvas.create_line(cx - px(10), cy, cx + px(10), cy, fill=self.VISION_ACCENT, width=max(1, px(2)))
            canvas.create_line(cx, cy - px(10), cx, cy + px(10), fill=self.VISION_ACCENT, width=max(1, px(2)))
            tag(min(p[0] for p in pts), max(px(12), min(p[1] for p in pts) - px(14)),
                f"{target.get('label', 'Цель')} · {target.get('note', 'вижу')}", self.VISION_ACCENT)
        elif target.get("label") and mode != "lost":
            turn = float(data.get("turn", 0) or 0)
            side = 1 if turn >= 0 else -1
            ex = width - px(36) if side > 0 else px(36)
            ey = height * 0.46
            canvas.create_polygon(ex - side * px(14), ey - px(16), ex + side * px(14), ey, ex - side * px(14), ey + px(16),
                                  fill=self.VISION_SEARCH, outline="")
            tag(ex - px(150) if side > 0 else ex + px(8), ey - px(30), f"{target['label']} · ищу", self.VISION_SEARCH)

        # tables of the plantation that are still growing
        for table in data.get("tables", []):
            x1, y1 = point((table["x"] - 8, table["y"] - 8))
            x2, y2 = point((table["x"] + table["w"] + 8, table["y"] + table["h"] + 8))
            state = table.get("state")
            color = self.VISION_ACCENT if state == "ready" else "#93a099" if state == "idle" else self.VISION_SEARCH
            corners(x1, y1, x2, y2, color, px(10), max(2, px(2)))
            tag(x1, y2 + px(14), str(table.get("label", "Стол")), color)

        # radar: the map the bot learned from the routes that were walked, and where it is on it
        radar = data.get("radar")
        if radar and (radar.get("paths") or radar.get("plan")):
            self._draw_radar(canvas, radar, width, px)

        # status card, top centre: a block of the overlay with the stage on its tab
        card_w, card_h = px(320), px(58)
        x0, y0 = (width - card_w) // 2, px(16)
        title = "ПЛАНТАЦИЯ · ЗРЕНИЕ" if mode == "tables" else f"АВТОПИЛОТ · ЭТАП {data.get('stage', '')}".strip()
        canvas.create_rectangle(x0, y0, x0 + card_w, y0 + card_h, fill=rr.PANEL, outline=rr.EDGE)
        canvas.create_rectangle(x0, y0, x0 + card_w, y0 + px(24), fill=rr.STRIP, outline="")
        label = canvas.create_text(x0 + px(9), y0 + px(12), text=title, anchor="w", fill=rr.INK, font=rr.font(rr.HEAD, 12, scale))
        lx1 = canvas.bbox(label)[2]
        tab = canvas.create_polygon(x0, y0, lx1 + px(18), y0, lx1 + px(9), y0 + px(24), x0, y0 + px(24), fill=accent, outline="")
        canvas.tag_raise(label, tab)
        canvas.create_text(x0 + px(12), y0 + px(41), text=str(data.get("status", "")), anchor="w", fill=rr.TEXT,
                           font=rr.font(rr.BODY, 13, scale))
        canvas.create_oval(x0 + card_w - px(18), y0 + px(37), x0 + card_w - px(10), y0 + px(45), fill=accent, outline="")
        canvas.create_rectangle(x0, y0 + card_h - px(3), x0 + card_w, y0 + card_h, fill=accent, outline="")

    def _draw_radar(self, canvas: tk.Canvas, radar: dict, width: int, px: Callable[[float], int]) -> None:
        card_w, card_h = px(236), px(186)
        x0, y0 = width - card_w - px(16), px(64)
        canvas.create_rectangle(x0, y0, x0 + card_w, y0 + card_h, fill=rr.PANEL, outline=rr.EDGE)
        canvas.create_rectangle(x0, y0, x0 + card_w, y0 + px(24), fill=rr.STRIP, outline="")
        label = canvas.create_text(x0 + px(9), y0 + px(12), text=str(radar.get("title") or "РАДАР").upper(), anchor="w",
                                   fill=rr.INK, font=rr.font(rr.HEAD, 12, float(self.settings["scale"])))
        lx1 = canvas.bbox(label)[2]
        canvas.tag_raise(label, canvas.create_polygon(x0, y0, lx1 + px(18), y0, lx1 + px(9), y0 + px(24), x0, y0 + px(24),
                                                      fill=rr.BLUE, outline=""))
        plan = radar.get("plan")
        if plan:  # the plan of the grow room: walls, stairs, tables, pillars, and the bot on it
            room_w, room_h = plan["room"]
            inner_w, inner_h = card_w - px(28), card_h - px(44)
            scale = min(inner_w / (room_w + 7.0), inner_h / (room_h + 1.0))
            ox = x0 + px(14) + (inner_w - (room_w + 7.0) * scale) / 2 + 2.5 * scale   # room_x is shifted: the container lies to the west
            oy = y0 + px(30) + (inner_h - (room_h + 1.0) * scale) / 2

            def at_plan(x: float, y: float) -> tuple[float, float]:
                return ox + x * scale, oy + (room_h - y) * scale  # north up

            x1, y1 = at_plan(0, room_h)
            x2, y2 = at_plan(room_w, 0)
            canvas.create_rectangle(x1, y1, x2, y2, outline="#46524d", width=max(1, px(1)))
            sx1, sy1 = at_plan(plan["stairs"][0], plan["stairs"][3])
            sx2, sy2 = at_plan(plan["stairs"][2], plan["stairs"][1])
            canvas.create_rectangle(sx1, sy1, sx2, sy2, outline="#79cfff", width=max(1, px(1)))
            for left, bottom, right, top in plan["tables"]:
                a1, b1 = at_plan(left, top)
                a2, b2 = at_plan(right, bottom)
                canvas.create_rectangle(a1, b1, a2, b2, outline="#f1c66d", width=max(1, px(2)))
            for cx_, cy_, size in plan["pillars"]:
                a1, b1 = at_plan(cx_ - size / 2, cy_ + size / 2)
                a2, b2 = at_plan(cx_ + size / 2, cy_ - size / 2)
                canvas.create_rectangle(a1, b1, a2, b2, fill="#93a099", outline="")
            goal = radar.get("goal")
            if goal:
                gx, gy = at_plan(*goal)
                canvas.create_line(gx - px(5), gy, gx + px(5), gy, fill="#c7f36a", width=max(1, px(2)))
                canvas.create_line(gx, gy - px(5), gx, gy + px(5), fill="#c7f36a", width=max(1, px(2)))
            bot = radar.get("bot")
            if bot:
                bx, by = at_plan(bot[0], bot[1])
                angle = math.radians(bot[2])
                tip = (bx + px(8) * math.sin(angle), by - px(8) * math.cos(angle))
                back_left = (bx + px(5) * math.sin(angle + 2.5), by - px(5) * math.cos(angle + 2.5))
                back_right = (bx + px(5) * math.sin(angle - 2.5), by - px(5) * math.cos(angle - 2.5))
                canvas.create_polygon(*tip, *back_left, *back_right, fill="#c7f36a", outline="")
            return
        points = [p for path in radar["paths"] for p in path] + radar.get("doors", []) + radar.get("tables", [])
        for key in ("bot", "goal"):
            if radar.get(key):
                points.append(radar[key][:2])
        if not points:
            return
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        left, right, low, high = min(xs), max(xs), min(ys), max(ys)
        if radar.get("bounds"):  # zoomed: the map says which part to show
            left, low, right, high = radar["bounds"]
        inner_w, inner_h = card_w - px(28), card_h - px(44)
        scale = min(inner_w / max(1.0, right - left), inner_h / max(1.0, high - low))
        ox = x0 + px(14) + (inner_w - (right - left) * scale) / 2
        oy = y0 + px(30) + (inner_h - (high - low) * scale) / 2

        def at(x: float, y: float) -> tuple[float, float]:
            return ox + (x - left) * scale, oy + (high - y) * scale  # north up

        for path in radar["paths"]:
            if len(path) > 1:
                canvas.create_line(*[c for p in path for c in at(*p)], fill="#46524d", width=max(1, px(1)))
        for x, y in radar.get("doors", []):
            cx, cy = at(x, y)
            canvas.create_rectangle(cx - px(3), cy - px(3), cx + px(3), cy + px(3), outline="#79cfff", width=max(1, px(1)))
        for x, y in radar.get("tables", []):
            cx, cy = at(x, y)
            canvas.create_oval(cx - px(4), cy - px(4), cx + px(4), cy + px(4), outline="#f1c66d", width=max(1, px(2)))
        goal = radar.get("goal")
        if goal:
            cx, cy = at(*goal)
            canvas.create_line(cx - px(5), cy, cx + px(5), cy, fill="#c7f36a", width=max(1, px(2)))
            canvas.create_line(cx, cy - px(5), cx, cy + px(5), fill="#c7f36a", width=max(1, px(2)))
        bot = radar.get("bot")
        if bot:
            cx, cy = at(bot[0], bot[1])
            angle = math.radians(bot[2])
            tip = (cx + px(8) * math.sin(angle), cy - px(8) * math.cos(angle))
            back_left = (cx + px(5) * math.sin(angle + 2.5), cy - px(5) * math.cos(angle + 2.5))
            back_right = (cx + px(5) * math.sin(angle - 2.5), cy - px(5) * math.cos(angle - 2.5))
            canvas.create_polygon(*tip, *back_left, *back_right, fill="#c7f36a", outline="")

    def _refresh_lab(self) -> None:
        if not self.settings["blocks"]["laboratory"]["enabled"] or self.lab_window.state() == "withdrawn":
            return
        self._read_feed()
        now = time.time()
        age = now - float(self._feed.get("updated_at", 0) or 0)
        value = self._feed.get("lab_remaining_seconds")
        if value is None or age >= 6:
            text, color, state = "— : —", rr.DIM, "нет данных"
        else:
            paused = self._feed.get("paused")
            due_at = self._feed.get("lab_due_at")
            remaining = float(value)
            if not paused:
                remaining = float(due_at) - now if due_at is not None else remaining - age
            text = "ГОТОВО" if remaining <= 0 else self._format_countdown(remaining)
            color = rr.AMBER_TEXT if paused else rr.LIME
            state = "пауза" if paused else ("готово" if remaining <= 0 else "варится")
        if (text != self.lab_canvas.itemcget(self.lab_timer_item, "text") or
                color != self.lab_canvas.itemcget(self.lab_timer_item, "fill")):
            self.lab_canvas.itemconfigure(self.lab_timer_item, text=text, fill=color)
        self.lab_canvas.itemconfigure(self.lab_state_item, text=state)

    def _write_heartbeat(self) -> None:
        now = time.monotonic()
        if now < self._next_heartbeat:
            return
        self._next_heartbeat = now + 2.0
        try:
            HEARTBEAT_PATH.write_text(
                json.dumps({"pid": os.getpid(), "ts": time.time(), "hidden": self._user_hidden}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _exit_requested_by_assistant(self) -> bool:
        try:
            command = json.loads(COMMAND_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        try:
            COMMAND_PATH.unlink()
        except OSError:
            pass
        # Ignore stale commands left from an earlier session.
        return command.get("action") == "exit" and time.time() - float(command.get("ts", 0)) < 15

    def _reload_settings_if_changed(self) -> None:
        try:
            mtime = SETTINGS_PATH.stat().st_mtime
        except OSError:
            return
        if mtime == self._settings_mtime:
            return
        self._settings_mtime = mtime
        previous_hotkeys = self.settings.get("hotkeys")
        self.settings = load_overlay_settings()
        self._apply_settings_preview()
        if hasattr(self, "hotkeys") and self.settings["hotkeys"] != previous_hotkeys:
            self.hotkeys.update_bindings(self.settings["hotkeys"])

    def _draw_overlay(self) -> None:
        scale = float(self.settings["scale"])
        px = lambda value: int(round(value * scale))
        cfg = self.settings["blocks"]["stats"]
        available_counters = (("kills", "Убийства", rr.SOFT), *PERSONAL_COUNTERS)
        counters = [counter for counter in available_counters if cfg[counter[0]]]
        # Rows that are switched off do not exist; the card is only as tall as what is shown.
        sections = [("header", 56)]
        if counters:
            sections.append(("counters", 62))
        if cfg.get("playtime", True):
            sections.append(("playtime", 28))
        if cfg.get("lifetime", True):
            sections.append(("lifetime", 58))
        if cfg.get("history", True):
            sections.append(("history", 30))
        base_height = sum(height for _name, height in sections) + 2
        self.stats_base_height = base_height
        width, height = px(self.BASE_WIDTH), px(base_height)
        canvas = self.canvas
        canvas.delete("all")
        self.canvas_items.clear()
        canvas.configure(width=width, height=height)

        def text(name: str, x: float, y: float, value: str, family: str, size: float, color: str, anchor: str = "w") -> None:
            self.canvas_items[name] = canvas.create_text(px(x), px(y), text=value, anchor=anchor, fill=color,
                                                         font=rr.font(family, size, scale))

        canvas.create_rectangle(0, 0, width - 1, height - 1, fill=rr.PANEL, outline=rr.EDGE)
        right = self.BASE_WIDTH - 12
        y = 0
        for name, section_height in sections:
            if name == "header":
                rr.header(canvas, width, scale, "HIDDEN POOL", rr.LIME, rr.INK, 138)
                text("session_state", right + 2, 15, "СЕССИЯ", rr.HEAD, 12, rr.LIME, anchor="e")
                x0 = canvas.bbox(self.canvas_items["session_state"])[0]
                self.canvas_items["session_dot"] = canvas.create_oval(x0 - px(13), px(11), x0 - px(5), px(19), fill=rr.LIME, outline="")
                started = time.strftime("%H:%M", time.localtime(self.window.created_at))
                text("session_start", 12, 43, f"СТАРТ {started}", rr.BODY, 10, rr.MUTED)
                text("screen", right, 43, format_screen(*self._screen_size), rr.BODY, 10, rr.MUTED, anchor="e")
            elif name == "counters":
                gap = 3
                cell = (right - 12 - gap * (len(counters) - 1)) / len(counters)
                for index, (key, label, color) in enumerate(counters):
                    x0 = 12 + index * (cell + gap)
                    canvas.create_rectangle(px(x0), px(y + 4), px(x0 + cell), px(y + 54), fill=rr.STRIP, outline="")
                    if key != "kills":
                        canvas.create_rectangle(px(x0), px(y + 52), px(x0 + cell), px(y + 54), fill=color, outline="")
                    text(f"{key}_label", x0 + 8, y + 16, label.upper(), rr.BODY, 9, rr.MUTED)
                    text(f"{key}_value", x0 + 7, y + 37, "—" if key == "kills" else "0", rr.HEAD, 22,
                         rr.TEXT if key == "kills" else color)
            elif name == "playtime":
                text("playtime_label", 12, y + 14, "В ИГРЕ", rr.BODY, 9, rr.MUTED)
                text("playtime", right, y + 14, "0 м", rr.HEAD, 16, rr.TEXT, anchor="e")
            elif name == "lifetime":
                canvas.create_rectangle(px(12), px(y + 4), px(right), px(y + 52), fill=rr.STRIP, outline="")
                canvas.create_rectangle(px(12), px(y + 4), px(15), px(y + 52), fill=rr.LIME, outline="")
                text("lifetime_title", 22, y + 16, "ЗА ВСЁ ВРЕМЯ", rr.BODY, 9, rr.MUTED)
                text("session_count", right - 8, y + 16, "0 СЕССИЙ", rr.BODY, 9, rr.SOFT, anchor="e")
                if cfg["kills"]:
                    text("lifetime_values", 22, y + 31, format_personal_counts({}), rr.BODY, 11, rr.SOFT)
                    text("lifetime_kills", 22, y + 44, "Убийства —", rr.BODY, 10, rr.MUTED)
                else:
                    text("lifetime_values", 22, y + 36, format_personal_counts({}), rr.BODY, 11, rr.SOFT)
            elif name == "history":
                canvas.create_rectangle(1, px(y), width - 1, height - 1, fill=rr.STRIP, outline="")
                text("history_label", 12, y + 15, "ИСТОРИЯ СЕССИЙ", rr.BODY, 9, rr.MUTED)
                rr.key(canvas, px(right), px(y + 15), "+", scale)
            y += section_height

    def _blink_session(self) -> None:
        """The dot next to «СЕССИЯ» blinks while the game is on."""
        item = self.canvas_items.get("session_dot")
        if item is None:
            return
        on = self.game_ended_at is None and (time.monotonic() % 1.2) < 0.75
        try:
            self.canvas.itemconfigure(item, fill=rr.LIME if on else rr.STRIP)
        except tk.TclError:
            pass

    def _apply_geometry(self) -> None:
        self._block_positions.clear()
        scale = float(self.settings["scale"])
        for name, window, base_width, base_height in self._block_specs():
            width = int(round(base_width * scale))
            height = int(round(base_height * scale))
            canvas = {"stats": self.canvas, "plantation": self.farm_canvas, "laboratory": self.lab_canvas}[name]
            canvas.configure(width=width, height=height)
            window.geometry(f"{width}x{height}+0+0")

    def _apply_opacity(self) -> None:
        try:
            for window in self._block_windows():
                window.wm_attributes("-alpha", float(self.settings["opacity"]))
        except tk.TclError as error:
            log_error(f"Не удалось применить прозрачность: {error}")

    def _make_click_through(self, target: tk.Misc | None = None) -> None:
        try:
            target = target or self.root
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = target.winfo_id()
            hwnd = user32.GetAncestor(hwnd, 2) or hwnd
            self._hwnds[target] = hwnd
            self._block_positions.pop(target, None)
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            # Make the game window the HUD's owner. Owned windows follow their owner
            # in z-order and are hidden when the owner is minimized.
            set_long(hwnd, -8, ctypes.c_ssize_t(int(self.window.hwnd)))
            style = get_long(hwnd, -20)
            ex_style = style | 0x00000020 | 0x00080000 | 0x08000000 | 0x00000080
            set_long(hwnd, -20, ex_style)
            try:
                # WDA_EXCLUDEFROMCAPTURE: the autopilot reads the game from screenshots. Our own labels («Дверь · открыть»,
                # frames, the radar card) must not be on them, or it reads its own words as the game's door prompt.
                user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, wintypes.DWORD]
                user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
                user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            except Exception:
                pass  # older Windows: the pilot also ignores its own label text (see PromptReader)
            user32.SetWindowPos(
                hwnd,
                ctypes.c_void_p(-2),  # HWND_NOTOPMOST
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0010 | 0x0020,
            )
            self._position_blocks()
        except Exception as error:
            log_error(f"Не удалось включить click-through: {error}")

    def _position_blocks(self) -> None:
        """Blocks form one column that is placed as a whole; they can never overlap."""
        try:
            if not win32gui.IsWindow(self.window.hwnd):
                return
            left, top, width, height = get_client_bounds(self.window.hwnd)
            if width <= 0 or height <= 0:
                return
            if self._screen_size != (width, height):
                self._screen_size = (width, height)
                self.canvas.itemconfigure(self.canvas_items["screen"], text=format_screen(width, height))
            scale = float(self.settings["scale"])
            sized = [
                (window, round(base_width * scale), round(base_height * scale))
                for _name, window, base_width, base_height in self._block_specs()
                if self._hwnds.get(window)
            ]
            if not sized:
                return
            gap = round(10 * scale)
            stack_width = max(block_width for _w, block_width, _h in sized)
            stack_height = sum(block_height for _w, _bw, block_height in sized) + gap * (len(sized) - 1)
            x = left + round(max(0, width - stack_width) * self.settings["x_percent"] / 100)
            y = top + round(max(0, height - stack_height) * self.settings["y_percent"] / 100)
            for window, block_width, block_height in sized:
                if self._block_positions.get(window) != (x, y):
                    flags = 0x0010 | 0x0001 | 0x0004  # no activate, keep size and z-order
                    if ctypes.windll.user32.SetWindowPos(
                        self._hwnds[window], ctypes.c_void_p(0), x, y, block_width, block_height, flags,
                    ):
                        self._block_positions[window] = (x, y)
                y += block_height + gap
        except (tk.TclError, win32gui.error, OSError) as error:
            log_error(f"Не удалось обновить положение блоков: {error}")

    def _restore_game_focus(self) -> None:
        try:
            if win32gui.IsWindow(self.window.hwnd):
                user32 = ctypes.windll.user32
                user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                user32.SetForegroundWindow.restype = wintypes.BOOL
                user32.SetForegroundWindow(self.window.hwnd)
        except (win32gui.error, OSError) as error:
            log_error(f"Не удалось вернуть фокус игре: {error}")

    def _edit_mode_active(self) -> bool:
        now = time.monotonic()
        if now >= getattr(self, "_next_edit_read", 0.0):
            self._next_edit_read = now + 0.4
            try:
                data = json.loads(EDIT_PATH.read_text(encoding="utf-8"))
                self._edit_mode = time.time() - float(data.get("ts", 0)) < 3.5
            except (OSError, ValueError, TypeError):
                self._edit_mode = False
        return bool(getattr(self, "_edit_mode", False))

    def _game_or_settings_has_focus(self) -> bool:
        try:
            if not win32gui.IsWindow(self.window.hwnd) or win32gui.IsIconic(self.window.hwnd):
                return False
            if self._edit_mode_active():
                return True  # live setup from Hidden pool: keep the HUD visible even though the game is not focused
            foreground = win32gui.GetForegroundWindow()
            if not foreground:
                return False
            _thread_id, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
            return foreground_pid in (self.window.pid, os.getpid())
        except (win32gui.error, OSError):
            return False

    def _sync_game_visibility(self) -> None:
        should_show = not self._user_hidden and self._game_or_settings_has_focus()
        enabled = {window for _name, window, _w, _h in self._block_specs()}
        for window in self._block_windows():
            hidden = window.state() == "withdrawn"
            if should_show and window in enabled:
                if hidden:
                    window.deiconify()
                    self._make_click_through(window)
            elif not hidden:
                window.withdraw()

    def _apply_settings_preview(self) -> None:
        self._draw_overlay()
        self._stats_dirty = True
        self._draw_farm_overlay()
        self._draw_lab_overlay()
        self._apply_geometry()
        self._apply_opacity()
        for window in self._block_windows():
            window.update_idletasks()
        self._sync_game_visibility()
        self._position_blocks()

    def _save_settings(self) -> None:
        save_overlay_settings(self.settings)
        try:
            self._settings_mtime = SETTINGS_PATH.stat().st_mtime
        except OSError:
            pass

    def open_settings(self) -> None:
        if self._settings_window is not None:
            try:
                self._settings_window.deiconify()
                self._settings_window.lift()
                self._settings_window.focus_force()
                return
            except tk.TclError:
                self._settings_window = None

        dialog = tk.Toplevel(self.root)
        self._settings_window = dialog
        dialog.title("Hidden pool · настройки")
        dialog.configure(bg="#111719")
        dialog.resizable(False, False)
        dialog.geometry("460x470")

        tk.Label(
            dialog, text="Настройки оверлея", bg="#111719", fg="#edf1ed",
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).pack(fill="x", padx=22, pady=(20, 3))
        tk.Label(
            dialog, text="Блоки и элементы оверлея включаются в Hidden pool на вкладке «Оверлей».",
            bg="#111719", fg="#9da8a3", font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 12))

        variables = {
            "scale": tk.IntVar(value=round(self.settings["scale"] * 100)),
            "opacity": tk.IntVar(value=round(self.settings["opacity"] * 100)),
            "x_percent": tk.IntVar(value=int(self.settings["x_percent"])),
            "y_percent": tk.IntVar(value=int(self.settings["y_percent"])),
        }

        def update_setting(_value: str = "") -> None:
            self.settings.update({
                "scale": variables["scale"].get() / 100,
                "opacity": variables["opacity"].get() / 100,
                "x_percent": variables["x_percent"].get(),
                "y_percent": variables["y_percent"].get(),
            })
            self._apply_settings_preview()

        def add_slider(title: str, key: str, minimum: int, maximum: int, suffix: str) -> None:
            row = tk.Frame(dialog, bg="#111719")
            row.pack(fill="x", padx=22, pady=4)
            tk.Label(row, text=title, bg="#111719", fg="#dce4dc", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
            scale_widget = tk.Scale(
                row,
                from_=minimum,
                to=maximum,
                orient="horizontal",
                variable=variables[key],
                command=update_setting,
                showvalue=True,
                resolution=1,
                bg="#111719",
                fg="#c7f36a",
                troughcolor="#293331",
                activebackground="#c7f36a",
                highlightthickness=0,
                bd=0,
                font=("Segoe UI", 8),
                length=410,
            )
            scale_widget.pack(fill="x")
            tk.Label(row, text=suffix, bg="#111719", fg="#7f8b85", font=("Segoe UI", 8), anchor="e").pack(fill="x")

        add_slider("Размер", "scale", 70, 160, "Масштаб от 70% до 160%")
        add_slider("Прозрачность", "opacity", 35, 100, "100% — полностью непрозрачный")
        add_slider("Положение X", "x_percent", 0, 100, "0% — слева, 100% — справа")
        add_slider("Положение Y", "y_percent", 0, 100, "0% — сверху, 100% — снизу")

        buttons = tk.Frame(dialog, bg="#111719")
        buttons.pack(fill="x", padx=22, pady=(12, 18))

        def set_defaults() -> None:
            variables["scale"].set(round(DEFAULT_OVERLAY_SETTINGS["scale"] * 100))
            variables["opacity"].set(round(DEFAULT_OVERLAY_SETTINGS["opacity"] * 100))
            variables["x_percent"].set(DEFAULT_OVERLAY_SETTINGS["x_percent"])
            variables["y_percent"].set(DEFAULT_OVERLAY_SETTINGS["y_percent"])
            update_setting()
            self._save_settings()

        def close_settings() -> None:
            self._save_settings()
            self._settings_window = None
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            self.root.after(120, self._restore_game_focus)

        tk.Button(
            buttons, text="По умолчанию", command=set_defaults,
            bg="#20292a", fg="#dbe2db", activebackground="#2d3938",
            activeforeground="#ffffff", relief="flat", padx=10, pady=8,
            font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Button(
            buttons, text="Сохранить", command=self._save_settings,
            bg="#33412e", fg="#c7f36a", activebackground="#46583d",
            activeforeground="#e5ffb3", relief="flat", padx=12, pady=8,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            buttons, text="Готово", command=close_settings,
            bg="#c7f36a", fg="#111719", activebackground="#d6ff82",
            activeforeground="#111719", relief="flat", padx=14, pady=8,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close_settings)
        dialog.update_idletasks()
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        x = max(0, (screen_w - dialog.winfo_width()) // 2)
        y = max(0, (screen_h - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.focus_force()

    def open_hotkey_settings(self) -> None:
        if self._hotkeys_window is not None:
            try:
                self._hotkeys_window.deiconify()
                self._hotkeys_window.lift()
                self._hotkeys_window.focus_force()
                return
            except tk.TclError:
                self._hotkeys_window = None

        dialog = tk.Toplevel(self.root)
        self._hotkeys_window = dialog
        dialog.title("Hidden pool · сочетания клавиш")
        dialog.configure(bg="#111719")
        dialog.resizable(False, False)
        dialog.geometry("430x310")

        tk.Label(
            dialog, text="Сочетания клавиш", bg="#111719", fg="#edf1ed",
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).pack(fill="x", padx=22, pady=(20, 3))
        tk.Label(
            dialog,
            text="Выберите модификаторы и клавишу. Сочетания должны отличаться.",
            bg="#111719", fg="#9da8a3", font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 16))

        hotkey_labels = (
            ("toggle", "Показать / скрыть"),
            ("settings", "Настройки"),
            ("exit", "Закрыть оверлей"),
        )
        variables: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for name, label in hotkey_labels:
            row = tk.Frame(dialog, bg="#111719")
            row.pack(fill="x", padx=22, pady=6)
            tk.Label(
                row, text=label, bg="#111719", fg="#dce4dc",
                font=("Segoe UI", 9, "bold"), anchor="w", width=18,
            ).pack(side="left")
            binding = self.settings["hotkeys"][name]
            modifiers_var = tk.StringVar(value=binding["modifiers"])
            key_var = tk.StringVar(value=binding["key"])
            variables[name] = (modifiers_var, key_var)
            ttk.Combobox(
                row, textvariable=modifiers_var, values=HOTKEY_MODIFIER_CHOICES,
                state="readonly", width=18,
            ).pack(side="left", padx=(0, 8))
            ttk.Combobox(
                row, textvariable=key_var, values=tuple(HOTKEY_VIRTUAL_KEYS),
                state="readonly", width=9,
            ).pack(side="left")

        tk.Label(
            dialog,
            text="Если сочетание занято Windows или другой программой, оно не сработает.",
            bg="#111719", fg="#7f8b85", font=("Segoe UI", 8), anchor="w",
        ).pack(fill="x", padx=22, pady=(10, 4))

        buttons = tk.Frame(dialog, bg="#111719")
        buttons.pack(fill="x", padx=22, pady=(10, 18))

        def close_dialog() -> None:
            self._hotkeys_window = None
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            self.root.after(120, self._restore_game_focus)

        def apply_bindings() -> None:
            updated = {
                name: {"modifiers": modifiers.get(), "key": key.get()}
                for name, (modifiers, key) in variables.items()
            }
            signatures = [(value["modifiers"], value["key"]) for value in updated.values()]
            if len(signatures) != len(set(signatures)):
                messagebox.showerror(
                    "Сочетания клавиш",
                    "Для каждого действия нужно выбрать отдельное сочетание.",
                    parent=dialog,
                )
                return
            self.settings["hotkeys"] = updated
            self._save_settings()
            if hasattr(self, "hotkeys"):
                self.hotkeys.update_bindings(updated)
            close_dialog()

        tk.Button(
            buttons, text="Отмена", command=close_dialog,
            bg="#20292a", fg="#dbe2db", activebackground="#2d3938",
            activeforeground="#ffffff", relief="flat", padx=14, pady=8,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            buttons, text="Применить", command=apply_bindings,
            bg="#c7f36a", fg="#111719", activebackground="#d6ff82",
            activeforeground="#111719", relief="flat", padx=14, pady=8,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.update_idletasks()
        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        x = max(0, (screen_w - dialog.winfo_width()) // 2)
        y = max(0, (screen_h - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.lift()
        dialog.focus_force()

    def open_session_history(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Hidden pool · история сессий")
        dialog.configure(bg="#111719")
        dialog.geometry("470x440")
        dialog.minsize(400, 300)

        tk.Label(
            dialog, text="История сессий", bg="#111719", fg="#edf1ed",
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 2))
        tk.Label(
            dialog, text="Сначала последние запуски игры.", bg="#111719", fg="#9da8a3",
            font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 12))

        body = tk.Frame(dialog, bg="#111719")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        listing = tk.Canvas(body, bg="#111719", highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=listing.yview)
        listing.configure(yscrollcommand=scrollbar.set)
        listing.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        rows = tk.Frame(listing, bg="#111719")
        rows_window = listing.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda _event: listing.configure(scrollregion=listing.bbox("all")))
        listing.bind("<Configure>", lambda event: listing.itemconfigure(rows_window, width=event.width))

        records = self.store.recent_sessions()
        if not records:
            tk.Label(
                rows, text="Пока нет записанных сессий.", bg="#111719", fg="#9da8a3",
                font=("Segoe UI", 9), anchor="w",
            ).pack(fill="x", pady=10)
        for record in records:
            started_at = float(record.get("started_at", 0))
            started = time.strftime("%d.%m.%Y  %H:%M", time.localtime(started_at))
            session_key = self.store.session_key_for_record(record)
            duration = self._duration() if session_key == self.session_key else float(record.get("duration_seconds", 0))
            minutes = int(max(0, duration) // 60)
            card = tk.Frame(rows, bg="#192022", highlightthickness=1, highlightbackground="#293432")
            card.pack(fill="x", pady=4)
            header = tk.Frame(card, bg="#192022")
            header.pack(fill="x", padx=12, pady=(8, 3))
            tk.Label(
                header, text=started, bg="#192022", fg="#e2e9e1",
                font=("Segoe UI", 9, "bold"), anchor="w",
            ).pack(side="left")
            tk.Label(
                header, text=f"{minutes} мин", bg="#192022", fg="#c7f36a",
                font=("Segoe UI", 8, "bold"), anchor="e",
            ).pack(side="right")
            counts = format_personal_counts(record)
            if self.settings["blocks"]["stats"]["kills"]:
                counts += "   Убийства —"
            tk.Label(
                card, text=counts, bg="#192022", fg="#aeb9b1",
                font=("Segoe UI", 8), anchor="w",
            ).pack(fill="x", padx=12, pady=(0, 9))

        tk.Button(
            dialog, text="Готово", command=dialog.destroy,
            bg="#c7f36a", fg="#111719", activebackground="#d6ff82",
            activeforeground="#111719", relief="flat", padx=16, pady=7,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="e", padx=20, pady=(0, 16))
        dialog.lift()
        dialog.focus_force()

    def _check_game_session(self) -> None:
        now = time.monotonic()
        if self.game_ended_at is not None or now < self._next_game_check:
            return
        self._next_game_check = now + 1.0
        try:
            process = psutil.Process(self.window.pid)
            if process.is_running() and abs(process.create_time() - self.window.created_at) < 1.0:
                return
        except psutil.AccessDenied:
            return
        except psutil.NoSuchProcess:
            pass
        self._set_status("ИГРА ЗАКРЫТА")

    def _poll_combat_log(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if self._combat_log is None or self.game_ended_at is not None:
            return
        if not force and now < self._next_combat_check:
            return
        self._next_combat_check = now + 1.0
        try:
            events = self._combat_log.read_new_events()
        except OSError as error:
            message = f"Не удалось прочитать игровой журнал: {error}"
            if message != self._combat_log_error:
                log_error(message)
            self._combat_log_error = message
            return
        self._combat_log_error = None
        if events and self.settings["blocks"]["stats"]["enabled"]:
            self.session = self.store.record_combat_events(self.session_key, events)
            self._stats_dirty = True

    def _set_status(self, status: str) -> None:
        self.status_text = status
        if "ОШИБКА" in status:
            log_error(status)
        if status == "ИГРА ЗАКРЫТА" and self.game_ended_at is None:
            self.game_ended_at = time.time()
            duration = max(0, self.game_ended_at - self.window.created_at)
            self.store.update_duration(self.session_key, duration, ended_at=self.game_ended_at)

    def _duration(self) -> float:
        end = self.game_ended_at or time.time()
        return max(0, end - self.window.created_at)

    def _refresh_view(self) -> None:
        if not self.settings["blocks"]["stats"]["enabled"]:
            return

        def put(name: str, **options: object) -> None:
            item = self.canvas_items.get(name)
            if item is not None:
                self.canvas.itemconfigure(item, **options)

        duration = self._duration()
        for key, _label, _color in PERSONAL_COUNTERS:
            put(f"{key}_value", text=str(self.session.get(key, 0)))
        put("playtime", text=rr.clock(duration))
        if "lifetime_values" in self.canvas_items:
            totals = self.store.totals()
            put("session_count", text=f"{totals['sessions']} СЕССИЙ")
            put("lifetime_values", text=format_personal_counts(totals))

    def _tick(self) -> None:
        while True:
            try:
                action = self.hotkey_actions.get_nowait()
            except queue.Empty:
                break
            if action == "exit":
                self.close()
                return
            if action == "toggle":
                self.toggle_visibility()
            elif action == "settings":
                self.open_settings()
        if self._exit_requested_by_assistant():
            self.close()
            return
        self._reload_settings_if_changed()
        self._check_game_session()
        self._poll_combat_log()
        self._sync_game_visibility()
        self._position_blocks()
        self._refresh_farm()
        self._refresh_lab()
        self._refresh_vision()
        self._refresh_autocop()
        self._blink_session()
        self._write_heartbeat()
        if self.game_ended_at is None:
            duration = self._duration()
            minute = int(duration // 60)
            if minute != self.last_persisted_minute:
                self.store.update_duration(self.session_key, duration)
                self.last_persisted_minute = minute
                self._stats_dirty = True
        if self._stats_dirty:
            self._stats_dirty = False
            self._refresh_view()
        self.root.after(200, self._tick)

    def toggle_visibility(self) -> None:
        self._user_hidden = not self._user_hidden
        self._sync_game_visibility()

    def close(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self._poll_combat_log(force=True)
        try:
            HEARTBEAT_PATH.unlink()
        except OSError:
            pass
        self._save_settings()
        if hasattr(self, "hotkeys"):
            self.hotkeys.stop_event.set()
        if hasattr(self, "tray_icon"):
            threading.Thread(target=self.tray_icon.stop, name="HiddenPoolTrayStop", daemon=True).start()
        if self.game_ended_at is None:
            self.store.update_duration(self.session_key, self._duration())
        try:
            self.farm_window.destroy()
            self.lab_window.destroy()
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self._refresh_view()
        self.root.mainloop()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Этот оверлей рассчитан на Windows.")
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
    close_other_instances()
    for _attempt in range(10):
        if acquire_single_instance_mutex():
            break
        time.sleep(0.3)
    else:
        log_error("Не удалось занять мьютекс оверлея даже после закрытия старых копий.")
        return
    chooser = ProcessChooser()
    selected = chooser.run()
    if selected is None:
        return
    try:
        app = OverlayApp(selected)
        app.run()
    except Exception as error:
        log_error(f"Критическая ошибка запуска: {error}")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Hidden pool overlay", f"Не удалось запустить оверлей.\n\n{error}\n\nПодробности: overlay.log")


if __name__ == "__main__":
    main()
