"""The «Статистика» tab: the state (server) you play on now, its seven days, what you earned, your own numbers, the
court sentence of the wipe, and the states before it to compare with. Drawn in the look of ROB RUN (as the autocop)."""

from __future__ import annotations

import json
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

from ..overlay_bridge import OVERLAY_DIR, format_money
from ..seasons import SeasonStore
from ..stats import StatsStore
from .theme import COLORS, ScrollPage

LIME, INK, STRIP, EDGE, PANEL = "#c6fb33", "#141414", "#1b1b1a", "#2c2c2a", "#0f0f0e"
TEXT, MUTED, DIM = "#e9e9e4", "#8d8d86", "#5f5f59"
AMBER, BLUE, RED = "#e9b93a", "#6cc4ff", "#ff7a70"
PAPER, PAPER_INK, PAPER_MUTED = "#ecebe6", "#141414", "#5a5a55"
KNOCK, DEATH = "#d3b2ff", "#ff927c"
HEAD, BODY = "Bahnschrift SemiBold Condensed", "Bahnschrift Condensed"
PACKS = {"yellow": ("Жёлтые пакеты", "#f5c542"), "purple": ("Фиолетовые", "#b06cf0"), "green": ("Зелёные", "#46c46a")}


def hours(seconds: float) -> str:
    minutes = int(seconds) // 60
    h, m = divmod(minutes, 60)
    return f"{h} ч {m:02d} м" if h else f"{m} м"


def countdown(seconds: float) -> str:
    total = int(seconds) // 60
    d, rest = divmod(total, 1440)
    h, m = divmod(rest, 60)
    return f"{d} д {h:02d} ч" if d else f"{h} ч {m:02d} м"


def years_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "ГОД"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "ГОДА"
    return "ЛЕТ"


def delta(now: float | None, before: float | None, *, money: bool = False, fewer_is_better: bool = False,
          unit: str = "") -> tuple[str, str]:
    """▲/▼ against the previous state, and its colour."""
    if now is None or before is None:
        return "", MUTED
    diff = now - before
    if abs(diff) < 1e-9:
        return "= как в прошлом", MUTED
    good = (diff < 0) if fewer_is_better else (diff > 0)
    arrow = "▲" if diff > 0 else "▼"
    if money and before:
        text = f"{arrow} {abs(diff) / before * 100:.0f}% к прошлому"
    elif isinstance(now, float) and not unit:
        text = f"{arrow} {abs(diff):.1f}"
    else:
        text = f"{arrow} {abs(int(diff))}{unit}"
    return text, LIME if good else RED


class Box(tk.Frame):
    def __init__(self, parent: tk.Misc, *, bg: str = STRIP, edge: str = EDGE) -> None:
        super().__init__(parent, bg=bg, highlightthickness=1, highlightbackground=edge)


def small(parent: tk.Misc, text: str, bg: str = STRIP, color: str = MUTED, size: int = 9) -> tk.Label:
    return tk.Label(parent, text=text.upper(), bg=bg, fg=color, font=(BODY, size))


def button(parent: tk.Misc, text: str, command, *, primary: bool = False, bg: str = STRIP) -> tk.Label:
    widget = tk.Label(parent, text=text.upper(), bg=LIME if primary else STRIP, fg=INK if primary else TEXT,
                      font=(HEAD, 11), padx=12, pady=4, cursor="hand2",
                      highlightthickness=1, highlightbackground=LIME if primary else "#3a3a37")
    widget.bind("<ButtonPress-1>", lambda _e: command())
    return widget


class Dialog(tk.Frame):
    """A pop-up card inside the app window (not a window of its own): fields, then «Сохранить»."""

    def __init__(self, parent: tk.Misc, title: str, fields: list[tuple[str, str, str]], on_save) -> None:
        host = parent.winfo_toplevel()
        super().__init__(host, bg=STRIP, highlightthickness=2, highlightbackground=LIME)
        self._on_save = on_save
        head = tk.Frame(self, bg=STRIP)
        head.pack(fill="x")
        tk.Label(head, text=title.upper(), bg=LIME, fg=INK, font=(HEAD, 15), padx=14, pady=4).pack(side="left")
        close = tk.Label(head, text="✕", bg=STRIP, fg=MUTED, font=(BODY, 14), padx=10, cursor="hand2")
        close.pack(side="right")
        close.bind("<ButtonPress-1>", lambda _e: self.destroy())
        body = tk.Frame(self, bg=STRIP)
        body.pack(fill="both", padx=18, pady=14)
        self.vars: dict[str, tk.StringVar] = {}
        entries = []
        for key, label, value in fields:
            small(body, label).pack(anchor="w", pady=(6, 2))
            var = tk.StringVar(value=value)
            entry = tk.Entry(body, textvariable=var, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                             font=(HEAD, 14), width=30, highlightthickness=1, highlightbackground=EDGE, highlightcolor=LIME)
            entry.pack(fill="x", ipady=4)
            entry.bind("<Return>", lambda _e: self._save())
            entry.bind("<Escape>", lambda _e: self.destroy())
            entries.append(entry)
            self.vars[key] = var
        self.error = tk.Label(body, text="", bg=STRIP, fg=RED, font=(BODY, 10), justify="left", wraplength=320)
        self.error.pack(anchor="w", pady=(8, 0))
        row = tk.Frame(body, bg=STRIP)
        row.pack(fill="x", pady=(10, 0))
        button(row, "Сохранить", self._save, primary=True).pack(side="left")
        button(row, "Отмена", self.destroy).pack(side="left", padx=(8, 0))
        self.place(relx=0.5, rely=0.42, anchor="center")
        self.lift()
        self.grab_set()           # the rest of the app waits until it is closed
        if entries:
            entries[0].focus_set()
            entries[0].icursor("end")

    def _save(self) -> None:
        problem = self._on_save({key: var.get().strip() for key, var in self.vars.items()})
        if problem:
            self.error.configure(text=problem)
        else:
            self.destroy()


def parse_when(text: str) -> float | None:
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m %H:%M", "%d.%m.%Y"):
        try:
            moment = datetime.strptime(text, fmt)
            if fmt == "%d.%m %H:%M":
                moment = moment.replace(year=datetime.now().year)
            return moment.timestamp()
        except ValueError:
            continue
    return None


class StatsPage(tk.Frame):
    def __init__(self, parent: tk.Misc, stats: StatsStore, seasons: SeasonStore | None = None,
                 overlay_stats: Path | None = None) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.stats = stats
        self.seasons = seasons or SeasonStore(Path(stats.path).with_name("seasons.json"))
        self.overlay_stats = overlay_stats or OVERLAY_DIR / "stats.json"
        self.viewing: int | None = None     # a past season picked in the history; None: the current one
        scroll = ScrollPage(self, max_width=980)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        self.body = scroll.inner
        self.content = tk.Frame(self.body, bg=COLORS["bg"])
        self.content.pack(fill="x")
        self._signature: tuple | None = None
        self.refresh()
        self.after(self.EVERY_MS, self._tick)

    # ---- data ---------------------------------------------------------------------------------------------------

    def _combat_sessions(self) -> list[dict]:
        try:
            data = json.loads(Path(self.overlay_stats).read_text(encoding="utf-8"))
            return list((data.get("sessions") or {}).values())
        except (OSError, ValueError, AttributeError):
            return []

    def _summary(self, season: dict) -> dict:
        return SeasonStore.summary(season, list(self.stats.data.get("events", [])), self._combat_sessions())

    EVERY_MS = 15 * 60 * 1000   # the tab refreshes itself every 15 minutes, or with «Обновить»

    def update_now(self) -> None:
        self._signature = None
        self.refresh()

    def _tick(self) -> None:
        try:
            if self.winfo_viewable():
                self.refresh()
        finally:
            self.after(self.EVERY_MS, self._tick)

    # ---- drawing ------------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self.seasons.roll_over()
        history = self.seasons.history()
        current = self.seasons.current()
        season = self.seasons.get(self.viewing) if self.viewing is not None else current
        if season is None and history and self.viewing is None:
            season = None
        summary = self._summary(season) if season else None
        before = None
        if season:
            older = [s for s in history if s["started_at"] < season["started_at"]]
            before = self._summary(older[0]) if older else None
        # Rebuilt only when something shown changed (time in the game counts in minutes, the wipe timer in tens of
        # minutes); the new picture is put together first and swapped in at once, so nothing blinks.
        shown = dict(summary or {}, playtime=int((summary or {}).get("playtime", 0) // 60), left=int((summary or {}).get("left", 0) // 600),
                     progress=round((summary or {}).get("progress", 0), 2))
        signature = (json.dumps(shown, sort_keys=True, default=str), json.dumps(history, default=str), self.viewing)
        if signature == self._signature:
            return
        self._signature = signature
        old = self.content
        scroll_top = self.scroll.canvas.yview()[0] if hasattr(self, "scroll") else 0.0
        self.content = tk.Frame(self.body, bg=COLORS["bg"])
        root = self.content
        self._header(root, current)
        if season is None:
            self._empty(root)
        else:
            top = tk.Frame(root, bg=COLORS["bg"])
            top.pack(fill="x")
            top.grid_columnconfigure(0, weight=3, uniform="top")
            top.grid_columnconfigure(1, weight=2, uniform="top")
            self._state_card(top, season, summary, season is current).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            self._sentence_card(top, season, summary).grid(row=0, column=1, sticky="nsew")
            self._tiles(root, summary, before)
            lower = tk.Frame(root, bg=COLORS["bg"])
            lower.pack(fill="x", pady=(10, 0))
            lower.grid_columnconfigure(0, weight=1, uniform="lower")
            lower.grid_columnconfigure(1, weight=1, uniform="lower")
            self._earnings(lower, summary).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
            self._personal(lower, summary).grid(row=0, column=1, sticky="nsew")
        if history:
            self._history(root, history, season)
        tk.Frame(root, bg=COLORS["bg"], height=24).pack()
        self.content.pack(fill="x", before=old)
        old.destroy()
        if hasattr(self, "scroll"):
            self.after_idle(lambda: self.scroll.canvas.yview_moveto(scroll_top))

    def _header(self, root: tk.Misc, current: dict | None) -> None:
        bar = tk.Canvas(root, bg=COLORS["bg"], highlightthickness=0, height=40)
        bar.pack(fill="x", pady=(28, 14))
        bar.create_polygon(0, 0, 196, 0, 184, 40, 0, 40, fill=LIME, outline="")
        bar.create_text(14, 20, text="СТАТИСТИКА", anchor="w", fill=INK, font=(HEAD, 22))
        bar.create_text(208, 20, text="штат, заработок и личное за сезон", anchor="w", fill=MUTED, font=(BODY, 11))
        tools = tk.Frame(root, bg=COLORS["bg"])
        tools.place(in_=bar, relx=1.0, x=-2, rely=0.5, anchor="e")
        tk.Label(tools, text=time.strftime("обновлено %H:%M"), bg=COLORS["bg"], fg=MUTED, font=(BODY, 10)).pack(side="left", padx=(0, 8))
        button(tools, "Обновить", self.update_now).pack(side="left")
        if self.viewing is not None:
            button(tools, "← к актуальному штату", self._back).pack(side="left", padx=(6, 0))

    def _empty(self, root: tk.Misc) -> None:
        box = Box(root)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="x", padx=18, pady=16)
        tk.Label(inner, text="ШТАТ НЕ ВЫБРАН", bg=STRIP, fg=TEXT, font=(HEAD, 24)).pack(anchor="w")
        tk.Label(inner, text="Заведи штат, на котором играешь: номер EU-…, название и когда сервер стартовал. "
                             "Статистика будет собираться за этот штат до вайпа через 7 дней.",
                 bg=STRIP, fg=MUTED, font=(BODY, 11), wraplength=620, justify="left").pack(anchor="w", pady=(4, 10))
        button(inner, "Завести штат", self._new_state, primary=True).pack(anchor="w")

    def _state_card(self, parent: tk.Misc, season: dict, summary: dict, is_current: bool) -> tk.Widget:
        card = Box(parent)
        canvas = tk.Canvas(card, bg=STRIP, highlightthickness=0, height=168)
        canvas.pack(fill="both", expand=True)

        def draw(_event=None) -> None:
            canvas.delete("all")
            width = max(300, canvas.winfo_width())
            for offset in range(-200, 300, 22):           # the honeycomb lines of the game's state card
                canvas.create_line(width - 230 + offset, 0, width - 230 + offset + 80, 140, fill="#252a1a")
                canvas.create_line(width - 230 + offset + 80, 0, width - 230 + offset, 140, fill="#252a1a")
            canvas.create_text(16, 22, text="ШТАТ", anchor="w", fill=TEXT, font=(HEAD, 15))
            canvas.create_text(66, 22, text=season["code"], anchor="w", fill=LIME, font=(HEAD, 15))
            if is_current:
                tag = canvas.create_text(width - 18, 22, text="АКТУАЛЬНЫЙ", anchor="e", fill=INK, font=(HEAD, 10))
                x0, y0, x1, y1 = canvas.bbox(tag)
                canvas.tag_raise(tag, canvas.create_rectangle(x0 - 7, y0 - 2, x1 + 7, y1 + 2, fill=LIME, outline=""))
            elif season.get("early"):
                canvas.create_text(width - 18, 22, text="ВАЙП РАНЬШЕ", anchor="e", fill=AMBER, font=(HEAD, 10))
            canvas.create_text(14, 62, text=season["name"] or "—", anchor="w", fill="#ffffff", font=(HEAD, 38))
            started = ("хх" + time.strftime(".%m", time.localtime(season["started_at"]))) if season.get("start_unknown") \
                else time.strftime("%d.%m · %H:%M", time.localtime(season["started_at"]))
            canvas.create_text(16, 100, text=f"СТАРТ {started}" + (" · по меню игры" if season.get("auto_start") else ""),
                               anchor="w", fill=MUTED, font=(BODY, 10))
            if is_current:
                right_text, color = f"ВАЙП ЧЕРЕЗ {countdown(summary['left']).upper()}", AMBER
            else:
                end = time.strftime("%d.%m", time.localtime(season.get("ended_at") or summary["wipe_at"]))
                right_text, color = f"ЗАКОНЧЕН {end}", MUTED
            canvas.create_text(width - 16, 100, text=right_text, anchor="e", fill=color, font=(BODY, 10))
            canvas.create_rectangle(16, 110, width - 16, 114, fill=PANEL, outline="")
            canvas.create_rectangle(16, 110, 16 + (width - 32) * summary["progress"], 114, fill=LIME if is_current else DIM, outline="")

        canvas.bind("<Configure>", draw)
        row = tk.Frame(card, bg=STRIP)
        row.place(in_=canvas, x=16, y=128)
        if is_current:
            button(row, "Изменить", lambda: self._edit_state(season)).pack(side="left")
            button(row, "Вайп раньше / новый штат", self._new_state).pack(side="left", padx=(6, 0))
        else:
            button(row, "Изменить", lambda: self._edit_state(season)).pack(side="left")
        return card

    def _sentence_card(self, parent: tk.Misc, season: dict, summary: dict) -> tk.Widget:
        card = Box(parent, bg=PAPER, edge=PAPER)
        inner = tk.Frame(card, bg=PAPER)
        inner.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(inner, text="ПРИГОВОР ЗА СЕЗОН", bg=PAPER, fg=PAPER_INK, font=(HEAD, 18)).pack()
        tk.Label(inner, text=f"ОБВИНЯЕМЫЙ: ВЫ · ШТАТ: {season['code']}", bg=PAPER, fg=PAPER_MUTED, font=(BODY, 9)).pack(anchor="w", pady=(4, 6))

        def line(left: str, right: str) -> None:
            row = tk.Frame(inner, bg=PAPER)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=left, bg=PAPER, fg=PAPER_INK, font=(BODY, 11)).pack(side="left")
            tk.Label(row, text=right, bg=PAPER, fg=PAPER_INK, font=(BODY, 11)).pack(side="right")
            tk.Frame(inner, bg="#b9b8b2", height=1).pack(fill="x")

        kills = summary["kills"]
        line(f"- Убийства ({kills} человек)" if kills is not None else "- Убийства", "Доказано" if kills is not None else "внесёшь при вайпе")
        line("- Вас арестовали", f"{summary['arrests']} раз")
        bottom = tk.Frame(inner, bg=PAPER)
        bottom.pack(fill="x", pady=(8, 0))
        left = tk.Frame(bottom, bg=PAPER)
        left.pack(side="left")
        tk.Label(left, text="ИТОГОВЫЙ СРОК", bg=PAPER, fg=PAPER_MUTED, font=(BODY, 9)).pack(anchor="w")
        years = summary["years"]
        tk.Label(left, text=f"{years} {years_word(int(years))}" if years is not None else "—", bg=PAPER, fg=PAPER_INK,
                 font=(HEAD, 28)).pack(anchor="w")
        button(bottom, "Внести приговор", lambda: self._sentence(season), primary=True).pack(side="right", anchor="s")
        return card

    def _tiles(self, root: tk.Misc, summary: dict, before: dict | None) -> None:
        row = tk.Frame(root, bg=COLORS["bg"])
        row.pack(fill="x", pady=(10, 0))
        prev = before or {}
        tiles = [
            ("заработок", format_money(summary["total"]), LIME, LIME,
             delta(summary["total"], prev.get("total"), money=True)),
            ("в игре", hours(summary["playtime"]), TEXT, None,
             delta(summary["playtime"] / 3600, prev.get("playtime", 0) / 3600 if before else None, unit=" ч")),
            ("ноки / смерти", f"{summary['kd']:.1f}" if summary["kd"] is not None else "—", KNOCK, "#a978ff",
             delta(summary["kd"], prev.get("kd"))),
            ("вас арестовали", str(summary["arrests"]), DEATH, "#ff7a5c",
             delta(summary["arrests"], prev.get("arrests"), fewer_is_better=True, unit=" раз")),
        ]
        for index, (name, value, color, bar, (change, change_color)) in enumerate(tiles):
            row.grid_columnconfigure(index, weight=1, uniform="tiles")
            tile = Box(row)
            tile.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            small(tile, name).pack(anchor="w", padx=12, pady=(10, 0))
            tk.Label(tile, text=value, bg=STRIP, fg=color, font=(HEAD, 26)).pack(anchor="w", padx=10)
            tk.Label(tile, text=change or " ", bg=STRIP, fg=change_color, font=(BODY, 10)).pack(anchor="w", padx=12, pady=(0, 8))
            if bar:
                tk.Frame(tile, bg=bar, height=2).pack(fill="x", side="bottom")

    def _earnings(self, parent: tk.Misc, summary: dict) -> tk.Widget:
        box = Box(parent)
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        rate = f" · {format_money(summary['per_hour'])} в час" if summary["per_hour"] else ""
        small(inner, "заработок" + rate).pack(anchor="w", pady=(0, 6))
        rows = [(PACKS[k][0], PACKS[k][1], f"× {summary['counts'][k]}", summary["packages"][k], TEXT) for k in ("yellow", "purple", "green")]
        rows.append(("Зарплата копа", "#3da9f5", f"· {summary['shifts']} смен", summary["salary"], BLUE))
        for name, color, note, amount, text_color in rows:
            line = tk.Frame(inner, bg=STRIP)
            line.pack(fill="x", pady=2)
            tk.Frame(line, bg=color, width=4, height=18).pack(side="left")
            tk.Label(line, text=name, bg=STRIP, fg=text_color, font=(BODY, 12)).pack(side="left", padx=(8, 4))
            tk.Label(line, text=note, bg=STRIP, fg=MUTED, font=(BODY, 11)).pack(side="left")
            tk.Label(line, text=format_money(amount), bg=STRIP, fg=text_color, font=(HEAD, 13)).pack(side="right")
        total = max(1, summary["total"])
        shares = tk.Canvas(inner, bg=STRIP, highlightthickness=0, height=10)
        shares.pack(fill="x", pady=(8, 0))

        def draw(_event=None) -> None:
            shares.delete("all")
            width, x = shares.winfo_width(), 0.0
            parts = [(summary["packages"][k], PACKS[k][1]) for k in ("yellow", "purple", "green")] + [(summary["salary"], "#3da9f5")]
            if summary["total"] <= 0:
                shares.create_rectangle(0, 0, width, 8, fill=PANEL, outline="")
                return
            for amount, color in parts:
                w = width * amount / total
                shares.create_rectangle(x, 0, x + w, 8, fill=color, outline="")
                x += w
        shares.bind("<Configure>", draw)
        return box

    def _personal(self, parent: tk.Misc, summary: dict) -> tk.Widget:
        box = Box(parent)
        inner = tk.Frame(box, bg=STRIP)
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        small(inner, "личное").pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(inner, bg=STRIP)
        grid.pack(fill="x")
        cells = [("убийства", "—" if summary["kills"] is None else str(summary["kills"]), TEXT, None),
                 ("ноки", str(summary["knocks"]), KNOCK, "#a978ff"),
                 ("смерти", str(summary["deaths"]), DEATH, "#ff7a5c"),
                 ("аресты", str(summary["arrests"]), TEXT, None)]
        for index, (name, value, color, bar) in enumerate(cells):
            grid.grid_columnconfigure(index, weight=1, uniform="cells")
            cell = tk.Frame(grid, bg=PANEL)
            cell.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 4, 0))
            small(cell, name, bg=PANEL).pack(anchor="w", padx=8, pady=(6, 0))
            tk.Label(cell, text=value, bg=PANEL, fg=color, font=(HEAD, 20)).pack(anchor="w", padx=7, pady=(0, 6))
            if bar:
                tk.Frame(cell, bg=bar, height=2).pack(fill="x", side="bottom")
        tk.Label(inner, text="Убийства и срок — из приговора при вайпе. Ноки, смерти и аресты считает оверлей, "
                             "время и зарплату копа — хаб, пока он открыт.",
                 bg=STRIP, fg=MUTED, font=(BODY, 10), wraplength=380, justify="left").pack(anchor="w", pady=(8, 0))
        return box

    def _history(self, root: tk.Misc, history: list[dict], shown: dict | None) -> None:
        box = Box(root)
        box.pack(fill="x", pady=(10, 0))
        head = tk.Frame(box, bg=STRIP)
        head.pack(fill="x", padx=10, pady=(8, 4))
        small(head, "история штатов").pack(side="left")
        tk.Label(head, text="нажми строку — вся вкладка покажет этот штат", bg=STRIP, fg=MUTED, font=(BODY, 10)).pack(side="right")
        columns = (110, 0, 90, 70, 50, 60)

        def row(values: list[tuple[str, str, tuple]], bg: str, season: dict | None) -> None:
            line = tk.Frame(box, bg=bg, cursor="hand2" if season else "arrow")
            line.pack(fill="x")
            tk.Frame(box, bg="#262624", height=1).pack(fill="x")
            for index, (text, color, font) in enumerate(values):
                cell = tk.Label(line, text=text, bg=bg, fg=color, font=font, anchor="w",
                                width=0 if columns[index] == 0 else columns[index] // 8)
                cell.pack(side="left", fill="x", expand=columns[index] == 0, padx=(10 if index == 0 else 4, 0), pady=6)
                if season:
                    cell.bind("<ButtonPress-1>", lambda _e, s=season: self._view(s))
            if season:
                line.bind("<ButtonPress-1>", lambda _e, s=season: self._view(s))

        header_font = (BODY, 9)
        row([("ШТАТ", MUTED, header_font), ("НАЗВАНИЕ · ДАТЫ", MUTED, header_font), ("ЗАРАБОТОК", MUTED, header_font),
             ("В ИГРЕ", MUTED, header_font), ("Н/С", MUTED, header_font), ("СРОК", MUTED, header_font)], STRIP, None)
        current = self.seasons.current()
        for season in history:
            summary = self._summary(season)
            start = ("хх" + time.strftime(".%m", time.localtime(season["started_at"]))) if season.get("start_unknown") \
                else time.strftime("%d.%m", time.localtime(season["started_at"]))
            end = time.strftime("%d.%m", time.localtime(season.get("ended_at") or summary["wipe_at"]))
            name = f"{season['name']} · {start}–{end}" + (" · вайп раньше" if season.get("early") else "")
            picked = shown is not None and season["id"] == shown["id"]
            row([(season["code"], LIME if current and season["id"] == current["id"] else TEXT, (HEAD, 13)),
                 (name, TEXT, (BODY, 11)),
                 (format_money(summary["total"]), TEXT, (HEAD, 12)),
                 (hours(summary["playtime"]), TEXT, (BODY, 11)),
                 (f"{summary['kd']:.1f}" if summary["kd"] is not None else "—", KNOCK, (BODY, 11)),
                 (f"{summary['years']} {years_word(int(summary['years'])).lower()}" if summary["years"] is not None else "—",
                  TEXT, (BODY, 11))],
                "#22251a" if picked else STRIP, season)

    # ---- actions ------------------------------------------------------------------------------------------------

    def _back(self) -> None:
        self.viewing = None
        self._signature = None
        self.refresh()

    def _view(self, season: dict) -> None:
        current = self.seasons.current()
        self.viewing = None if current and season["id"] == current["id"] else season["id"]
        self._signature = None
        self.refresh()

    def _new_state(self) -> None:
        seen = self.seasons.data.get("last_server_start")
        guess = seen if seen and time.time() - seen < 7 * 86400 else time.time()
        current = self.seasons.current()
        note = "Текущий штат закончится сейчас (вайп раньше)." if current else ""

        def save(values: dict) -> str | None:
            code, name = values["code"], values["name"]
            if not any(char.isdigit() for char in code):
                return "Номер штата: например EU-06242."
            if not name:
                return "Название штата: например ROCKY VALLEY."
            when = parse_when(values["start"]) if values["start"] else time.time()
            if when is None:
                return "Старт: ДД.ММ.ГГГГ ЧЧ:ММ, например 12.11.2026 18:40."
            self.seasons.start(code, name, when, auto_start=values["start"] == default_start)
            self.viewing = None
            self._signature = None
            self.refresh()
            return None

        default_start = time.strftime("%d.%m.%Y %H:%M", time.localtime(guess))
        dialog = Dialog(self, "Новый штат", [("code", "Штат (номер)", "EU-"), ("name", "Название", ""),
                                             ("start", "Старт сервера (оставь как есть — бот уточнит по меню игры)", default_start)], save)
        if note:
            tk.Label(dialog, text=note, bg=STRIP, fg=AMBER, font=(BODY, 10)).pack(anchor="w", padx=18, pady=(0, 12))

    def _edit_state(self, season: dict) -> None:
        start_text = time.strftime("%d.%m.%Y %H:%M", time.localtime(season["started_at"]))

        def save(values: dict) -> str | None:
            when = parse_when(values["start"])
            if when is None:
                return "Старт: ДД.ММ.ГГГГ ЧЧ:ММ."
            fields = {"code": values["code"], "name": values["name"]}
            if values["start"] != start_text:
                fields.update(started_at=when, auto_start=False)   # typed by hand: the menu does not move it any more
            self.seasons.update(season["id"], **fields)
            self._signature = None
            self.refresh()
            return None

        Dialog(self, "Штат", [("code", "Штат (номер)", season["code"]), ("name", "Название", season["name"]),
                              ("start", "Старт сервера", start_text)], save)

    def _sentence(self, season: dict) -> None:
        def save(values: dict) -> str | None:
            try:
                kills = int(values["kills"]) if values["kills"] else None
                years = int(values["years"].lower().replace("лет", "").replace("года", "").replace("год", "").strip()) if values["years"] else None
            except ValueError:
                return "Только числа: убийства (135) и срок в годах (20)."
            self.seasons.update(season["id"], kills=kills, years=years)
            self._signature = None
            self.refresh()
            return None

        Dialog(self, "Приговор", [("kills", "Убийства (человек) — из «Обвинения»", "" if season.get("kills") is None else str(season["kills"])),
                                  ("years", "Итоговый срок, лет", "" if season.get("years") is None else str(season["years"]))], save)
