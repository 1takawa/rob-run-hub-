"""«Оверлей»: which blocks and elements show over the game, their size and place, and the overlay's keys.

Drawn in the look of ROB RUN, like the overlay itself: each block has the same slanted colour tab it has in the game
(HIDDEN POOL lime, ПЛАНТАЦИЯ green, ЛАБОРАТОРИЯ amber); what is switched off simply disappears from the overlay."""

from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .. import overlay_bridge as bridge
from ..models import Profile
from .theme import COLORS, ScrollPage, Toggle

LIME, INK, STRIP, EDGE, PANEL = "#c6fb33", "#141414", "#1b1b1a", "#2c2c2a", "#0f0f0e"
TEXT, SOFT, MUTED, DIM = "#e9e9e4", "#c9c9c2", "#8d8d86", "#5f5f59"
HEAD, BODY = "Bahnschrift SemiBold Condensed", "Bahnschrift Condensed"

# name -> (tab text, tab colour, ink on the tab, description, [(element key, chip text, bar colour)])
BLOCKS = {
    "stats": ("HIDDEN POOL", LIME, INK, "Сессия: ноки, смерти, аресты, время в игре, история", [
        ("kills", "Убийства", None), ("knocks", "Ноки", "#d3b2ff"), ("deaths", "Смерти", "#ff927c"),
        ("arrests", "Аресты", "#79cfff"), ("playtime", "Время в игре", None),
        ("lifetime", "За всё время", None), ("history", "История", None),
    ]),
    "plantation": ("ПЛАНТАЦИЯ", "#46c46a", "#0d1f12", "Таймер сбора и пакеты", [
        ("timer", "Таймер", None), ("yellow", "Жёлтые", COLORS["yellow"]),
        ("purple", "Фиолетовые", COLORS["purple"]), ("green", "Зелёные", COLORS["pkg_green"]),
    ]),
    "laboratory": ("ЛАБОРАТОРИЯ", "#e9b93a", INK, "Таймер текущей партии · в разработке", []),
}
SLIDERS = (
    ("scale", "Размер", 70, 160, lambda v: f"{round(v)}%"),
    ("opacity", "Прозрачность", 35, 100, lambda v: f"{round(v)}%"),
    ("x_percent", "По горизонтали", 0, 100, lambda v: f"{round(v)}%"),
    ("y_percent", "По вертикали", 0, 100, lambda v: f"{round(v)}%"),
)
HOTKEY_LABELS = (("toggle", "Показать / скрыть"), ("settings", "Окно настроек"), ("exit", "Закрыть оверлей"))


def button(parent: tk.Misc, text: str, command, *, primary: bool = False) -> tk.Label:
    widget = tk.Label(parent, text=text.upper(), bg=LIME if primary else STRIP, fg=INK if primary else TEXT,
                      font=(HEAD, 12), padx=14, pady=5, cursor="hand2",
                      highlightthickness=1, highlightbackground=LIME if primary else "#3a3a37")
    widget.bind("<ButtonPress-1>", lambda _e: command())
    return widget


def tab(parent: tk.Misc, text: str, color: str, ink: str, *, size: int = 15) -> tk.Canvas:
    """The slanted colour tab with the name on it, as the blocks have in the game."""
    canvas = tk.Canvas(parent, bg=parent.cget("bg"), highlightthickness=0, height=size + 16)
    label = canvas.create_text(10, (size + 16) / 2, text=text, anchor="w", fill=ink, font=(HEAD, size))
    right = canvas.bbox(label)[2] + 20
    canvas.tag_raise(label, canvas.create_polygon(0, 0, right, 0, right - 10, size + 16, 0, size + 16, fill=color, outline=""))
    canvas.configure(width=right + 2)
    return canvas


class Chip(tk.Frame):
    """An element of a block: lit (lime frame) when it shows, dim when it does not."""

    def __init__(self, parent: tk.Misc, text: str, variable: tk.BooleanVar, command, bar: str | None) -> None:
        super().__init__(parent, bg=PANEL, highlightthickness=1, highlightbackground=EDGE, cursor="hand2")
        self.var, self.command, self.enabled = variable, command, True
        self.bar = tk.Frame(self, bg=bar or PANEL, width=4)
        self.bar.pack(side="left", fill="y")
        self.label = tk.Label(self, text=text.upper(), bg=PANEL, fg=TEXT, font=(HEAD, 11), padx=10, pady=4, cursor="hand2")
        self.label.pack(side="left")
        self._bar_color = bar
        for widget in (self, self.label, self.bar):
            widget.bind("<ButtonPress-1>", self._press)
        variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def _press(self, _event=None) -> None:
        if self.enabled:
            self.var.set(not self.var.get())
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.draw()

    def draw(self) -> None:
        on = self.var.get() and self.enabled
        bg = "#22251a" if on else PANEL
        self.configure(bg=bg, highlightbackground=LIME if on else EDGE, cursor="hand2" if self.enabled else "arrow")
        self.label.configure(bg=bg, fg=TEXT if on else (DIM if self.enabled else "#3a3a37"),
                             cursor="hand2" if self.enabled else "arrow")
        self.bar.configure(bg=(self._bar_color or bg) if on else bg)


class Bar(tk.Canvas):
    """A slider in the same look: a dark track, the lime part up to the value, a square knob; 1:1 with the pointer."""

    def __init__(self, parent: tk.Misc, low: float, high: float, command: Callable[[float], None]) -> None:
        super().__init__(parent, height=22, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self._low, self._high, self._value, self._command = low, high, low, command
        self.bind("<ButtonPress-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Configure>", lambda _e: self._draw())

    def get(self) -> float:
        return self._value

    def set(self, value: float) -> None:
        self._value = min(self._high, max(self._low, value))
        self._draw()

    def _drag(self, event: tk.Event) -> None:
        span = max(1, self.winfo_width() - 14)
        self._value = self._low + (self._high - self._low) * min(1.0, max(0.0, (event.x - 7) / span))
        self._draw()
        self._command(self._value)

    def _draw(self) -> None:
        self.delete("all")
        width = max(20, self.winfo_width())
        x = 7 + (width - 14) * (self._value - self._low) / max(1e-9, self._high - self._low)
        self.create_rectangle(7, 9, width - 7, 13, fill=PANEL, outline="")
        self.create_rectangle(7, 9, x, 13, fill=LIME, outline="")
        self.create_rectangle(x - 6, 4, x + 6, 18, fill=TEXT, outline=INK)


class OverlayPage(tk.Frame):
    def __init__(self, parent: tk.Misc, profile: Profile, callbacks: dict[str, Callable[[], None]]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.profile = profile
        self.callbacks = callbacks
        self.settings = bridge.read_settings()
        self._save_job: str | None = None
        self._loading = False
        self.block_vars: dict[str, dict[str, tk.BooleanVar]] = {}
        self.chips: dict[str, dict[str, Chip]] = {}
        self.sliders: dict[str, Bar] = {}
        self.slider_texts: dict[str, tk.StringVar] = {}
        self.hotkey_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        self.hotkey_note = tk.StringVar(value="")
        self._running: bool | None = None

        scroll = ScrollPage(self, max_width=900)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        self._header(body)
        self._build_status(body)
        for name in BLOCKS:
            self._build_block(body, name)
        lower = tk.Frame(body, bg=COLORS["bg"])
        lower.pack(fill="x")
        lower.grid_columnconfigure(0, weight=3, uniform="lower")
        lower.grid_columnconfigure(1, weight=2, uniform="lower")
        self._build_layout(lower).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._build_hotkeys(lower).grid(row=0, column=1, sticky="nsew")
        tk.Frame(body, height=28, bg=COLORS["bg"]).pack()
        self._load()

    # ---- sections ----------------------------------------------------------------------------------------------

    @staticmethod
    def _box(parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=STRIP, highlightthickness=1, highlightbackground=EDGE)

    def _header(self, body: tk.Misc) -> None:
        bar = tk.Canvas(body, bg=COLORS["bg"], highlightthickness=0, height=40)
        bar.pack(fill="x", pady=(28, 14))
        bar.create_polygon(0, 0, 160, 0, 148, 40, 0, 40, fill=LIME, outline="")
        bar.create_text(14, 20, text="ОВЕРЛЕЙ", anchor="w", fill=INK, font=(HEAD, 22))
        bar.create_text(172, 20, text="что показывать поверх игры", anchor="w", fill=MUTED, font=(BODY, 11))

    def _build_status(self, body: tk.Misc) -> None:
        box = self._box(body)
        box.pack(fill="x", pady=(0, 10))
        self.status_bar = tk.Frame(box, bg=DIM, width=3)
        self.status_bar.pack(side="left", fill="y")
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        head = tk.Frame(inner, bg=STRIP)
        head.pack(anchor="w")
        self.dot = tk.Canvas(head, width=10, height=10, bg=STRIP, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self._dot_item = self.dot.create_oval(1, 1, 9, 9, fill=DIM, outline="")
        self.status_text = tk.Label(head, text="ОВЕРЛЕЙ НЕ ЗАПУЩЕН", bg=STRIP, fg=TEXT, font=(HEAD, 16))
        self.status_text.pack(side="left")
        tk.Label(inner, text="Пока эта вкладка открыта, оверлей виден в игре: меняй настройки и смотри результат сразу.",
                 bg=STRIP, fg=MUTED, font=(BODY, 11), anchor="w").pack(anchor="w", pady=(2, 0))
        buttons = tk.Frame(box, bg=STRIP)
        buttons.pack(side="right", padx=14)
        self.start_button = button(buttons, "Запустить", self.callbacks["start"], primary=True)
        self.stop_button = button(buttons, "Закрыть", self.callbacks["stop"])
        self.start_button.pack()

    def _build_block(self, body: tk.Misc, name: str) -> None:
        text, color, ink, description, elements = BLOCKS[name]
        box = self._box(body)
        box.pack(fill="x", pady=(0, 10))
        head = tk.Frame(box, bg=PANEL)
        head.pack(fill="x")
        tab(head, text, color, ink).pack(side="left")
        tk.Label(head, text=description, bg=PANEL, fg="#e9b93a" if name == "laboratory" else MUTED,
                 font=(BODY, 11)).pack(side="left", padx=(12, 0))
        enabled = tk.BooleanVar(value=True)
        self.block_vars.setdefault(name, {})["enabled"] = enabled
        switch = tk.Frame(head, bg=PANEL)
        switch.pack(side="right", padx=12)
        Toggle(switch, enabled, command=lambda n=name: self._changed(n)).pack()
        if not elements:
            return
        wrap = tk.Frame(box, bg=STRIP)
        wrap.pack(fill="x", padx=14, pady=12)
        self.chips[name] = {}
        for index, (key, chip_text, bar) in enumerate(elements):
            var = tk.BooleanVar(value=True)
            self.block_vars[name][key] = var
            chip = Chip(wrap, chip_text, var, lambda n=name: self._changed(n), bar)
            chip.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 8), pady=(0, 8))
            self.chips[name][key] = chip

    def _build_layout(self, parent: tk.Misc) -> tk.Widget:
        box = self._box(parent)
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text="ВИД И ПОЛОЖЕНИЕ", bg=STRIP, fg=TEXT, font=(HEAD, 15)).pack(anchor="w")
        tk.Label(inner, text="Блоки идут одним столбцом и не налезают друг на друга.", bg=STRIP, fg=MUTED,
                 font=(BODY, 10)).pack(anchor="w", pady=(0, 8))
        for key, title, low, high, _fmt in SLIDERS:
            row = tk.Frame(inner, bg=STRIP)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=title.upper(), bg=STRIP, fg=MUTED, font=(BODY, 10), width=15, anchor="w").pack(side="left")
            text = tk.StringVar()
            self.slider_texts[key] = text
            tk.Label(row, textvariable=text, bg=STRIP, fg=TEXT, font=(HEAD, 12), width=5, anchor="e").pack(side="right")
            slider = Bar(row, low, high, lambda v, k=key: self._slider(k, v))
            slider.pack(side="left", fill="x", expand=True, padx=10)
            self.sliders[key] = slider
        button(inner, "По умолчанию", self._reset).pack(anchor="w", pady=(10, 0))
        return box

    def _build_hotkeys(self, parent: tk.Misc) -> tk.Widget:
        box = self._box(parent)
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text="КЛАВИШИ ОВЕРЛЕЯ", bg=STRIP, fg=TEXT, font=(HEAD, 15)).pack(anchor="w", pady=(0, 6))
        for name, title in HOTKEY_LABELS:
            tk.Label(inner, text=title.upper(), bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(anchor="w", pady=(4, 2))
            row = tk.Frame(inner, bg=STRIP)
            row.pack(fill="x")
            modifiers, key = tk.StringVar(), tk.StringVar()
            self.hotkey_vars[name] = (modifiers, key)
            ttk.Combobox(row, textvariable=modifiers, values=bridge.HOTKEY_MODIFIER_CHOICES, state="readonly", width=12).pack(side="left")
            ttk.Combobox(row, textvariable=key, values=bridge.HOTKEY_KEY_CHOICES, state="readonly", width=7).pack(side="left", padx=(6, 0))
        actions = tk.Frame(inner, bg=STRIP)
        actions.pack(fill="x", pady=(12, 0))
        button(actions, "Применить", self._apply_hotkeys).pack(side="left")
        tk.Label(actions, textvariable=self.hotkey_note, bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(side="left", padx=10)
        return box

    # ---- data ---------------------------------------------------------------------------------------------------

    def _load(self) -> None:
        self._loading = True
        settings = self.settings
        for name, variables in self.block_vars.items():
            for key, var in variables.items():
                var.set(bool(settings["blocks"][name][key]))
        self.sliders["scale"].set(settings["scale"] * 100)
        self.sliders["opacity"].set(settings["opacity"] * 100)
        self.sliders["x_percent"].set(settings["x_percent"])
        self.sliders["y_percent"].set(settings["y_percent"])
        for name, (modifiers, key) in self.hotkey_vars.items():
            modifiers.set(settings["hotkeys"][name]["modifiers"])
            key.set(settings["hotkeys"][name]["key"])
        self._loading = False
        self._sync_chip_state()
        self._update_texts()

    def _sync_chip_state(self) -> None:
        for name, chips in self.chips.items():
            enabled = self.block_vars[name]["enabled"].get()
            for chip in chips.values():
                chip.set_enabled(enabled)

    def _update_texts(self) -> None:
        values = {"scale": self.settings["scale"] * 100, "opacity": self.settings["opacity"] * 100,
                  "x_percent": self.settings["x_percent"], "y_percent": self.settings["y_percent"]}
        for key, _t, _l, _h, fmt in SLIDERS:
            self.slider_texts[key].set(fmt(values[key]))

    def _changed(self, _block: str = "") -> None:
        if self._loading:
            return
        for name, variables in self.block_vars.items():
            for key, var in variables.items():
                self.settings["blocks"][name][key] = bool(var.get())
        self._sync_chip_state()
        self._schedule_save()

    def _slider(self, key: str, value: float) -> None:
        if self._loading:
            return
        if key == "scale":
            self.settings["scale"] = round(value / 100, 2)
        elif key == "opacity":
            self.settings["opacity"] = round(value / 100, 2)
        else:
            self.settings[key] = int(round(value))
        self._update_texts()
        self._schedule_save()

    def _schedule_save(self) -> None:
        # Debounced: the overlay re-reads the file within a second.
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(80, self._save)

    def _save(self) -> None:
        self._save_job = None
        bridge.write_settings(self.settings)

    def _reset(self) -> None:
        defaults = copy.deepcopy(bridge.DEFAULT_SETTINGS)
        defaults["hotkeys"] = self.settings["hotkeys"]
        self.settings = defaults
        self._load()
        self._save()

    def _apply_hotkeys(self) -> None:
        chosen = {name: (mods.get(), key.get()) for name, (mods, key) in self.hotkey_vars.items()}
        if len(set(chosen.values())) != len(chosen):
            self.hotkey_note.set("У каждой команды своё сочетание.")
            return
        for name, (mods, key) in chosen.items():
            self.settings["hotkeys"][name] = {"modifiers": mods, "key": key}
        self._save()
        self.hotkey_note.set("Сохранено")

    def refresh(self, running: bool, profile: Profile) -> None:
        self.profile = profile
        if running == self._running:
            return   # called every second: nothing to redraw
        self._running = running
        self.status_text.configure(text="ОВЕРЛЕЙ РАБОТАЕТ" if running else "ОВЕРЛЕЙ НЕ ЗАПУЩЕН")
        self.dot.itemconfigure(self._dot_item, fill=LIME if running else DIM)
        self.status_bar.configure(bg=LIME if running else DIM)
        self.start_button.pack_forget()
        self.stop_button.pack_forget()
        (self.stop_button if running else self.start_button).pack()
