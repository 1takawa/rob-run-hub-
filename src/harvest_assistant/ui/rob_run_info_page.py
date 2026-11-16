"""Decoder calculator and editable, season-specific progression checklist."""

from __future__ import annotations

import json
import tkinter as tk
import time
from tkinter import messagebox, ttk
from typing import Callable

from ..progression_store import ProgressionStore
from ..rob_run_tools import DECODER_LABELS, STRUCTURES, normalize_goal, plan_totals, raid_defense
from .theme import COLORS, FONT_SEMI, Card, PillButton, ScrollPage, label, page_header


def _money(value: int) -> str:
    return f"${value:,}".replace(",", " ")


class ROBRunInfoPage(tk.Frame):
    def __init__(self, parent: tk.Misc, get_season: Callable[[], dict | None],
                 get_history: Callable[[], list[dict]], store: ProgressionStore) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.get_season = get_season
        self.get_history = get_history
        self.store = store
        self._season_id: int | None = None
        self._signature: str | None = None
        self._season_choice_initialized = False
        self._selected_season_id: int | None = None
        self._current_season_id: int | None = None
        self._season_choice_ids: dict[str, int | None] = {}
        self._structure_keys = list(STRUCTURES)
        self._structure_names = [str(STRUCTURES[key]["name"]) for key in self._structure_keys]

        scroll = ScrollPage(self, max_width=920)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "ROB RUN Info", "Помощник по рейду и развитию на сезон").pack(fill="x", pady=(34, 22), padx=4)

        raid = Card(body, padding=(24, 20), radius=18)
        raid.pack(fill="x", pady=(0, 18))
        label(raid.body, "КАЛЬКУЛЯТОР ДЕКОДЕРОВ", size=12, font=FONT_SEMI, color="muted", bg=raid.fill).pack(anchor="w")
        label(raid.body, "Выбери строение и укажи предохранители. Считаются все три качества декодера.",
              size=9, color="muted", bg=raid.fill, wraplength=780, justify="left").pack(anchor="w", pady=(5, 14))
        controls = tk.Frame(raid.body, bg=raid.fill)
        controls.pack(fill="x")
        label(controls, "Строение", size=9, color="muted", bg=raid.fill).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.structure_var = tk.StringVar(value=self._structure_names[0])
        self.structure_box = ttk.Combobox(controls, state="readonly", width=28, values=self._structure_names,
                                           textvariable=self.structure_var)
        self.structure_box.grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(5, 0))
        self.structure_box.bind("<<ComboboxSelected>>", lambda _event: self._calculate())

        self.fuse_vars = {key: tk.StringVar(value="0") for key in ("uncommon", "epic", "legendary")}
        names = (("uncommon", "Обычные фьюзы · 1 очко"), ("epic", "Эпические · 3 очка"),
                 ("legendary", "Легендарные · 5 очков"))
        for column, (key, title) in enumerate(names, start=1):
            group = tk.Frame(controls, bg=raid.fill)
            group.grid(row=0, column=column, sticky="w", padx=(0, 12))
            label(group, title, size=9, color="muted", bg=raid.fill).pack(anchor="w")
            entry = ttk.Entry(group, textvariable=self.fuse_vars[key], width=8)
            entry.pack(anchor="w", pady=(5, 0))
            entry.bind("<KeyRelease>", lambda _event: self._calculate())
            self.fuse_vars[key].trace_add("write", lambda *_args: self._calculate())

        self.raid_result = tk.StringVar(value="")
        label(raid.body, "", size=11, font=FONT_SEMI, bg=raid.fill, textvariable=self.raid_result,
              wraplength=800, justify="left").pack(anchor="w", pady=(16, 0))
        label(raid.body, "Расчёт по правилам ROB RUN Info: установка одного декодера занимает 12 секунд. Проверь правила игры перед рейдом.",
              size=8, color="muted", bg=raid.fill, wraplength=800, justify="left").pack(anchor="w", pady=(8, 0))

        plan = Card(body, padding=(24, 20), radius=18)
        plan.pack(fill="x", pady=(0, 18))
        top = tk.Frame(plan.body, bg=plan.fill)
        top.pack(fill="x")
        label(top, "ПЛАН РАЗВИТИЯ НА СЕЗОН", size=12, font=FONT_SEMI, color="muted", bg=plan.fill).pack(side="left")
        PillButton(top, "+ Добавить цель", self._add_goal, style="primary", padx=16, height=34, size=9).pack(side="right")
        season_row = tk.Frame(plan.body, bg=plan.fill)
        season_row.pack(fill="x", pady=(10, 0))
        label(season_row, "Штат плана", size=9, color="muted", bg=plan.fill).pack(side="left")
        self.season_choice_var = tk.StringVar(value="")
        self.season_choice_box = ttk.Combobox(season_row, state="readonly", width=42, textvariable=self.season_choice_var)
        self.season_choice_box.pack(side="right")
        self.season_choice_box.bind("<<ComboboxSelected>>", self._choose_season)
        self.season_caption = tk.StringVar(value="")
        label(plan.body, "", size=9, color="muted", bg=plan.fill, textvariable=self.season_caption).pack(anchor="w", pady=(6, 12))
        self.goals_host = tk.Frame(plan.body, bg=plan.fill)
        self.goals_host.pack(fill="x")
        self.total_caption = tk.StringVar(value="")
        self.total_label = label(plan.body, "", size=11, font=FONT_SEMI, bg=plan.fill, textvariable=self.total_caption)
        self.total_label.pack(anchor="w", pady=(14, 0))
        label(plan.body, "Цены и требования здесь редактируются под выбранный сервер. План хранится отдельно для каждого штата.",
              size=8, color="muted", bg=plan.fill, wraplength=800, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Frame(body, height=24, bg=COLORS["bg"]).pack()
        self._calculate()
        self.refresh()

    def _calculate(self) -> None:
        if not hasattr(self, "raid_result"):
            return
        try:
            key_index = self._structure_names.index(self.structure_var.get())
            fuses = {key: int(value.get() or "0") for key, value in self.fuse_vars.items()}
            result = raid_defense(self._structure_keys[key_index], fuses)
        except ValueError as exc:
            self.raid_result.set(str(exc))
            return
        lines = [f"Защита: {result['defense']} очков · ячеек с фьюзами: {result['fuse_count']}/{result['slots']}"]
        for rarity in ("uncommon", "epic", "legendary"):
            count = result["decoders"][rarity]
            seconds = result["install_seconds"][rarity]
            minutes, rest = divmod(seconds, 60)
            duration = f"{minutes} мин {rest} сек" if minutes else f"{rest} сек"
            lines.append(f"{DECODER_LABELS[rarity]}: {count} шт. · установка {duration}")
        self.raid_result.set("\n".join(lines))

    def _active_season(self) -> dict | None:
        season = self.get_season()
        return season if isinstance(season, dict) else None

    @staticmethod
    def _id(season: dict | None) -> int | None:
        try:
            return int(season["id"]) if season else None
        except (KeyError, TypeError, ValueError):
            return None

    def _choose_season(self, _event=None) -> None:
        self._selected_season_id = self._season_choice_ids.get(self.season_choice_var.get())
        self._season_choice_initialized = True
        self._signature = None
        self.refresh()

    def refresh(self) -> None:
        current = self._active_season()
        current_id = self._id(current)
        history = self.get_history()
        available = {self._id(item): item for item in history if self._id(item) is not None}
        if not self._season_choice_initialized:
            self._selected_season_id = current_id
            self._season_choice_initialized = True
        elif current_id != self._current_season_id and self._selected_season_id == self._current_season_id:
            self._selected_season_id = current_id
        self._current_season_id = current_id
        if self._selected_season_id is not None and self._selected_season_id not in available:
            self._selected_season_id = current_id
        season = available.get(self._selected_season_id) if self._selected_season_id is not None else None
        season_id = self._selected_season_id
        choices = {"Без штата": None}
        for season_item in history:
            season_item_id = self._id(season_item)
            if season_item_id is None:
                continue
            date = time.strftime("%d.%m.%y", time.localtime(season_item.get("started_at", 0)))
            current_tag = " · текущий" if season_item_id == current_id else ""
            caption = f"{season_item.get('code', 'EU-?????')} · {season_item.get('name', '')} · {date}{current_tag}"
            choices[caption] = season_item_id
        self._season_choice_ids = choices
        labels = list(choices)
        self.season_choice_box.configure(values=labels)
        selected_label = next((name for name, value in choices.items() if value == season_id), "Без штата")
        self.season_choice_var.set(selected_label)
        rows = self.store.rows_for_season(season_id)
        summary = plan_totals(rows)
        signature = json.dumps([season_id, season.get("code") if season else None,
                                season.get("name") if season else None, labels, rows],
                               ensure_ascii=False, sort_keys=True, default=str)
        if signature == self._signature:
            return
        self._signature = signature
        self._season_id = season_id
        if season:
            self.season_caption.set(f"План для {season.get('code', 'штат')} · {season.get('name', '')}")
        else:
            self.season_caption.set("Штат не выбран · пока используется общий план без привязки к сезону")
        for child in self.goals_host.winfo_children():
            child.destroy()
        if not rows:
            label(self.goals_host, "Целей пока нет. Добавь предмет или этап развития — хаб посчитает оставшиеся расходы.",
                  size=9, color="muted", bg=self.goals_host.cget("bg"), wraplength=780, justify="left").pack(anchor="w", pady=8)
        for row in rows:
            self._goal_row(row)
        self.total_caption.set(
            f"Исследования: {_money(summary['research'])}    Покупки: {_money(summary['purchase'])}    Всего: {_money(summary['total'])}"
        )

    def _goal_row(self, goal: dict) -> None:
        row = tk.Frame(self.goals_host, bg=COLORS["surface_alt"], padx=12, pady=9)
        row.pack(fill="x", pady=3)
        content = tk.Frame(row, bg=COLORS["surface_alt"])
        content.pack(side="left", fill="x", expand=True)
        title = goal["title"] + (" · готово" if goal["researched"] and goal["owned"] >= goal["desired"] else "")
        label(content, title, size=10, font=FONT_SEMI, bg=content.cget("bg")).pack(anchor="w")
        remaining = max(0, goal["desired"] - goal["owned"])
        notes = [f"Есть {goal['owned']} / нужно {goal['desired']}"]
        if goal["tier"]:
            notes.append(f"уровень {goal['tier']}")
        if goal["sentence_years"]:
            notes.append(f"срок {goal['sentence_years']} лет")
        remaining_cost = (0 if goal["researched"] else goal["research_cost"]) + remaining * goal["unit_price"]
        notes.append(f"осталось {_money(remaining_cost)}")
        if goal["note"]:
            notes.append(goal["note"])
        label(content, " · ".join(notes), size=8, color="muted", bg=content.cget("bg"), wraplength=560,
              justify="left").pack(anchor="w", pady=(3, 0))
        actions = tk.Frame(row, bg=COLORS["surface_alt"])
        actions.pack(side="right", padx=(10, 0))
        PillButton(actions, "Изменить", lambda item=goal: self._edit_goal(item), style="ghost", padx=9, height=28, size=8).pack(side="left")
        PillButton(actions, "Удалить", lambda item=goal: self._delete_goal(item), style="ghost", padx=9, height=28, size=8).pack(side="left", padx=(2, 0))

    def _add_goal(self) -> None:
        self._goal_dialog(None)

    def _edit_goal(self, goal: dict) -> None:
        self._goal_dialog(goal)

    def _delete_goal(self, goal: dict) -> None:
        if messagebox.askyesno("Удалить цель", f"Убрать «{goal['title']}» из плана?", parent=self):
            self.store.remove(self._season_id, goal["id"])
            self._signature = None
            self.refresh()

    def _goal_dialog(self, initial: dict | None) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Цель сезона")
        dialog.transient(self.winfo_toplevel())
        dialog.configure(bg=COLORS["bg"])
        dialog.resizable(False, False)
        box = Card(dialog, padding=(22, 18), radius=18)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        label(box.body, "ЦЕЛЬ РАЗВИТИЯ", size=12, font=FONT_SEMI, color="muted", bg=box.fill).pack(anchor="w", pady=(0, 12))

        fields = (
            ("title", "Название", ""),
            ("desired", "Нужно штук", "0"),
            ("owned", "Уже есть", "0"),
            ("research_cost", "Цена исследования · разово", "0"),
            ("unit_price", "Цена покупки · за штуку", "0"),
            ("tier", "Уровень торговца", ""),
            ("sentence_years", "Требуемый срок · лет", "0"),
            ("note", "Заметка", ""),
        )
        variables: dict[str, tk.StringVar] = {}
        for key, title, default in fields:
            line = tk.Frame(box.body, bg=box.fill)
            line.pack(fill="x", pady=3)
            label(line, title, size=9, color="muted", bg=box.fill).pack(side="left")
            variable = tk.StringVar(value=str((initial or {}).get(key, default)))
            variables[key] = variable
            ttk.Entry(line, textvariable=variable, width=34 if key in ("title", "note", "tier") else 14).pack(side="right")
        researched = tk.BooleanVar(value=bool((initial or {}).get("researched", False)))
        ttk.Checkbutton(box.body, text="Исследование уже открыто", variable=researched).pack(anchor="w", pady=(8, 10))

        def save() -> None:
            payload = {key: variable.get().strip() for key, variable in variables.items()}
            payload.update(researched=researched.get(), id=(initial or {}).get("id", ""))
            try:
                cleaned = normalize_goal(payload)
                if initial:
                    self.store.update(self._season_id, initial["id"], cleaned)
                else:
                    self.store.add(self._season_id, cleaned)
            except ValueError as exc:
                messagebox.showwarning("Проверь цель", str(exc), parent=dialog)
                return
            self._signature = None
            self.refresh()
            dialog.destroy()

        buttons = tk.Frame(box.body, bg=box.fill)
        buttons.pack(fill="x", pady=(8, 0))
        PillButton(buttons, "Сохранить", save, style="primary", padx=16, height=34, size=9).pack(side="right")
        PillButton(buttons, "Отмена", dialog.destroy, style="ghost", padx=14, height=34, size=9).pack(side="right", padx=(0, 8))
        dialog.grab_set()
        dialog.after(0, lambda: dialog.focus_force())
