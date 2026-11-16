"""Statistics: cycles and collected packages by period, and the 7-day server wipe."""

from __future__ import annotations

import time
import tkinter as tk

from ..overlay_bridge import format_money
from ..stats import KINDS, PRICES, StatsStore
from .theme import COLORS, FONT_SEMI, Card, PillButton, ScrollPage, Segmented, caption, divider, label, page_header

RANGES = [("session", "Последняя сессия"), ("1", "1 день"), ("2", "2 дня"), ("3", "3 дня"), ("wipe", "Весь вайп")]
NAMES = {"yellow": ("Жёлтые", COLORS["yellow"]), "purple": ("Фиолетовые", COLORS["purple"]), "green": ("Зелёные", COLORS["pkg_green"])}


class StatsPage(tk.Frame):
    def __init__(self, parent: tk.Misc, stats: StatsStore) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.stats = stats
        self.range_var = tk.StringVar(value="session")
        self._signature: tuple | None = None
        self.wipe_var = tk.StringVar()
        self.wipe_info = tk.StringVar()

        scroll = ScrollPage(self, max_width=860)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Статистика", "Циклы, пакеты и деньги").pack(fill="x", pady=(34, 20), padx=4)
        Segmented(body, self.range_var, RANGES, command=self.refresh).pack(anchor="w", pady=(0, 18))

        # wipe: shown only for the "whole wipe" range
        self.wipe_card = Card(body, padding=(26, 18), radius=20)
        row = tk.Frame(self.wipe_card.body, bg=self.wipe_card.fill)
        row.pack(fill="x")
        text = tk.Frame(row, bg=self.wipe_card.fill)
        text.pack(side="left")
        label(text, "Сервер создан", size=11, font=FONT_SEMI, bg=self.wipe_card.fill).pack(anchor="w")
        label(text, "ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ. Сервер живёт 7 дней, потом полный сброс.", size=9, color="muted",
              bg=self.wipe_card.fill, justify="left", wraplength=360).pack(anchor="w", pady=(2, 0))
        from tkinter import ttk
        PillButton(row, "Сохранить", self._save_wipe, style="secondary", padx=16, height=34, size=9).pack(side="right")
        ttk.Entry(row, textvariable=self.wipe_var, width=18).pack(side="right", padx=(0, 8))
        label(self.wipe_card.body, "", size=10, color="green", bg=self.wipe_card.fill, textvariable=self.wipe_info,
              justify="left").pack(anchor="w", pady=(12, 0))

        self.summary = Card(body, padding=(28, 22), radius=22)
        self.summary.pack(fill="x", pady=(0, 14))
        self.days = Card(body, padding=(28, 22), radius=22)
        self.days.pack(fill="x", pady=(0, 34))
        self.wipe_var.set(self._fmt_date(self.stats.wipe_start) if self.stats.wipe_start else "")
        self.refresh()

    # ---- helpers ------------------------------------------------------------------------

    @staticmethod
    def _fmt_date(timestamp: float) -> str:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))

    def _save_wipe(self) -> None:
        text = self.wipe_var.get().strip()
        if not text:
            self.stats.set_wipe_start(None)
        else:
            parsed = self.stats.parse_date(text)
            if parsed is None:
                self.wipe_info.set("Не понял дату. Пример: 09.11.2026 или 09.11.2026 14:30")
                return
            self.stats.set_wipe_start(parsed)
            self.wipe_var.set(self._fmt_date(parsed))
        self._signature = None
        self.refresh()

    def _scope(self) -> tuple[float | None, int | None, str]:
        choice = self.range_var.get()
        if choice == "session":
            return None, self.stats.last_session_id(), "последняя сессия"
        if choice == "wipe":
            return self.stats.wipe_start, None, "весь вайп"
        days = int(choice)
        return time.time() - days * 86400, None, f"за {days} дн."

    # ---- rendering ----------------------------------------------------------------------

    def refresh(self) -> None:
        since, session_id, title = self._scope()
        wipe = self.range_var.get() == "wipe"
        if wipe:
            self.wipe_card.pack(fill="x", pady=(0, 14), before=self.summary)
            start, end = self.stats.wipe_start, self.stats.wipe_end
            if start is None:
                self.wipe_info.set("Дата сервера не задана — показана статистика за всё время.")
            else:
                left = max(0, int(end - time.time()))
                days, rest = divmod(left, 86400)
                self.wipe_info.set(f"Сброс: {self._fmt_date(end)} · осталось {days} д {rest // 3600} ч" if left else f"Сервер сброшен {self._fmt_date(end)}")
        else:
            self.wipe_card.pack_forget()

        if self.range_var.get() == "session" and session_id is None:
            summary = {"cycles": 0, "loot": {k: 0 for k in KINDS}, "money": 0, "reconnects": 0}
            rows: list[dict] = []
        else:
            summary = self.stats.summary(since, session_id)
            rows = self.stats.by_day(since, session_id)
        signature = (self.range_var.get(), summary["cycles"], summary.get("reconnects", 0), tuple(summary["loot"].items()), tuple((r["day"], r["cycles"], r["money"]) for r in rows))
        if signature == self._signature:
            return
        self._signature = signature
        self._draw_summary(summary, title)
        self._draw_days(rows)

    def _clear(self, card: Card) -> None:
        for child in card.body.winfo_children():
            child.destroy()

    def _draw_summary(self, summary: dict, title: str) -> None:
        self._clear(self.summary)
        body, fill = self.summary.body, self.summary.fill
        caption(body, f"Итого · {title}", fill).pack(anchor="w", pady=(0, 12))
        grid = tk.Frame(body, bg=fill)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        for col, text in ((1, "ШТ."), (2, "ДЕНЬГИ")):
            label(grid, text, size=8, color="faint", font=FONT_SEMI, bg=fill).grid(row=0, column=col, sticky="e", padx=(30, 0))
        row = 1
        label(grid, "Циклов", size=11, bg=fill).grid(row=row, column=0, sticky="w", pady=6)
        label(grid, str(summary["cycles"]), size=11, font=FONT_SEMI, bg=fill).grid(row=row, column=1, sticky="e", padx=(30, 0))
        if summary.get("reconnects"):
            row += 1
            label(grid, "Реконнектов", size=11, bg=fill).grid(row=row, column=0, sticky="w", pady=6)
            label(grid, str(summary["reconnects"]), size=11, font=FONT_SEMI, bg=fill).grid(row=row, column=1, sticky="e", padx=(30, 0))
        for kind in KINDS:
            row += 1
            name, color = NAMES[kind]
            cell = tk.Frame(grid, bg=fill)
            cell.grid(row=row, column=0, sticky="w", pady=6)
            tk.Frame(cell, width=3, height=16, bg=color).pack(side="left", padx=(0, 9))
            label(cell, name, size=11, bg=fill).pack(side="left")
            count = summary["loot"][kind]
            label(grid, str(count), size=11, font=FONT_SEMI, bg=fill).grid(row=row, column=1, sticky="e", padx=(30, 0))
            label(grid, format_money(count * PRICES[kind]), size=11, bg=fill, color="muted").grid(row=row, column=2, sticky="e", padx=(30, 0))
        row += 1
        divider(grid).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        label(grid, "Всего", size=11, color="muted", bg=fill).grid(row=row, column=0, sticky="w")
        label(grid, str(sum(summary["loot"].values())), size=11, font=FONT_SEMI, bg=fill).grid(row=row, column=1, sticky="e", padx=(30, 0))
        label(grid, format_money(summary["money"]), size=14, font=FONT_SEMI, color="green", bg=fill).grid(row=row, column=2, sticky="e", padx=(30, 0))

    def _draw_days(self, rows: list[dict]) -> None:
        self._clear(self.days)
        body, fill = self.days.body, self.days.fill
        caption(body, "По дням", fill).pack(anchor="w", pady=(0, 12))
        if not rows:
            label(body, "Пока нет данных за этот период.", size=10, color="muted", bg=fill).pack(anchor="w")
            return
        grid = tk.Frame(body, bg=fill)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        headers = ["ДАТА", "ЦИКЛЫ", "ЖЁЛТ.", "ФИОЛ.", "ЗЕЛ.", "ДЕНЬГИ"]
        for col, text in enumerate(headers):
            label(grid, text, size=8, color="faint", font=FONT_SEMI, bg=fill).grid(row=0, column=col, sticky="w" if col == 0 else "e", padx=(22, 0) if col else 0)
        for index, row in enumerate(rows[:31], start=1):
            year, month, day = row["day"].split("-")
            values = [f"{day}.{month}.{year}", str(row["cycles"])] + [str(row["loot"][k]) for k in KINDS] + [format_money(row["money"])]
            for col, value in enumerate(values):
                label(grid, value, size=10, bg=fill, color="text" if col in (0, 5) else "muted",
                      font=FONT_SEMI if col == 5 else "Segoe UI").grid(row=index, column=col, sticky="w" if col == 0 else "e", padx=(22, 0) if col else 0, pady=5)
