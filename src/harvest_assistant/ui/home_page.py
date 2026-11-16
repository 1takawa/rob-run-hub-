"""The «Главная» tab: the project, its version and author, what is new, and the full patch notes in a pop-up.

The text comes from CHANGELOG.json in the project folder: it is written with every update and arrives with it
through «Проверить обновления»."""

from __future__ import annotations

import json
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Callable

from .. import updater
from .theme import COLORS, ScrollPage

LIME, INK, STRIP, EDGE, PANEL = "#c6fb33", "#141414", "#1b1b1a", "#2c2c2a", "#0f0f0e"
TEXT, SOFT, MUTED = "#e9e9e4", "#c9c9c2", "#8d8d86"
HEAD, BODY = "Bahnschrift SemiBold Condensed", "Bahnschrift Condensed"
KINDS = {   # tag text, tag colour, text on the tag, heading colour in the notes
    "added": ("ДОБАВЛЕНО", "#46c46a", "#0d1f12", "#46c46a"),
    "fixed": ("ИСПРАВЛЕНО", "#3da9f5", "#0c1a26", "#6cc4ff"),
    "removed": ("УБРАНО", "#ff7a70", "#2a0f0c", "#ff7a70"),
}
CHANGELOG = Path(__file__).resolve().parents[3] / "CHANGELOG.json"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
_logos: dict = {}


def logo_image(name: str, *, height: int, bg: str | None = None):
    """A picture of the logo (transparent PNG in assets) at this height, laid on `bg` for tkinter."""
    from PIL import Image, ImageTk

    key = (name, height, bg)
    if key not in _logos:
        image = Image.open(ASSETS / name).convert("RGBA")
        image = image.resize((max(1, round(image.width * height / image.height)), height), Image.Resampling.LANCZOS)
        if bg:
            base = Image.new("RGBA", image.size, bg)
            base.alpha_composite(image)
            image = base
        _logos[key] = ImageTk.PhotoImage(image)
    return _logos[key]


def load_changelog(path: Path = CHANGELOG) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def button(parent: tk.Misc, text: str, command, *, primary: bool = False) -> tk.Label:
    widget = tk.Label(parent, text=text.upper(), bg=LIME if primary else STRIP, fg=INK if primary else TEXT,
                      font=(HEAD, 12), padx=14, pady=5, cursor="hand2",
                      highlightthickness=1, highlightbackground=LIME if primary else "#3a3a37")
    widget.bind("<ButtonPress-1>", lambda _e: command())
    return widget


def tag(parent: tk.Misc, kind: str, bg: str) -> tk.Label:
    text, color, ink, _heading = KINDS[kind]
    return tk.Label(parent, text=text, bg=color, fg=ink, font=(HEAD, 9), width=11)


class PatchNotes(tk.Frame):
    """The full notes: a card inside the app window over the page."""

    def __init__(self, parent: tk.Misc, changelog: dict) -> None:
        host = parent.winfo_toplevel()
        super().__init__(host, bg=STRIP, highlightthickness=2, highlightbackground=LIME)
        head = tk.Frame(self, bg=STRIP)
        head.pack(fill="x")
        releases = changelog.get("releases") or []
        latest = releases[0] if releases else {}
        tk.Label(head, text=f"ПАТЧНОУТ {latest.get('date', '')[:5]}", bg=LIME, fg=INK, font=(HEAD, 16), padx=14, pady=4).pack(side="left")
        close = tk.Label(head, text="✕", bg=STRIP, fg=MUTED, font=(BODY, 15), padx=12, cursor="hand2")
        close.pack(side="right")
        close.bind("<ButtonPress-1>", lambda _e: self.destroy())
        text = tk.Text(self, bg=STRIP, fg=SOFT, relief="flat", wrap="word", width=70, height=24, padx=18, pady=10,
                       font=(BODY, 12), cursor="arrow", highlightthickness=0, spacing1=2, spacing3=2)
        text.pack(fill="both", expand=True)
        text.tag_configure("title", font=(HEAD, 15), foreground=TEXT, spacing1=10, spacing3=4)
        text.tag_configure("date", font=(BODY, 10), foreground=MUTED)
        for kind, (_word, _color, _ink, heading) in KINDS.items():
            text.tag_configure(kind, font=(HEAD, 11), foreground=heading, spacing1=8)
        text.tag_configure("item", lmargin1=6, lmargin2=20)
        names = {"added": "ДОБАВЛЕНО", "fixed": "ИСПРАВЛЕНО", "removed": "УБРАНО"}
        for index, release in enumerate(releases):
            text.insert("end", release.get("title", "") + "\n", "title")
            text.insert("end", release.get("date", "") + "\n", "date")
            for kind in ("added", "fixed", "removed"):
                items = release.get(kind) or []
                if not items:
                    continue
                text.insert("end", names[kind] + "\n", kind)
                for item in items:
                    text.insert("end", f"•  {item}\n", "item")
            if index < len(releases) - 1:
                text.insert("end", "\n")
        text.configure(state="disabled")
        text.bind("<Escape>", lambda _e: self.destroy())
        self.place(relx=0.5, rely=0.5, anchor="center")
        self.lift()
        self.grab_set()
        text.focus_set()


class HomePage(tk.Frame):
    def __init__(self, parent: tk.Misc, state: Callable[[], dict]) -> None:
        """`state()` gives {"hotkeys": [(key, what)], "season": dict|None, "wipe_left": seconds, "runs": int}."""
        super().__init__(parent, bg=COLORS["bg"])
        self.state = state
        self.changelog = load_changelog()
        scroll = ScrollPage(self, max_width=980)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        tk.Frame(body, bg=COLORS["bg"], height=28).pack()
        self._hero(body)
        lower = tk.Frame(body, bg=COLORS["bg"])
        lower.pack(fill="x", pady=(10, 0))
        lower.grid_columnconfigure(0, weight=8, uniform="home")
        lower.grid_columnconfigure(1, weight=5, uniform="home")
        self._news(lower).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        side = tk.Frame(lower, bg=COLORS["bg"])
        side.grid(row=0, column=1, sticky="nsew")
        self.quick = self._card(side, "быстрый старт")
        self.quick.pack(fill="x")
        self.status = self._card(side, "состояние")
        self.status.pack(fill="x", pady=(10, 0))
        self.refresh()

    # ---- parts ----------------------------------------------------------------------------------------------------

    def _hero(self, body: tk.Misc) -> None:
        box = tk.Frame(body, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        box.pack(fill="x")
        canvas = tk.Canvas(box, bg=STRIP, highlightthickness=0, height=140)
        self._emblem = logo_image("logo_emblem.png", height=112, bg=STRIP)
        self._wordmark = logo_image("logo_wordmark.png", height=80, bg=STRIP)
        canvas.pack(fill="x")
        edition = self.changelog.get("edition", "ROB RUN EDITION")
        version = updater.version()

        def draw(_event=None) -> None:
            canvas.delete("all")
            width = max(400, canvas.winfo_width())
            for offset in range(-220, 320, 22):   # honeycomb lines, as on the state card of the game
                canvas.create_line(width - 260 + offset, 0, width - 260 + offset + 90, 160, fill="#252a1a")
                canvas.create_line(width - 260 + offset + 90, 0, width - 260 + offset, 160, fill="#252a1a")
            canvas.create_image(24, 70, image=self._emblem, anchor="w")                              # the logo
            canvas.create_image(24 + self._emblem.width() + 14, 58, image=self._wordmark, anchor="w")
            left = 24 + self._emblem.width() + 26
            label = canvas.create_text(left, 118, text=edition, anchor="w", fill=INK, font=(HEAD, 10))
            x0, y0, x1, y1 = canvas.bbox(label)
            canvas.tag_raise(label, canvas.create_rectangle(x0 - 8, y0 - 2, x1 + 8, y1 + 2, fill=LIME, outline=""))
            if version:
                canvas.create_text(x1 + 18, 118, text=f"версия {version}", anchor="w", fill=MUTED, font=(BODY, 10))
            canvas.create_text(width - 22, 34, text="АВТОР", anchor="e", fill=MUTED, font=(BODY, 9))

        canvas.bind("<Configure>", draw)
        nick = str((self.changelog.get("author") or {}).get("telegram") or "").strip().lstrip("@")
        author = tk.Frame(box, bg=STRIP)
        author.place(in_=canvas, relx=1.0, x=-22, y=46, anchor="ne")
        if nick:
            link = button(author, f"✈  @{nick}", lambda: webbrowser.open(f"https://t.me/{nick}"))
            link.configure(fg="#6cc4ff")
            link.pack()
        else:
            tk.Label(author, text="Telegram скоро", bg=STRIP, fg=MUTED, font=(BODY, 11)).pack()

    def _news(self, parent: tk.Misc) -> tk.Widget:
        box = tk.Frame(parent, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        releases = self.changelog.get("releases") or []
        latest = releases[0] if releases else {}
        head = tk.Frame(inner, bg=STRIP)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text="ЧТО НОВОГО", bg=STRIP, fg=TEXT, font=(HEAD, 18)).pack(side="left")
        tk.Label(head, text=latest.get("date", ""), bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(side="left", padx=(10, 0), pady=(6, 0))
        for kind, line in (latest.get("short") or [])[:6]:
            if kind not in KINDS:
                continue
            row = tk.Frame(inner, bg=STRIP)
            row.pack(fill="x", pady=1)
            tag(row, kind, STRIP).pack(side="left")
            tk.Label(row, text=line, bg=STRIP, fg=TEXT, font=(BODY, 12), anchor="w").pack(side="left", padx=(10, 0), pady=4)
            tk.Frame(inner, bg="#232321", height=1).pack(fill="x")
        if not latest:
            tk.Label(inner, text="Список изменений не найден.", bg=STRIP, fg=MUTED, font=(BODY, 11)).pack(anchor="w")
        button(inner, "Читать полностью", lambda: PatchNotes(self, self.changelog), primary=True).pack(anchor="w", pady=(12, 0))
        return box

    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        box = tk.Frame(parent, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        tk.Label(box, text=title.upper(), bg=STRIP, fg=MUTED, font=(BODY, 9)).pack(anchor="w", padx=14, pady=(10, 4))
        box.lines = tk.Frame(box, bg=STRIP)
        box.lines.pack(fill="x", padx=14, pady=(0, 10))
        return box

    @staticmethod
    def _fill(card: tk.Frame, lines: list[tuple[str, str]]) -> None:
        for child in card.lines.winfo_children():
            child.destroy()
        for key, text in lines:
            row = tk.Frame(card.lines, bg=STRIP)
            row.pack(fill="x", pady=2)
            if key:
                tk.Label(row, text=key, bg=STRIP, fg=LIME, font=(HEAD, 12), width=4, anchor="w").pack(side="left")
            tk.Label(row, text=text, bg=STRIP, fg=SOFT, font=(BODY, 12), anchor="w").pack(side="left")

    def refresh(self) -> None:
        try:
            state = self.state()
        except Exception:
            state = {}
        self._fill(self.quick, [(key.upper(), what) for key, what in state.get("hotkeys", [])])
        season = state.get("season")
        lines = []
        if season:
            left = int(state.get("wipe_left", 0)) // 3600
            when = f"вайп через {left // 24} д {left % 24} ч" if left >= 24 else f"вайп через {left} ч"
            lines.append(("", f"штат {season['code']} · {when}"))
        else:
            lines.append(("", "штат не выбран — «Статистика»"))
        lines.append(("", f"заходов записано: {state.get('runs', 0)}"))
        lines.append(("", time.strftime("обновлено %H:%M")))
        self._fill(self.status, lines)
