"""Overlay: pick the blocks and elements you want; what is switched off simply disappears."""

from __future__ import annotations

import copy
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .. import overlay_bridge as bridge
from ..models import Profile
from .theme import (COLORS, FONT, FONT_SEMI, Card, ChipToggle, PillButton, ScrollPage, Slider, Toggle, caption,
                    divider, label, page_header, rounded_points)

# name -> (title, description, [(element key, chip text, dot colour)])
BLOCKS = {
    "stats": ("Личная статистика", "Твои ноки, смерти, аресты и убийства", [
        ("kills", "Убийства", None), ("knocks", "Ноки", None), ("deaths", "Смерти", None),
        ("arrests", "Аресты", "#79cfff"),
        ("playtime", "Время в игре", None),
        ("lifetime", "За всё время", None), ("history", "История", None),
    ]),
    "plantation": ("Плантация", "Таймер сбора и пакеты", [
        ("timer", "Таймер", None), ("yellow", "Жёлтые", COLORS["yellow"]),
        ("purple", "Фиолетовые", COLORS["purple"]), ("green", "Зелёные", COLORS["pkg_green"]),
    ]),
    "laboratory": ("Лаборатория", "Таймер текущей партии", []),
}
SLIDERS = (
    ("scale", "Размер", 70, 160, lambda v: f"{round(v)}%"),
    ("opacity", "Прозрачность", 35, 100, lambda v: f"{round(v)}%"),
    ("x_percent", "По горизонтали", 0, 100, lambda v: f"{round(v)}%"),
    ("y_percent", "По вертикали", 0, 100, lambda v: f"{round(v)}%"),
)
HOTKEY_LABELS = (("toggle", "Показать / скрыть"), ("settings", "Окно настроек"), ("exit", "Закрыть оверлей"))


class OverlayPage(tk.Frame):
    def __init__(self, parent: tk.Misc, profile: Profile, callbacks: dict[str, Callable[[], None]]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.profile = profile
        self.callbacks = callbacks
        self.settings = bridge.read_settings()
        self._save_job: str | None = None
        self._loading = False
        self.running_text = tk.StringVar(value="Оверлей не запущен")
        self.block_vars: dict[str, dict[str, tk.BooleanVar]] = {}
        self.chips: dict[str, dict[str, ChipToggle]] = {}
        self.sliders: dict[str, Slider] = {}
        self.slider_texts: dict[str, tk.StringVar] = {}
        self.hotkey_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        self.hotkey_note = tk.StringVar(value="")

        scroll = ScrollPage(self, max_width=780)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Оверлей", "Что показывать поверх игры").pack(fill="x", pady=(34, 22), padx=4)
        self._build_status(body)
        for name in BLOCKS:
            self._build_block(body, name)
        self._build_layout(body)
        self._build_hotkeys(body)
        tk.Frame(body, height=34, bg=COLORS["bg"]).pack()
        self._load()

    # ---- sections ----------------------------------------------------------------------

    def _build_status(self, body: tk.Frame) -> None:
        row = tk.Frame(body, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, 8))
        self.dot = tk.Canvas(row, width=10, height=10, bg=COLORS["bg"], highlightthickness=0)
        self.dot.pack(side="left", padx=(4, 10))
        self._dot_item = self.dot.create_oval(1, 1, 9, 9, fill=COLORS["muted"], outline="")
        label(row, "", size=11, textvariable=self.running_text).pack(side="left")
        self.stop_button = PillButton(row, "Закрыть", self.callbacks["stop"], style="ghost", padx=18, height=36)
        self.start_button = PillButton(row, "Запустить", self.callbacks["start"], style="primary", padx=22, height=36)
        self.start_button.pack(side="right")
        label(body, "Пока эта вкладка открыта, оверлей виден в игре — меняй настройки и смотри результат сразу.",
              size=9, color="muted", justify="left", wraplength=640).pack(anchor="w", padx=6, pady=(0, 18))

    def _build_block(self, body: tk.Frame, name: str) -> None:
        title, description, elements = BLOCKS[name]
        card = Card(body, padding=(26, 20), radius=20)
        card.pack(fill="x", pady=(0, 12))
        head = tk.Frame(card.body, bg=card.fill)
        head.pack(fill="x")
        text = tk.Frame(head, bg=card.fill)
        text.pack(side="left")
        label(text, title, size=13, font=FONT_SEMI, bg=card.fill).pack(anchor="w")
        label(text, description, size=9, color="amber" if name == "laboratory" else "muted", bg=card.fill).pack(anchor="w", pady=(2, 0))
        enabled = tk.BooleanVar(value=True)
        self.block_vars.setdefault(name, {})["enabled"] = enabled
        Toggle(head, enabled, command=lambda n=name: self._changed(n)).pack(side="right")
        if not elements:
            return
        wrap = tk.Frame(card.body, bg=card.fill)
        wrap.pack(fill="x", pady=(16, 0))
        self.chips[name] = {}
        for index, (key, text_value, dot) in enumerate(elements):
            var = tk.BooleanVar(value=True)
            self.block_vars[name][key] = var
            chip = ChipToggle(wrap, text_value, var, command=lambda n=name: self._changed(n), dot=dot)
            chip.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 8), pady=(0, 8))
            self.chips[name][key] = chip

    def _build_layout(self, body: tk.Frame) -> None:
        card = Card(body, padding=(26, 20), radius=20)
        card.pack(fill="x", pady=(0, 12))
        label(card.body, "Вид и положение", size=13, font=FONT_SEMI, bg=card.fill).pack(anchor="w")
        label(card.body, "Блоки идут одним столбцом и не налезают друг на друга.", size=9, color="muted", bg=card.fill).pack(anchor="w", pady=(2, 14))
        for key, title, low, high, fmt in SLIDERS:
            row = tk.Frame(card.body, bg=card.fill)
            row.pack(fill="x", pady=4)
            label(row, title, size=10, bg=card.fill, width=16, anchor="w").pack(side="left")
            text = tk.StringVar()
            self.slider_texts[key] = text
            label(row, "", size=10, color="muted", bg=card.fill, width=6, anchor="e", textvariable=text).pack(side="right")
            slider = Slider(row, low, high, low, command=lambda v, k=key: self._slider(k, v), width=300)
            slider.pack(side="left", fill="x", expand=True, padx=12)
            self.sliders[key] = slider
        PillButton(card.body, "По умолчанию", self._reset, style="ghost", padx=16, height=34, size=9).pack(anchor="w", pady=(14, 0))

    def _build_hotkeys(self, body: tk.Frame) -> None:
        card = Card(body, padding=(26, 20), radius=20)
        card.pack(fill="x")
        label(card.body, "Горячие клавиши", size=13, font=FONT_SEMI, bg=card.fill).pack(anchor="w", pady=(0, 8))
        for name, title in HOTKEY_LABELS:
            row = tk.Frame(card.body, bg=card.fill)
            row.pack(fill="x", pady=5)
            label(row, title, size=10, bg=card.fill).pack(side="left")
            modifiers, key = tk.StringVar(), tk.StringVar()
            self.hotkey_vars[name] = (modifiers, key)
            ttk.Combobox(row, textvariable=key, values=bridge.HOTKEY_KEY_CHOICES, state="readonly", width=8).pack(side="right")
            ttk.Combobox(row, textvariable=modifiers, values=bridge.HOTKEY_MODIFIER_CHOICES, state="readonly", width=12).pack(side="right", padx=(0, 6))
        actions = tk.Frame(card.body, bg=card.fill)
        actions.pack(fill="x", pady=(12, 0))
        PillButton(actions, "Применить", self._apply_hotkeys, style="secondary", padx=18, height=34, size=9).pack(side="left")
        label(actions, "", size=9, color="muted", bg=card.fill, textvariable=self.hotkey_note).pack(side="left", padx=12)

    # ---- data ---------------------------------------------------------------------------

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
        self.running_text.set("Оверлей работает" if running else "Оверлей не запущен")
        self.dot.itemconfigure(self._dot_item, fill=COLORS["green"] if running else COLORS["faint"])
        self.start_button.pack_forget()
        self.stop_button.pack_forget()
        (self.stop_button if running else self.start_button).pack(side="right")
