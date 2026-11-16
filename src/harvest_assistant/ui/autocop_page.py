"""The «Автокоп» tab: the five loadouts as in the game, the launch panel and how it works.

Drawn in the look of ROB RUN (as the overlay panel): a slanted lime tab for the title, black strips, keys in light
squares, each class as its loadout card from the game (the picture shows the weapons, the armour and the items of the
set), its cost in tokens as last read on the loadout screen, and the chosen one framed in lime.
"""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk

from ..autocop import SERVICE_CLASSES
from .theme import COLORS, ScrollPage

ASSETS = Path(__file__).resolve().parents[1] / "assets"
CLASS_IMAGES = {
    "Патрульный": "autocop_patrol.png",
    "Сержант I": "autocop_sergeant_1.png",
    "Сержант II": "autocop_sergeant_2.png",
    "Лейтенант I": "autocop_lieutenant_1.png",
    "Капитан": "autocop_captain.png",
}
RANKS = {"Патрульный": "#3f9b4b", "Сержант I": "#3f9b4b", "Сержант II": "#3f9b4b", "Лейтенант I": "#8e4fc4", "Капитан": "#c9a227"}

LIME = "#c6fb33"
INK = "#141414"
STRIP = "#1b1b1a"
PANEL = "#151514"
EDGE = "#2c2c2a"
TEXT = "#e9e9e4"
MUTED = "#8d8d86"
DIM = "#5f5f59"
KEY = "#d8d8d2"
RED = "#ff7a70"
BLUE = "#6cc4ff"
HEAD = "Bahnschrift SemiBold Condensed"
BODY = "Bahnschrift Condensed"
GAP = 8

_image_cache: dict[tuple[str, int], ImageTk.PhotoImage] = {}


def class_image(name: str, width: int) -> ImageTk.PhotoImage:
    """The loadout card of the class (from the game), `width` pixels wide."""
    key = (name, width)
    cached = _image_cache.get(key)
    if cached is not None:
        return cached
    image = Image.open(ASSETS / CLASS_IMAGES[name]).convert("RGBA")
    height = round(width * 376 / 206)   # the cards differ by a few pixels: one height keeps the row level
    photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS))
    _image_cache[key] = photo
    return photo


def token_text(token: str | None) -> tuple[str, str]:
    """What a card says about its cost, and in which colour."""
    if not token:
        return "цена — у стойки", DIM
    if token == "бесплатно":
        return "Бесплатно", TEXT
    if token == "0" or token.startswith("0/"):
        return "жетонов нет", RED
    return f"жетон {token}", TEXT


def clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours} ч {minutes:02d} м {secs:02d} с" if hours else f"{minutes} м {secs:02d} с"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class GameButton(tk.Canvas):
    """A flat game-style button: lime (primary), dark (secondary). Feedback on press; `configure(state=, text=)`."""

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None] | None, *, primary: bool = True,
                 height: int = 44, size: int = 15, width: int | None = None) -> None:
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, height=height, cursor="hand2")
        self._text, self._command, self._primary = text, command, primary
        self._height, self._size, self._fixed = height, size, width
        self._enabled, self._state = True, "idle"
        self.bind("<Enter>", lambda _e: self._set("hover"))
        self.bind("<Leave>", lambda _e: self._set("idle"))
        self.bind("<ButtonPress-1>", lambda _e: self._set("press"))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda _e: self._render())
        self._render()

    def _render(self) -> None:
        font = (HEAD, self._size)
        width = self._fixed or tkfont.Font(font=font).measure(self._text.upper()) + 36
        if self._fixed is None:
            super().configure(width=width)
        width = max(width, self.winfo_width() if self._fixed is None and self.winfo_width() > 1 else width)
        self.delete("all")
        if self._primary:
            fill = {"idle": LIME, "hover": "#d6ff6a", "press": "#a8d82a"}[self._state]
            color = INK
            outline = fill
        else:
            fill = {"idle": STRIP, "hover": "#242422", "press": "#111110"}[self._state]
            color = TEXT
            outline = "#3a3a37"
        if not self._enabled:
            fill, color, outline = "#1f1f1d", DIM, "#2a2a28"
        self.create_rectangle(0, 0, width - 1, self._height - 1, fill=fill, outline=outline)
        self.create_text(width / 2, self._height / 2, text=self._text.upper(), fill=color, font=font)
        super().configure(cursor="hand2" if self._enabled else "arrow")

    def _set(self, state: str) -> None:
        if self._enabled and state != self._state:
            self._state = state
            self._render()

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set("hover" if inside else "idle")
        if inside and self._enabled and self._command:
            self._command()

    def configure(self, cnf: dict | None = None, **options: object) -> object:  # type: ignore[override]
        merged = {**(cnf or {}), **options}
        redraw = False
        if "text" in merged:
            self._text, redraw = str(merged.pop("text")), True
        if "state" in merged:
            self._enabled, self._state, redraw = merged.pop("state") != "disabled", "idle", True
        result = super().configure(**merged) if merged else None
        if redraw:
            self._render()
        return result

    config = configure

    def cget(self, key: str):  # type: ignore[override]
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)


class LoadoutRow(tk.Canvas):
    """The five loadout cards side by side, drawn on one canvas; a click picks one."""

    def __init__(self, parent: tk.Misc, pick: Callable[[str], None]) -> None:
        super().__init__(parent, bg=COLORS["bg"], highlightthickness=0, bd=0, height=360)
        self._pick = pick
        self.selected = SERVICE_CLASSES[0]
        self.tokens: dict[str, str] = {}
        self.locked = False
        self._hover: str | None = None
        self._boxes: dict[str, tuple[float, float, float, float]] = {}
        self.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<ButtonPress-1>", self._press)

    def _at(self, x: float, y: float) -> str | None:
        for name, (x0, y0, x1, y1) in self._boxes.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _motion(self, event: tk.Event) -> None:
        self._set_hover(self._at(event.x, event.y))

    def _set_hover(self, name: str | None) -> None:
        if name != self._hover:
            self._hover = name
            self.configure(cursor="hand2" if name and not self.locked else "arrow")
            self.draw()

    def _press(self, event: tk.Event) -> None:
        name = self._at(event.x, event.y)
        if name and not self.locked:
            self._pick(name)

    def draw(self) -> None:
        width = max(300, self.winfo_width())
        card_w = (width - GAP * 4) / 5
        picture_w = int(card_w - 10)
        self.delete("all")
        self._boxes.clear()
        bottom = 0
        for index, name in enumerate(SERVICE_CLASSES):
            x0 = index * (card_w + GAP)
            x1 = x0 + card_w
            chosen = name == self.selected
            dimmed = self.locked and not chosen
            picture = class_image(name, picture_w)
            pic_h = picture.height()
            cost_y = 5 + pic_h + 16
            pick_top = cost_y + 16
            y1 = pick_top + 30 + 5
            bottom = max(bottom, y1)
            edge = LIME if chosen else ("#4a4a46" if self._hover == name and not self.locked else EDGE)
            self.create_rectangle(x0, 0, x1, y1, fill=STRIP, outline=edge, width=2 if chosen else 1)
            self.create_image(x0 + 5, 5, anchor="nw", image=picture)
            text, color = token_text(self.tokens.get(name))
            self.create_text((x0 + x1) / 2, cost_y, text=text, fill=DIM if dimmed else color, font=(BODY, 11))
            self.create_rectangle(x0 + 5, pick_top, x1 - 5, pick_top + 30, fill=LIME if chosen else "#0c0c0e", outline="")
            self.create_text((x0 + x1) / 2, pick_top + 15, text="ВЫБРАНО" if chosen else "ВЫБРАТЬ",
                             fill=INK if chosen else (DIM if dimmed else MUTED), font=(HEAD, 12))
            if chosen:   # the flag in the corner, like a tab
                self.create_polygon(x1 - 62, 0, x1, 0, x1, 18, x1 - 54, 18, fill=LIME, outline="")
                self.create_text(x1 - 30, 9, text="ВЫБРАН", fill=INK, font=(HEAD, 9))
            if dimmed:   # while it runs the other cards step back
                self.create_rectangle(x0 + 1, 1, x1 - 1, y1 - 1, fill="#0c0c0e", outline="", stipple="gray50")
            self._boxes[name] = (x0, 0, x1, y1)
        self.configure(height=bottom + 2)


class AutocopPage(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        start: Callable[[str], None],
        stop: Callable[[], None],
        bind_hotkey: Callable[[], None],
        hotkey: str,
        on_class_selected: Callable[[str], None],
        selected_class: str,
        memory: dict | None = None,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self._start = start
        self._stop = stop
        self._bind_hotkey = bind_hotkey
        self._on_class_selected = on_class_selected
        if selected_class not in SERVICE_CLASSES:
            selected_class = SERVICE_CLASSES[0]
        self.selected = tk.StringVar(value=selected_class)
        self.status = tk.StringVar(value="Остановлено")
        self.hotkey = hotkey
        self._running = False
        memory = memory or {}
        self._last: dict = dict(memory.get("last") or {})

        scroll = ScrollPage(self, max_width=900)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        # the title: a slanted lime tab, and the key that starts and stops it in the game
        self.header = tk.Canvas(body, bg=COLORS["bg"], highlightthickness=0, bd=0, height=40)
        self.header.pack(fill="x", pady=(28, 16))
        self.header.bind("<Configure>", lambda _e: self._draw_header())

        tk.Label(body, text="ВЫБЕРИ ЭКИПИРОВКУ", bg=COLORS["bg"], fg=MUTED, font=(BODY, 10)).pack(anchor="w", pady=(0, 6))
        self.cards = LoadoutRow(body, self._select_class)
        self.cards.selected = selected_class
        self.cards.tokens = dict(memory.get("tokens") or {})
        self.cards.pack(fill="x")

        lower = tk.Frame(body, bg=COLORS["bg"])
        lower.pack(fill="x", pady=(12, 0))
        lower.grid_columnconfigure(0, weight=5, uniform="lower")
        lower.grid_columnconfigure(1, weight=4, uniform="lower")

        # the launch panel
        launch = tk.Frame(lower, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        launch.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        inner = tk.Frame(launch, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        self.launch_caption = tk.Label(inner, text="ГОТОВ К ЗАПУСКУ", bg=STRIP, fg=MUTED, font=(BODY, 10))
        self.launch_caption.pack(anchor="w")
        chosen_row = tk.Frame(inner, bg=STRIP)
        chosen_row.pack(fill="x", pady=(4, 2))
        self.rank_bar = tk.Frame(chosen_row, bg=RANKS[selected_class], width=4, height=26)
        self.rank_bar.pack(side="left")
        self.class_label = tk.Label(chosen_row, text=selected_class.upper(), bg=STRIP, fg=TEXT, font=(HEAD, 20))
        self.class_label.pack(side="left", padx=(8, 0))
        self.cost_label = tk.Label(chosen_row, text="", bg=STRIP, fg=MUTED, font=(BODY, 11))
        self.cost_label.pack(side="right")
        self.last_label = tk.Label(inner, text="", bg=STRIP, fg=MUTED, font=(BODY, 11), anchor="w", justify="left")
        self.last_label.pack(fill="x", pady=(0, 10))
        buttons = tk.Frame(inner, bg=STRIP)
        buttons.pack(fill="x")
        self.start_button = GameButton(buttons, "Запустить автокоп", self._start_selected, primary=True, size=16)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = GameButton(buttons, "Стоп", self._stop, primary=False, size=14)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")
        tk.Label(inner, textvariable=self.status, bg=STRIP, fg=MUTED, font=(BODY, 11), anchor="w", justify="left",
                 wraplength=420).pack(fill="x", pady=(10, 0))

        # how it works, and the key
        side = tk.Frame(lower, bg=COLORS["bg"])
        side.grid(row=0, column=1, sticky="nsew")
        steps = tk.Frame(side, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        steps.pack(fill="x")
        steps_inner = tk.Frame(steps, bg=STRIP)
        steps_inner.pack(fill="x", padx=12, pady=10)
        tk.Label(steps_inner, text="КАК РАБОТАЕТ", bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(anchor="w", pady=(0, 6))
        for color, key, text in ((LIME, "E", "жмёт, как только «Начать службу» светится"),
                                 (LIME, None, "сразу подтверждает выбранный класс"),
                                 ("#3da9f5", None, "крутит, пока не увидит «Завершить службу»")):
            row = tk.Frame(steps_inner, bg="#0f0f0e")
            row.pack(fill="x", pady=2)
            tk.Frame(row, bg=color, width=3).pack(side="left", fill="y")
            if key:
                tk.Label(row, text=key, bg=KEY, fg=STRIP, font=(HEAD, 9), padx=4).pack(side="left", padx=(8, 0), pady=6)
            tk.Label(row, text=text, bg="#0f0f0e", fg=TEXT, font=(BODY, 11), anchor="w").pack(side="left", padx=8, pady=6)

        bind = tk.Frame(side, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)
        bind.pack(fill="x", pady=(10, 0))
        bind_inner = tk.Frame(bind, bg=STRIP)
        bind_inner.pack(fill="x", padx=12, pady=10)
        bind_row = tk.Frame(bind_inner, bg=STRIP)
        bind_row.pack(fill="x")
        tk.Label(bind_row, text="КЛАВИША В ИГРЕ", bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(side="left")
        self.bind_button = GameButton(bind_row, self._bind_button_text(), self._bind_hotkey, primary=False, height=30, size=11)
        self.bind_button.pack(side="right")
        self.bind_note = tk.StringVar(value="Запуск и остановка автокопа прямо из игры.")
        tk.Label(bind_inner, textvariable=self.bind_note, bg=STRIP, fg=MUTED, font=(BODY, 10), anchor="w", justify="left",
                 wraplength=300).pack(fill="x", pady=(6, 0))
        tk.Frame(body, bg=COLORS["bg"], height=24).pack(fill="x")
        self._draw_selection()

    # ---- drawing ------------------------------------------------------------------------------------------------

    def _draw_header(self) -> None:
        canvas = self.header
        canvas.delete("all")
        width = canvas.winfo_width()
        state = "РАБОТАЕТ" if self._running else ""
        tab = 150
        canvas.create_polygon(0, 0, tab, 0, tab - 12, 40, 0, 40, fill=LIME, outline="")
        canvas.create_text(14, 20, text="АВТОКОП", anchor="w", fill=INK, font=(HEAD, 22))
        canvas.create_text(tab + 12, 20, text=state or "выбор класса и запуск службы", anchor="w",
                           fill=LIME if state else MUTED, font=(HEAD if state else BODY, 13 if state else 11))
        hotkey = (self.hotkey or "").upper()
        if hotkey:
            note = canvas.create_text(width, 20, text="запуск / стоп в игре", anchor="e", fill=MUTED, font=(BODY, 11))
            x0 = canvas.bbox(note)[0] - 10
            label = canvas.create_text(x0, 20, text=hotkey, anchor="e", fill=STRIP, font=(HEAD, 11))
            bx0, _by0, bx1, _by1 = canvas.bbox(label)
            canvas.tag_raise(label, canvas.create_rectangle(bx0 - 6, 9, bx1 + 6, 31, fill=KEY, outline=""))

    def _draw_selection(self) -> None:
        name = self.selected.get()
        self.cards.selected = name
        self.cards.locked = self._running
        self.cards.draw()
        self.rank_bar.configure(bg=RANKS.get(name, "#3f9b4b"))
        self.class_label.configure(text=name.upper())
        text, color = token_text(self.cards.tokens.get(name))
        self.cost_label.configure(text=text, fg=color)
        last = self._last
        if last.get("tries") is not None and last.get("seconds") is not None:
            tries = int(last["tries"])
            when = last.get("class") or ""
            self.last_label.configure(
                text=f"последний раз: {when} · {tries} {plural(tries, 'попытка', 'попытки', 'попыток')} · {clock(float(last['seconds']))}",
                fg=BLUE)
        else:
            self.last_label.configure(text="ещё не запускался", fg=MUTED)
        self.launch_caption.configure(text="РАБОТАЕТ" if self._running else "ГОТОВ К ЗАПУСКУ",
                                      fg=LIME if self._running else MUTED)
        self._draw_header()

    # ---- actions ------------------------------------------------------------------------------------------------

    def _bind_button_text(self) -> str:
        return f"Назначить: {self.hotkey.upper()}" if self.hotkey else "Назначить"

    def _select_class(self, name: str) -> None:
        if self._running:
            return
        self.selected.set(name)
        self._on_class_selected(name)
        self._draw_selection()

    def _start_selected(self) -> None:
        self._start(self.selected.get())

    # ---- what the app calls -------------------------------------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.configure(state="disabled" if running else "normal")
        self.bind_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self._draw_selection()

    def set_tokens(self, tokens: dict) -> None:
        """Costs read on the loadout screen: {class: "бесплатно" | "19/1" | "0"}."""
        self.cards.tokens.update({k: v for k, v in tokens.items() if v})
        self._draw_selection()

    def set_last_result(self, result: dict) -> None:
        """The last time the service was joined: {"class", "tries", "seconds", "at"}."""
        self._last = dict(result)
        self._draw_selection()

    def set_autocop_hotkey(self, hotkey: str) -> None:
        self.hotkey = hotkey
        self.bind_button.configure(text=self._bind_button_text())
        self.bind_note.set("Запуск и остановка автокопа прямо из игры.")
        self._draw_header()

    def set_hotkey_capture(self, capturing: bool) -> None:
        if capturing:
            self.bind_button.configure(text="Нажми клавишу…", state="disabled")
            self.bind_note.set("Нажми клавишу для бинда. Esc — отмена.")
        else:
            self.bind_button.configure(text=self._bind_button_text(), state="normal")
            self.bind_note.set("Запуск и остановка автокопа прямо из игры.")

    def set_status(self, text: str) -> None:
        self.status.set(text)
