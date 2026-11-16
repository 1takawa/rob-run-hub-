"""Plantation: next harvest on the left (money details fold out), compact table list on the right."""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from ..models import AUTOMATION_MODES, MODIFIERS, Profile, RunSnapshot, RunState, TableProfile, effective_interval, remaining_seconds
from ..overlay_bridge import PACKAGE_PRICES, format_money
from ..stats import StatsStore
from ..vision import VisionService
from .modifiers_popup import ModifiersPopup, icon
from .theme import COLORS, FONT, FONT_LIGHT, FONT_SEMI, Bar, Card, PillButton, ScrollPage, Segmented, caption, divider, label, page_header

KINDS = (
    ("yellow", COLORS["yellow"], "Жёлтые"),
    ("purple", COLORS["purple"], "Фиолетовые"),
    ("green", COLORS["pkg_green"], "Зелёные"),
)
_chevrons: dict[int, ImageTk.PhotoImage] = {}


def chevron(progress: float) -> ImageTk.PhotoImage:
    """Small arrow pointing down (0) that turns up (1)."""
    step = round(progress * 12)
    if step not in _chevrons:
        big = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.line([(26, 38), (48, 60), (70, 38)], fill=COLORS["muted"], width=8, joint="curve")
        for point in ((26, 38), (70, 38)):
            draw.ellipse((point[0] - 4, point[1] - 4, point[0] + 4, point[1] + 4), fill=COLORS["muted"])
        big = big.rotate(-180 * step / 12, resample=Image.BICUBIC)
        _chevrons[step] = ImageTk.PhotoImage(big.resize((24, 24), Image.LANCZOS))
    return _chevrons[step]


class Collapsible(tk.Frame):
    """Height-animated container; interruptible, so a second click reverses it mid-flight."""

    def __init__(self, parent: tk.Misc, bg: str, on_progress: Callable[[float], None] | None = None) -> None:
        super().__init__(parent, bg=bg, height=0)
        self.pack_propagate(False)
        self.inner = tk.Frame(self, bg=bg)
        self.inner.pack(fill="x", anchor="n")
        self.opened = False
        self._height = 0.0
        self._job: str | None = None
        self._on_progress = on_progress

    def toggle(self) -> None:
        self.set_open(not self.opened)

    def set_open(self, opened: bool) -> None:
        self.opened = opened
        if self._job is not None:
            self.after_cancel(self._job)  # continue from the current height
        self.update_idletasks()
        full = max(1, self.inner.winfo_reqheight())

        def step() -> None:
            target = full if self.opened else 0
            self._height += (target - self._height) * 0.3
            if abs(target - self._height) < 1:
                self._height = target
                self._job = None
            else:
                self._job = self.after(16, step)
            self.configure(height=int(self._height))
            if self._on_progress:
                self._on_progress(self._height / full)

        step()


class DashboardPage(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        callbacks: dict[str, Callable[[], None]],
        on_select_table: Callable[[str | None], None],
        profile: Profile,
        vision: VisionService,
        timer_pause_var: tk.BooleanVar,
        stats: StatsStore,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.stats = stats
        self.callbacks = callbacks
        self.on_select_table = on_select_table
        self.profile = profile
        self.vision = vision
        self._rows: dict[str, dict[str, object]] = {}
        self._selected: str | None = None
        self._controls_key: tuple | None = None

        scroll = ScrollPage(self, max_width=940)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Плантация").pack(fill="x", pady=(34, 20), padx=4)
        self.mode_var = tk.StringVar(value=profile.automation_mode)
        label(body, "Режим автоматизации", size=10, color="muted").pack(anchor="w", padx=4)
        Segmented(body, self.mode_var, list(AUTOMATION_MODES),
                  command=lambda: callbacks["set_mode"](self.mode_var.get())).pack(anchor="w", pady=(6, 18), padx=4)

        grid = tk.Frame(body, bg=COLORS["bg"])
        grid.pack(fill="x", pady=(0, 34))
        grid.columnconfigure(1, weight=1)

        # --- left: next harvest, one-line package counters, foldable money details, start
        left = Card(grid, padding=(28, 24), radius=22)
        left.grid(row=0, column=0, sticky="new", padx=(0, 16))
        inner = left.body
        caption(inner, "До готовности", left.fill).pack(anchor="w")
        self.next_name = tk.StringVar(value="Пока нет столов")
        self.next_time = tk.StringVar(value="— : —")
        self.timer_note = tk.StringVar(value="")
        self.lab_timer = tk.StringVar(value="")
        label(inner, "", bg=left.fill, size=11, color="muted", textvariable=self.next_name).pack(anchor="w", pady=(10, 0))
        self.timer_label = label(inner, "", bg=left.fill, size=40, font=FONT_LIGHT, textvariable=self.next_time)
        self.timer_label.pack(anchor="w")
        label(inner, "", bg=left.fill, size=9, color="amber", textvariable=self.timer_note).pack(anchor="w")
        label(inner, "", bg=left.fill, size=9, color="muted", textvariable=self.lab_timer).pack(anchor="w")

        strip = tk.Frame(inner, bg=left.fill)
        strip.pack(anchor="w", pady=(14, 0))
        self.count_labels: dict[str, tk.Label] = {}
        for index, (key, color, _name) in enumerate(KINDS):
            if index:
                tk.Frame(strip, width=1, height=20, bg="#34343a").pack(side="left", padx=14)
            tk.Frame(strip, width=3, height=20, bg=color).pack(side="left", padx=(0, 8))
            count = tk.Label(strip, text="0", bg=left.fill, fg=COLORS["text"], font=(FONT_SEMI, 14))
            count.pack(side="left")
            self.count_labels[key] = count

        self.arrow = tk.Label(inner, bg=left.fill, cursor="hand2", image=chevron(0))
        self.arrow.pack(pady=(8, 0))
        self.arrow.bind("<ButtonPress-1>", lambda _e: self.details.toggle())

        self.details = Collapsible(inner, left.fill, on_progress=self._on_fold)
        self.details.pack(fill="x")
        self.money_labels: dict[str, tk.Label] = {}
        self.piece_labels: dict[str, tk.Label] = {}
        box = self.details.inner
        for key, color, name in KINDS:
            row = tk.Frame(box, bg=left.fill)
            row.pack(fill="x", pady=4)
            tk.Frame(row, width=3, height=16, bg=color).pack(side="left", padx=(0, 9))
            label(row, name, size=10, bg=left.fill).pack(side="left")
            self.piece_labels[key] = label(row, "", size=9, color="faint", bg=left.fill)
            self.piece_labels[key].pack(side="left", padx=(6, 0))
            self.money_labels[key] = label(row, "$0", size=10, font=FONT_SEMI, bg=left.fill)
            self.money_labels[key].pack(side="right")
        divider(box).pack(fill="x", pady=(8, 8))
        total = tk.Frame(box, bg=left.fill)
        total.pack(fill="x", pady=(0, 6))
        label(total, "Всего", size=10, color="muted", bg=left.fill).pack(side="left")
        self.total_label = label(total, "$0", size=12, font=FONT_SEMI, color="green", bg=left.fill)
        self.total_label.pack(side="right")

        self.controls = tk.Frame(inner, bg=left.fill)
        self.controls.pack(fill="x", pady=(14, 0))
        self.start_button = PillButton(self.controls, "Старт", callbacks["start"], style="primary", padx=40, height=42, size=11)
        self.pause_button = PillButton(self.controls, "Пауза", callbacks["pause"], style="secondary", padx=24, height=42, size=11)
        self.stop_button = PillButton(self.controls, "Стоп", callbacks["stop"], style="ghost", padx=18, height=42, size=11)
        self.cycles_var = tk.StringVar(value="Циклов: 0")
        label(inner, "", size=10, color="muted", bg=left.fill, textvariable=self.cycles_var).pack(anchor="w", pady=(12, 0))
        self.hint = tk.StringVar(value="")
        label(inner, "", size=9, color="faint", bg=left.fill, textvariable=self.hint, justify="left", wraplength=250).pack(anchor="w", pady=(12, 0))

        # --- right: up to six tables, one compact line each
        right = Card(grid, padding=(14, 14), radius=22)
        right.grid(row=0, column=1, sticky="new")
        self.rows_card = right
        head = tk.Frame(right.body, bg=right.fill)
        head.pack(fill="x", padx=12, pady=(4, 8))
        label(head, "Столы", size=12, font=FONT_SEMI, bg=right.fill).pack(side="left")
        PillButton(head, "Добавить", callbacks["add"], style="ghost", padx=14, height=30, size=9).pack(side="right")
        self.rows_host = tk.Frame(right.body, bg=right.fill)
        self.rows_host.pack(fill="x")
        self.empty = label(self.rows_host, "Добавь первый стол — программа начнёт отсчёт.", size=10, color="muted", bg=right.fill)

    # ------------------------------------------------------------------------------------

    def _on_fold(self, progress: float) -> None:
        self.arrow.configure(image=chevron(progress))

    def refresh(
        self,
        snapshot: RunSnapshot,
        tables: list[TableProfile],
        selected_table_id: str | None,
        timer_paused: bool | None = None,
        game_open: bool = True,
    ) -> None:
        self._selected = selected_table_id
        now = time.time()

        counts = self.stats.session_counts()
        self.cycles_var.set(f"Циклов: {self.stats.session_cycles()}")
        grand_total = 0
        for key, _color, _name in KINDS:
            amount = counts[key] * PACKAGE_PRICES[key]
            grand_total += amount
            self.count_labels[key].configure(text=str(counts[key]))
            self.piece_labels[key].configure(text=f"× {counts[key]}")
            self.money_labels[key].configure(text=format_money(amount))
        self.total_label.configure(text=format_money(grand_total))

        statuses = {item.table_id: item for item in snapshot.table_statuses}
        ordered = sorted(tables, key=lambda item: item.route_index)
        wanted = [table.table_id for table in ordered]
        if list(self._rows) != wanted:
            for child in self.rows_host.winfo_children():
                if child is not self.empty:
                    child.destroy()
            self._rows.clear()
            for table in ordered:
                self._rows[table.table_id] = self._create_row(table)
        if ordered:
            self.empty.pack_forget()
        elif not self.empty.winfo_ismapped():
            self.empty.pack(pady=18)

        for table in ordered:
            row = self._rows[table.table_id]
            status = statuses.get(table.table_id)
            remaining = remaining_seconds(table, self.profile, now) if status is None else status.remaining_seconds
            has_point = any(wp.table_id == table.table_id for wp in self.profile.route.waypoints)
            ready = remaining == 0
            row["name"].configure(text=table.name, fg=COLORS["text"] if has_point else COLORS["amber"])
            row["time"].configure(text=self._format_time(remaining), fg=COLORS["green"] if ready else COLORS["muted"])
            interval = max(1, effective_interval(table))
            self._show_modifiers(row, table)
            row["bar"].set(0.0 if remaining is None else 1.0 - min(1.0, remaining / interval), ready)
            self._paint_row(row, table.table_id == selected_table_id)

        table_times = [remaining_seconds(table, self.profile, now) for table in ordered]
        farm_seconds = max(table_times) if table_times and None not in table_times else None
        lab_seconds = remaining_seconds(self.profile.laboratory.timer, self.profile, now)
        if self.profile.automation_mode == "both":
            self.next_name.set("До общего цикла")
            next_seconds = max(farm_seconds, lab_seconds) if farm_seconds is not None and lab_seconds is not None else None
            self.lab_timer.set(
                f"Плантация · {self._format_time(farm_seconds)}\nЛаборатория · {self._format_time(lab_seconds)}"
            )
        elif self.profile.runs_laboratory:
            self.next_name.set("Лаборатория")
            next_seconds = lab_seconds
            self.lab_timer.set("")
        else:
            self.next_name.set("Плантация" if ordered else "Пока нет столов")
            next_seconds = farm_seconds
            self.lab_timer.set("")
        self.next_time.set("Готово" if next_seconds == 0 else self._format_time(next_seconds))
        self.timer_label.configure(fg=COLORS["green"] if next_seconds == 0 else COLORS["text"])
        self.timer_note.set("Таймеры на паузе" if self.profile.manual_timer_paused else "")

        active = snapshot.state not in (RunState.STOPPED, RunState.ERROR)
        paused = snapshot.state == RunState.PAUSED
        session = self.stats.session_active()
        can_start = (bool(ordered) or not self.profile.runs_plantation) and game_open
        key = (active, paused, can_start, session)
        if key != self._controls_key:
            self._controls_key = key
            for button in (self.start_button, self.pause_button, self.stop_button):
                button.pack_forget()
            if not active:
                self.start_button.configure(text="Старт", state="normal" if can_start else "disabled")
                self.start_button.pack(side="left")
                if session:
                    self.stop_button.pack(side="left", padx=(6, 0))
            elif paused:
                self.start_button.configure(text="Продолжить", state="normal")
                self.start_button.pack(side="left")
                self.stop_button.pack(side="left", padx=(6, 0))
            else:
                self.pause_button.pack(side="left")
                self.stop_button.pack(side="left", padx=(6, 0))
        if self.profile.runs_plantation and not ordered:
            self.hint.set("Добавь стол, чтобы начать")
        elif not game_open:
            self.hint.set("Открой игру")
        elif active:
            self.hint.set(snapshot.reason)
        else:
            self.hint.set("Встань на общий старт и нажми «Старт»")

    # ------------------------------------------------------------------------------------

    def _create_row(self, table: TableProfile) -> dict[str, object]:
        fill = self.rows_card.fill
        frame = tk.Frame(self.rows_host, bg=fill, padx=12, pady=9, cursor="hand2")
        frame.pack(fill="x")
        line = tk.Frame(frame, bg=fill)
        line.pack(fill="x")
        name = tk.Label(line, text=table.name, bg=fill, fg=COLORS["text"], font=(FONT, 11), width=9, anchor="w")
        name.pack(side="left")
        time_label = tk.Label(line, text="—", bg=fill, fg=COLORS["muted"], font=(FONT_SEMI, 11), width=8, anchor="e")
        time_label.pack(side="right")
        menu = tk.Canvas(line, width=30, height=30, bg=fill, highlightthickness=0, cursor="hand2")
        menu.pack(side="right", padx=(4, 4))
        self._draw_menu_button(menu, False)
        menu.bind("<Enter>", lambda _e, c=menu: self._draw_menu_button(c, True))
        menu.bind("<Leave>", lambda _e, c=menu: self._draw_menu_button(c, False))
        menu.bind("<ButtonPress-1>", lambda _e, c=menu, t=table: self._open_modifiers(c, t))
        icons = tk.Frame(line, bg=fill, width=70, height=24)
        icons.pack(side="left", padx=(0, 0))
        icons.pack_propagate(False)
        bar = Bar(line, bg=fill)
        bar.pack(side="left", fill="x", expand=True, padx=(8, 10), pady=8)
        actions = tk.Frame(frame, bg=fill)
        for text, key, style in (("Время", "set_timer", "ghost"), ("Имя", "rename", "ghost"), ("Удалить", "remove", "danger")):
            PillButton(actions, text, self.callbacks[key], style=style, padx=12, height=28, size=9).pack(side="left", padx=(0, 4))
        for widget in (frame, line, name, time_label):
            widget.bind("<ButtonPress-1>", lambda _e, tid=table.table_id: self.on_select_table(tid))
        return {"frame": frame, "line": line, "name": name, "time": time_label, "bar": bar, "actions": actions, "selected": None,
                "icons": icons, "menu": menu, "mods": None, "table": table}

    def _paint_row(self, row: dict[str, object], selected: bool) -> None:
        if row["selected"] == selected:
            return
        row["selected"] = selected
        fill = COLORS["surface_hi"] if selected else self.rows_card.fill
        for key in ("frame", "line", "name", "time", "actions", "icons", "menu"):
            row[key].configure(bg=fill)
        for picture in row["icons"].winfo_children():
            picture.configure(bg=fill)
        row["bar"].configure(bg=fill)
        row["bar"]._draw()
        if selected:
            row["actions"].pack(fill="x", pady=(8, 0))
        else:
            row["actions"].pack_forget()

    def _show_modifiers(self, row: dict[str, object], table: TableProfile) -> None:
        """Tiny pictures of the upgrades installed on the table."""
        key = tuple(table.modifiers)
        if row["mods"] == key:
            return
        row["mods"] = key
        for child in row["icons"].winfo_children():
            child.destroy()
        fill = row["frame"].cget("bg")
        for name in table.modifiers:
            picture = icon(name, 20)
            if picture is not None:
                tk.Label(row["icons"], image=picture, bg=fill, bd=0).pack(side="left", padx=(0, 4))

    @staticmethod
    def _draw_menu_button(canvas: tk.Canvas, hover: bool) -> None:
        canvas.delete("all")
        if hover:
            canvas.create_oval(1, 1, 29, 29, fill=COLORS["surface_hi"] if canvas.cget("bg") == COLORS["surface"] else "#303036", outline="")
        for y in (9, 15, 21):
            canvas.create_oval(13, y - 1.6, 16.6, y + 2, fill=COLORS["muted"] if not hover else COLORS["text"], outline="")

    def _open_modifiers(self, anchor: tk.Misc, table: TableProfile) -> None:
        if getattr(self, "_popup", None) is not None:
            self._popup.close()
        self._popup = ModifiersPopup(self.winfo_toplevel(), anchor, table, lambda tid, mods: self.callbacks["set_modifiers"](tid, mods))

    @staticmethod
    def _format_time(seconds: int | None) -> str:
        if seconds is None:
            return "—"
        hours, remainder = divmod(max(0, seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
