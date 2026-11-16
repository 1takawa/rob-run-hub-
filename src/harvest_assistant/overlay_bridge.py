"""Link between Harvest Assistant and the in-game overlay (the `overlay/` folder).

The two programs run in separate Python environments, so they talk through small
JSON files in the overlay folder:

* ``farm_feed.json``       – assistant -> overlay: next harvest timer + package counters
* ``overlay_settings.json`` – assistant <-> overlay: size, opacity, positions, hotkeys
* ``overlay_heartbeat.json`` – overlay -> assistant: pid + last-seen time
* ``overlay_command.json``  – assistant -> overlay: {"action": "exit"}
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .loot import split_key
from .models import Profile, remaining_seconds

OVERLAY_DIR = Path(__file__).resolve().parents[2] / "overlay"
FEED_PATH = OVERLAY_DIR / "farm_feed.json"
SETTINGS_PATH = OVERLAY_DIR / "overlay_settings.json"
HEARTBEAT_PATH = OVERLAY_DIR / "overlay_heartbeat.json"
COMMAND_PATH = OVERLAY_DIR / "overlay_command.json"
EDIT_PATH = OVERLAY_DIR / "overlay_edit.json"

DEFAULT_SETTINGS = {
    "scale": 1.0,
    "opacity": 0.9,
    "x_percent": 2,
    "y_percent": 4,
    "blocks": {
        "stats": {"enabled": True, "kills": True, "knocks": True, "deaths": True, "arrests": True,
                  "playtime": True, "lifetime": True, "history": True},
        "plantation": {"enabled": True, "timer": True, "yellow": True, "purple": True, "green": True},
        "laboratory": {"enabled": False},
    },
    "hotkeys": {
        "toggle": {"modifiers": "Ctrl+Alt", "key": "F12"},
        "exit": {"modifiers": "Ctrl+Alt", "key": "F11"},
        "settings": {"modifiers": "Ctrl+Alt", "key": "F10"},
    },
}
HOTKEY_MODIFIER_CHOICES = ("Ctrl+Alt", "Ctrl+Shift", "Alt+Shift", "Ctrl+Alt+Shift")
HOTKEY_KEY_CHOICES = tuple(f"F{number}" for number in range(1, 13)) + ("Insert", "Home", "End", "Pause")

# Colour names produced by the loot reader -> the three counters on the overlay.
COUNTER_GROUPS = {
    "жёлтый": "yellow", "оранжевый": "yellow",
    "фиолетовый": "purple", "розовый": "purple",
    "зелёный": "green",
}


# What one package is worth: green 4 000, purple 6 000, yellow (legendary) 8 000.
PACKAGE_PRICES = {"green": 4000, "purple": 6000, "yellow": 8000}


def format_money(value: int) -> str:
    """$100 242 - groups of three digits separated by spaces."""
    return "$" + f"{int(value):,}".replace(",", " ")


def loot_counts(profile: Profile) -> dict[str, int]:
    counts = {"yellow": 0, "purple": 0, "green": 0}
    for key, amount in profile.loot_totals.items():
        group = COUNTER_GROUPS.get(split_key(key)[0])
        if group:
            counts[group] += int(amount)
    return counts


def next_harvest(profile: Profile, now: float) -> tuple[str | None, int | None]:
    """Name and remaining seconds of the table that becomes ready LAST: a cycle needs every table."""
    best: tuple[str, int] | None = None
    for table in profile.tables:
        remaining = remaining_seconds(table, profile, now)
        if remaining is not None and (best is None or remaining > best[1]):
            best = (table.name, remaining)
    return (best[0], best[1]) if best else (None, None)


def _atomic_write(path: Path, data: dict) -> None:
    try:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # overlay folder missing or file briefly locked: the next tick retries


def write_feed(profile: Profile, running: bool, state: str, counts: dict[str, int] | None = None) -> None:
    if not OVERLAY_DIR.is_dir():
        return
    now = time.time()
    name, remaining = next_harvest(profile, now)
    lab_remaining = remaining_seconds(profile.laboratory.timer, profile, now)
    wall_clock = profile.clock_mode == "wall_clock"
    due_at = now + remaining if (remaining is not None and wall_clock and not profile.manual_timer_paused) else None
    lab_due_at = now + lab_remaining if (
        lab_remaining is not None and wall_clock and not profile.manual_timer_paused
    ) else None
    _atomic_write(
        FEED_PATH,
        {
            "version": 1,
            "updated_at": now,
            "running": running,
            "state": state,
            "paused": bool(profile.manual_timer_paused),
            "next_table": name,
            "next_due_at": due_at,
            "remaining_seconds": remaining,
            "lab_remaining_seconds": lab_remaining,
            "lab_due_at": lab_due_at,
            "counts": counts if counts is not None else loot_counts(profile),
        },
    )


def read_settings() -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(saved, dict):
        return settings
    for key in ("scale", "opacity"):
        if isinstance(saved.get(key), (int, float)):
            settings[key] = float(saved[key])
    for key in ("x_percent", "y_percent"):
        if isinstance(saved.get(key), (int, float)):
            settings[key] = int(saved[key])
    saved_blocks = saved.get("blocks")
    if isinstance(saved_blocks, dict):
        for block, defaults in DEFAULT_SETTINGS["blocks"].items():
            values = saved_blocks.get(block)
            if isinstance(values, dict):
                for key in defaults:
                    if isinstance(values.get(key), bool):
                        settings["blocks"][block][key] = values[key]
    hotkeys = saved.get("hotkeys")
    if isinstance(hotkeys, dict):
        for name, binding in hotkeys.items():
            if name in settings["hotkeys"] and isinstance(binding, dict):
                settings["hotkeys"][name] = {
                    "modifiers": binding.get("modifiers", settings["hotkeys"][name]["modifiers"]),
                    "key": binding.get("key", settings["hotkeys"][name]["key"]),
                }
    return settings


def write_settings(settings: dict) -> None:
    _atomic_write(SETTINGS_PATH, settings)


def running_overlay_pid() -> int | None:
    """Pid of the live overlay (it refreshes its heartbeat every ~2 s)."""
    try:
        data = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        if time.time() - float(data["ts"]) < 8:
            return int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def launch_overlay() -> str | None:
    """Start the overlay; returns an error message or None."""
    if running_overlay_pid() is not None:
        return None
    python = OVERLAY_DIR / ".venv" / "Scripts" / "pythonw.exe"
    hub_python = OVERLAY_DIR.parent / ".venv" / "Scripts" / "pythonw.exe"
    if not python.is_file() and hub_python.is_file() and (OVERLAY_DIR.parent / ".venv" / "requirements.sha").is_file():
        python = hub_python   # start.bat put the overlay's packages into the hub's environment
    try:
        if python.is_file():
            subprocess.Popen(
                [str(python), "main.py"],
                cwd=str(OVERLAY_DIR),
                creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                close_fds=True,
            )
        else:
            launcher = OVERLAY_DIR / "start.vbs"
            if not launcher.is_file():
                return "Папка overlay не найдена."
            os.startfile(str(launcher))  # first run: installs dependencies, then starts
    except OSError as exc:
        return str(exc)
    return None


def stop_overlay() -> bool:
    """Ask the overlay to quit; True if it was running."""
    if running_overlay_pid() is None:
        return False
    _atomic_write(COMMAND_PATH, {"action": "exit", "ts": time.time()})
    return True


def set_edit_mode(active: bool) -> None:
    """While True (refreshed every second) the overlay stays visible in the game for live setup."""
    if not OVERLAY_DIR.is_dir():
        return
    if active:
        _atomic_write(EDIT_PATH, {"ts": time.time()})
    else:
        try:
            EDIT_PATH.unlink()
        except OSError:
            pass


VISION_PATH = OVERLAY_DIR / "vision_feed.json"


def write_vision(payload: dict) -> None:
    """What the autopilot sees right now; the overlay draws it over the game (see overlay/main.py)."""
    if OVERLAY_DIR.is_dir():
        _atomic_write(VISION_PATH, payload)


AUTOCOP_PATH = OVERLAY_DIR / "autocop_feed.json"


def write_autocop(payload: dict) -> None:
    """The police-service helper: on/off, attempts, time in the cycle (the overlay draws its panel from it)."""
    if OVERLAY_DIR.is_dir():
        _atomic_write(AUTOCOP_PATH, payload)


def clear_autocop() -> None:
    write_autocop({"active": False, "ts": time.time()})
