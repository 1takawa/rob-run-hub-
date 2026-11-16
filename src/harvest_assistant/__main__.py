"""Launch the Harvest Assistant desktop application."""

from __future__ import annotations

import atexit
import ctypes
import os
import subprocess
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

from .app import HarvestAssistantApp
from .models import Profile
from .persistence import load_profile, save_profile
from .runner import RouteRunner


def _python_process_alive(pid: int) -> bool:
    """True if `pid` is a running python process (guards against a recycled pid)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000 | 0x0400, False, pid)  # QUERY_LIMITED_INFORMATION | QUERY_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        return os.path.basename(buffer.value).lower() in ("python.exe", "pythonw.exe")
    finally:
        kernel32.CloseHandle(handle)


def replace_previous_instance(profile_dir: Path) -> None:
    """Only one copy may run: two copies overwrite each other's profile (timers, upgrades...).

    The previous copy is closed, then this one records its own pid.
    """
    pid_file = profile_dir / "hub.pid"
    try:
        previous = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        previous = 0
    if previous and previous != os.getpid() and _python_process_alive(previous):
        subprocess.run(["taskkill", "/PID", str(previous), "/T", "/F"], capture_output=True, creationflags=0x08000000)
        deadline = time.time() + 4
        while time.time() < deadline and _python_process_alive(previous):
            time.sleep(0.1)
    profile_dir.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    def forget() -> None:
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass

    atexit.register(forget)


def reset_statistics_once(profiles: Path) -> None:
    """The season statistics start clean: the old farm statistics, the overlay's knocks/deaths/arrests and the logs are
    moved into profiles/archive_<date>/ once (nothing is deleted). The overlay is told to forget its counters too."""
    import shutil

    marker = profiles / ".stats_reset_v1"
    if marker.exists():
        return
    archive = profiles / time.strftime("archive_%Y%m%d_%H%M%S")
    overlay = profiles.parent / "overlay"
    moved = [(profiles / "stats.json", archive / "stats.json"), (overlay / "stats.json", archive / "overlay_stats.json"),
             (profiles / "logs", archive / "logs")]
    for source, target in moved:
        if source.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
            except OSError:
                pass
    try:
        if overlay.is_dir():
            (overlay / "stats_reset.flag").write_text(str(time.time()), encoding="utf-8")
        profiles.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(archive.name), encoding="utf-8")
    except OSError:
        pass


def main() -> None:
    project_dir = Path(__file__).resolve().parents[2]
    profile_path = project_dir / "profiles" / "default.json"
    replace_previous_instance(profile_path.parent)
    reset_statistics_once(profile_path.parent)
    root = tk.Tk()
    profile = Profile()
    if profile_path.exists():
        try:
            profile = load_profile(profile_path)
        except (ValueError, OSError, TypeError, KeyError):
            # a profile file damaged (e.g. zeroed by a crash while it was written): keep it aside, start from defaults
            damaged = profile_path.with_name(f"default.damaged_{int(time.time())}.json")
            try:
                profile_path.replace(damaged)
            except OSError:
                pass
        save_profile(profile, profile_path)
    HarvestAssistantApp(root, profile, profile_path, runner_factory=RouteRunner)
    root.mainloop()


if __name__ == "__main__":
    main()
