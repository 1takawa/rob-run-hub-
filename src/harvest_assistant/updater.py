"""Updates from GitHub: is there a newer version of the hub, and getting it.

The hub folder is a git clone of https://github.com/1takawa/rob-run-hub-.git (branch main). A check fetches the branch
and counts the commits this copy does not have; an update fast-forwards to them. Personal data (profiles/, the overlay's
stats and settings) is not in git, so an update never touches it. run.bat installs new packages on the next start.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
REMOTE = "origin"
BRANCH = "main"
NO_WINDOW = 0x08000000


@dataclass
class UpdateInfo:
    state: str                      # latest, available, local_changes, error
    message: str
    local: str = ""                 # short hash of this copy
    behind: int = 0                 # commits on GitHub this copy does not have
    changes: list[str] = field(default_factory=list)   # their titles, newest first


def _git(*args: str, folder: Path = PROJECT_DIR, timeout: float = 60.0) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # never wait for a password prompt nobody sees
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, creationflags=NO_WINDOW if os.name == "nt" else 0)


def version(folder: Path = PROJECT_DIR) -> str:
    """Short hash and date of this copy, e.g. «461f149 · 16.11.2026»; empty when it is not a git copy."""
    try:
        result = _git("log", "-1", "--format=%h · %cd", "--date=format:%d.%m.%Y", folder=folder, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check(folder: Path = PROJECT_DIR) -> UpdateInfo:
    try:
        if _git("rev-parse", "--is-inside-work-tree", folder=folder, timeout=10).returncode != 0:
            return UpdateInfo("error", "Эта папка не связана с GitHub: обновления недоступны.")
        fetched = _git("fetch", "--quiet", REMOTE, BRANCH, folder=folder)
        if fetched.returncode != 0:
            return UpdateInfo("error", "Не удалось связаться с GitHub. Проверь интернет и попробуй ещё раз.")
        local = _git("rev-parse", "--short", "HEAD", folder=folder).stdout.strip()
        behind = int(_git("rev-list", "--count", f"HEAD..{REMOTE}/{BRANCH}", folder=folder).stdout.strip() or 0)
        if behind == 0:
            return UpdateInfo("latest", "Актуальная версия.", local)
        titles = _git("log", "--format=%s", "-n", "8", f"HEAD..{REMOTE}/{BRANCH}", folder=folder).stdout.splitlines()
        changed = _git("status", "--porcelain", "--untracked-files=no", folder=folder).stdout.strip()
        if changed:
            return UpdateInfo("local_changes", "Доступно обновление, но файлы программы изменены вручную: "
                              "сначала верни их или обнови вручную.", local, behind, titles)
        return UpdateInfo("available", f"Доступно обновление: {behind} {_plural(behind)}.", local, behind, titles)
    except FileNotFoundError:
        return UpdateInfo("error", "Git не установлен: скачай его с git-scm.com, чтобы получать обновления.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return UpdateInfo("error", f"Проверка обновлений не удалась: {exc}")


def apply(folder: Path = PROJECT_DIR) -> tuple[bool, str]:
    """Bring this copy up to GitHub. Only a fast-forward: nothing local is ever overwritten."""
    try:
        result = _git("pull", "--ff-only", "--quiet", REMOTE, BRANCH, folder=folder, timeout=180)
    except FileNotFoundError:
        return False, "Git не установлен."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Обновление не удалось: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, "Обновление не удалось" + (f": {detail[-1]}" if detail else ".")
    return True, f"Обновлено до {version(folder) or 'новой версии'}. Перезапусти приложение."


def restart(folder: Path = PROJECT_DIR) -> None:
    """Start the hub again through run.bat (it installs new packages); the new copy closes this one."""
    subprocess.Popen(["cmd", "/c", "start", "", str(folder / "run.bat")], cwd=folder,
                     creationflags=NO_WINDOW if os.name == "nt" else 0)


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "изменение"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "изменения"
    return "изменений"
