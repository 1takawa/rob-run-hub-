"""The «Проверить обновления» box at the bottom of the left menu."""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

from .. import updater
from .theme import COLORS, FONT, PillButton


class UpdateBox(tk.Frame):
    def __init__(self, parent: tk.Misc, post: Callable[[Callable[[], None]], None], quit_app: Callable[[], None]) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self._post = post
        self._quit = quit_app
        self._busy = False
        bg = self.cget("bg")
        self.check_button = PillButton(self, "Проверить обновления", self.check, style="secondary", padx=14, height=32, size=9)
        self.check_button.pack(anchor="w")
        self.message = tk.Label(self, text="", bg=bg, fg=COLORS["muted"], font=(FONT, 8), anchor="w", justify="left",
                                wraplength=165)
        self.message.pack(fill="x", pady=(6, 0))
        self.changes = tk.Label(self, text="", bg=bg, fg=COLORS["faint"], font=(FONT, 8), anchor="w", justify="left",
                                wraplength=165)
        self.action = PillButton(self, "Загрузить", self.download, style="primary", padx=16, height=30, size=9)
        self.version = tk.Label(self, text="", bg=bg, fg=COLORS["faint"], font=(FONT, 7), anchor="w")
        self.version.pack(fill="x", pady=(6, 0))
        # the version this window runs: files on disk may be newer (downloaded, not restarted yet)
        self._running = updater.head()
        text = updater.version()
        self.version.configure(text=f"версия {text}" if text else "")

    def _say(self, text: str, color: str = "muted") -> None:
        self.message.configure(text=text, fg=COLORS.get(color, color))

    def check(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.check_button.configure(state="disabled")
        self.action.pack_forget()
        self.changes.pack_forget()
        self._say("Проверяю…")

        def work() -> None:
            info = updater.check()
            self._post(lambda: self._checked(info))
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _checked(self, info: updater.UpdateInfo) -> None:
        self._busy = False
        self.check_button.configure(state="normal")
        color = {"latest": "green", "available": "amber", "local_changes": "amber", "error": "red"}.get(info.state, "muted")
        if info.state == "latest" and self._running and info.local and info.local != self._running:
            # the newest version is on disk already, but this window still runs the old one
            self._say(f"Новая версия {info.local} уже скачана. Перезапусти приложение.", "amber")
            self.action.configure(text="Перезапустить", command=self.restart, state="normal")
            self.action.pack(anchor="w", pady=(8, 0), before=self.version)
            return
        self._say(info.message, color)
        if info.changes:
            shown = info.changes[:4]
            more = len(info.changes) - len(shown)
            self.changes.configure(text="\n".join(f"• {title[:60]}" for title in shown) + (f"\n и ещё {more}" if more > 0 else ""))
            self.changes.pack(fill="x", pady=(4, 0), before=self.version)
        if info.state == "available":
            self.action.configure(text="Загрузить", command=self.download, state="normal")
            self.action.pack(anchor="w", pady=(8, 0), before=self.version)

    def download(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.action.configure(state="disabled")
        self.check_button.configure(state="disabled")
        self._say("Загружаю обновление…")

        def work() -> None:
            ok, message = updater.apply()
            self._post(lambda: self._downloaded(ok, message))
        threading.Thread(target=work, name="update-apply", daemon=True).start()

    def _downloaded(self, ok: bool, message: str) -> None:
        self._busy = False
        self.check_button.configure(state="normal")
        self._say(message, "green" if ok else "red")
        self.changes.pack_forget()
        if ok:
            self.action.configure(text="Перезапустить", command=self.restart, state="normal")
            self.action.pack(anchor="w", pady=(8, 0), before=self.version)
        else:
            self.action.pack_forget()

    def restart(self) -> None:
        updater.restart()
        self._quit()
