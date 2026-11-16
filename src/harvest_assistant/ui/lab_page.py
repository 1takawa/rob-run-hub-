"""Laboratory timer, input steps, and screen markers."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..laboratory import marker_available
from ..models import Profile, remaining_seconds
from .theme import COLORS, Card, PillButton, ScrollPage, caption, label, page_header


class LabPage(tk.Frame):
    def __init__(self, parent: tk.Misc, profile: Profile, save: Callable[..., str], choose_marker: Callable[[str], None]) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        lab = profile.laboratory
        self._save = save
        self.duration = tk.StringVar(value=f"{lab.timer.interval_seconds / 60:g}")
        self.remaining = tk.StringVar()
        self.timer = tk.StringVar()
        self.note = tk.StringVar()
        self.markers = tk.StringVar()
        self.editors: dict[str, tk.Text] = {}

        scroll = ScrollPage(self, max_width=760)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Лаборатория", "Настрой цикл приготовления").pack(fill="x", pady=(30, 18), padx=4)
        card = Card(body, padding=(24, 20), radius=20)
        card.pack(fill="x", pady=(0, 12))
        caption(card.body, "Текущая партия", card.fill).pack(anchor="w")
        label(card.body, "", textvariable=self.timer, bg=card.fill, size=21).pack(anchor="w", pady=(6, 12))
        row = tk.Frame(card.body, bg=card.fill)
        row.pack(fill="x")
        for title, variable in (("Длительность партии, мин", self.duration), ("Задать остаток, мин", self.remaining)):
            item = tk.Frame(row, bg=card.fill)
            item.pack(side="left", padx=(0, 22))
            label(item, title, bg=card.fill, size=9).pack(anchor="w")
            ttk.Entry(item, textvariable=variable, width=10).pack(anchor="w", pady=(4, 0))
        label(card.body, "Остаток 0 означает, что партия готова. Пустое поле сохраняет текущий таймер.",
              bg=card.fill, size=9, color="muted", wraplength=520, justify="left").pack(anchor="w", pady=(8, 0))

        card = Card(body, padding=(24, 20), radius=20)
        card.pack(fill="x", pady=(0, 24))
        caption(card.body, "Действия на точке Ctrl+F3", card.fill).pack(anchor="w")
        label(card.body, "Одна команда на строку. Например: key e, wait 0.5, click 640 480, rightclick 640 480, drag 640 480 750 500.",
              bg=card.fill, size=9, color="muted", wraplength=520, justify="left").pack(anchor="w", pady=(6, 10))
        for key, title in (("prepare", "Открыть"), ("actions", "Сбор и запуск"), ("finish", "Закрыть")):
            label(card.body, title, bg=card.fill, size=10).pack(anchor="w", pady=(6, 3))
            editor = tk.Text(card.body, height=3 if key == "actions" else 2, width=42,
                             bg=COLORS["bg"], fg=COLORS["text"], insertbackground=COLORS["text"],
                             relief="flat", padx=7, pady=4, undo=True)
            editor.insert("1.0", getattr(lab, key))
            editor.pack(fill="x")
            self.editors[key] = editor
        label(card.body, "Выбери область на полном скриншоте для каждого состояния. Перед открытием нужен ориентир у самой станции.",
              bg=card.fill, size=9, color="muted", wraplength=520, justify="left").pack(anchor="w", pady=(12, 6))
        row = tk.Frame(card.body, bg=card.fill)
        row.pack(fill="x")
        for key, title in (("station", "У станции"), ("ready", "Готово"), ("running", "Партия запущена")):
            PillButton(row, title, lambda stage=key: choose_marker(stage), style="secondary",
                       padx=9, height=32, size=9).pack(side="left", padx=(0, 6))
        label(card.body, "", textvariable=self.markers, bg=card.fill, size=9, color="muted").pack(anchor="w", pady=8)
        PillButton(card.body, "Сохранить", self._save_form, style="primary", padx=16, height=34).pack(anchor="w")
        label(card.body, "", textvariable=self.note, bg=card.fill, size=9,
              wraplength=520, justify="left").pack(anchor="w", pady=(6, 0))

    def _save_form(self) -> None:
        message = self._save(
            self.duration.get(), self.remaining.get(),
            *(self.editors[key].get("1.0", "end-1c") for key in ("prepare", "actions", "finish")),
        )
        self.note.set(message)
        if not message.startswith(("Не сохранено", "Останови")):
            self.remaining.set("")

    def refresh(self, profile: Profile, vision) -> None:
        lab = profile.laboratory
        seconds = remaining_seconds(lab.timer, profile, time.time())
        self.timer.set("Задай время партии" if seconds is None else "Готово" if seconds == 0 else
                       f"{seconds // 60}:{seconds % 60:02d}")
        active = sum(marker_available(marker, vision) for marker in (lab.station, lab.ready, lab.running))
        route = "точка в маршруте есть" if profile.route.lab_offset is not None else "отметь точку в маршруте"
        self.markers.set(f"Ориентиры: {active} из 3 · {route}")
