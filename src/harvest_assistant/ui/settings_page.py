"""Settings: everything about the app itself, in one calm column."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from typing import Callable

import numpy as np
from PIL import Image, ImageTk

from ..loot import LootItem
from ..models import InteractionCalibration, Profile
from ..places import CHOICES as PLACE_CHOICES
from .theme import (COLORS, FONT_SEMI, Block, Card, HelpTip, PillButton, ScrollPage, Segmented, Toggle, label, page_header,
                    settings_row)

SWATCH = {
    "красный": "#ff6b72", "оранжевый": "#f08a24", "жёлтый": "#f5c542", "зелёный": "#46c46a", "голубой": "#4cc2e0",
    "синий": "#4a7bf0", "фиолетовый": "#b06cf0", "розовый": "#f06cb0", "белый": "#f5f5f7", "серый": "#8e8e93",
}


class SettingsPage(tk.Frame):
    HOTKEYS = (
        ("Отметить стол (при записи пути)", "mark_waypoint_hotkey"),
        ("Старт / пауза", "pause_hotkey"),
        ("Аварийный стоп", "stop_hotkey"),
        ("Автосбор вкл / выкл", "auto_hotkey"),
        ("Записать путь до столов", "approach_hotkey"),
        ("Пройти путь до столов", "visual_hotkey"),
        ("Прочитать пакеты", "loot_hotkey"),
    )

    def __init__(
        self,
        parent: tk.Misc,
        callbacks: dict[str, Callable[[], None]],
        profile: Profile,
        timer_pause_var: tk.BooleanVar,
        plantation_parent: tk.Misc | None = None,
    ) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.callbacks = callbacks
        self.profile = profile
        self.timer_pause_var = timer_pause_var
        self.window_title_var = tk.StringVar(value=profile.target_window_title_match)
        self.background_control_var = tk.BooleanVar(value=profile.background_control)
        self.clock_mode_var = tk.StringVar(value=profile.clock_mode)
        self.auto_var = tk.BooleanVar(value=profile.auto_collect)
        self.reconnect_var = tk.BooleanVar(value=profile.auto_reconnect)
        self.table_vision_var = tk.BooleanVar(value=profile.table_vision)
        self.vision_harvest_var = tk.BooleanVar(value=profile.vision_harvest)
        self.table_vision_note = tk.StringVar(value="")
        self.reconnect_note = tk.StringVar(value="")
        first = profile.tables[0].interval_seconds if profile.tables else 3600
        self.base_var = tk.StringVar(value=f"{first // 60}:{first % 60:02d}" if first % 60 else str(first // 60))
        self.base_note = tk.StringVar(value="")
        self.hotkey_vars = {attribute: tk.StringVar(value=getattr(profile, attribute)) for _, attribute in self.HOTKEYS}
        self.window_status = tk.StringVar(value="Окно не привязано")
        self.discord_status = tk.StringVar(value="Подключение к Discord…")
        self.discord_enabled_var = tk.BooleanVar(value=profile.discord_enabled)
        self.discord_show_server_var = tk.BooleanVar(value=profile.discord_show_server)
        self.discord_show_role_var = tk.BooleanVar(value=profile.discord_show_role)
        self.discord_show_party_var = tk.BooleanVar(value=profile.discord_show_party)
        self.discord_show_earnings_var = tk.BooleanVar(value=profile.discord_show_earnings)
        self.discord_show_hours_var = tk.BooleanVar(value=profile.discord_show_hours)
        self.place_var = tk.StringVar(value="")
        self.place_note = tk.StringVar(value="")
        self._place_code: str | None = None
        self.hotkey_help = tk.StringVar(value="")
        self.route_state = tk.StringVar(value="Маршрут не записан")
        self.route_detail = tk.StringVar(value="")
        self.waiting_state = tk.StringVar(value="")
        self.key_var = tk.StringVar(value=profile.interaction.key)
        self.wait_var = tk.StringVar(value=f"{profile.interaction.harvest_seconds:g}")
        self.collect_note = tk.StringVar(value="")
        self.loot_result = tk.StringVar(value="")
        self.totals_text = tk.StringVar(value="")
        self._preview: ImageTk.PhotoImage | None = None
        self._game_open = False

        scroll = ScrollPage(self, max_width=720)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Настройки", "Приложение" if plantation_parent is not None else "Приложение и плантация").pack(
            fill="x", pady=(34, 22), padx=4)
        farm = plantation_parent if plantation_parent is not None else body
        self._build_game(body)
        self._build_discord_status(body)
        self._build_route(farm)
        self._build_collect(farm)
        self._build_timers(farm)
        self._build_table_vision(farm)
        self._build_keys(body)
        tk.Frame(body, height=34, bg=COLORS["bg"]).pack()

        # The app talks to these two like to the pages they replaced.
        self._route_signature: tuple | None = None
        self._taught_count = -1
        self.render_taught()
        self.route = SimpleNamespace(refresh=self._refresh_route)
        self.collect = SimpleNamespace(refresh=self._refresh_collect, set_services=lambda *_: None, show_loot=self.show_loot)

    # ---- sections ----------------------------------------------------------------------

    TONES = {"Игра": "lime", "Путь до столов": "blue", "Сбор урожая": "amber", "Таймеры": "amber",
             "Зрение плантации": "green"}

    def _group(self, parent: tk.Frame, title: str) -> Block:
        """A block with the slanted tab of its name, coloured by what it is about (as the blocks of the overlay)."""
        tone = self.TONES.get(title, "violet" if "discord" in title.lower() else "grey")
        card = Block(parent, title, tone, padding=(16, 12))
        card.pack(fill="x", pady=(0, 14))
        return card

    def _build_game(self, body: tk.Frame) -> None:
        card = self._group(body, "Игра")
        control = settings_row(card, "Окно игры", "Часть заголовка. Проще всего: в игре нажми Ctrl+F10.", first=True)
        PillButton(control, "Применить", self.callbacks["apply_window"], style="secondary", padx=16, height=34, size=9).pack(side="right")
        ttk.Entry(control, textvariable=self.window_title_var, width=18).pack(side="right", padx=(0, 8))
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.window_status).pack(anchor="w", pady=(10, 0))
        control = settings_row(card, "Автореконнект")
        Toggle(control, self.reconnect_var, command=self.callbacks["toggle_reconnect"]).pack(side="right")
        HelpTip(control, "Если тебя выкинуло из игры в лобби, бот сам вернётся: подтвердит окно «Ранний доступ», "
                         "нажмёт RUN и дождётся появления персонажа. Вылет он узнаёт по журналу игры (Player.log).\n\n"
                         "После входа маршрут начнётся сначала, поэтому персонаж должен появляться там, "
                         "где записан старт.").pack(side="right", padx=(0, 12))
        control = settings_row(card, "Старт сервера", "Узнать, сколько живёт сервер, и выставить старт текущего штата.")
        PillButton(control, "Уточнить", self.callbacks["server_clock"], style="secondary", padx=16, height=34, size=9).pack(side="right")
        HelpTip(control, "Если игра закрыта — бот её запустит, если свёрнута — развернёт. По журналу игры (Player.log) он "
                         "видит, где ты: в игре — нажмёт Esc → «Выйти» → «В главное меню». В главном меню прочитает "
                         "«Время существования сервера» над кнопкой RUN, выставит старт штата (вайп через 7 дней) "
                         "и нажмёт RUN, чтобы вернуть тебя в игру.").pack(side="right", padx=(0, 12))
        self.server_clock_note = tk.StringVar(value="")
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.server_clock_note, justify="left",
              wraplength=560).pack(anchor="w", pady=(8, 0))

    def _build_discord_status(self, body: tk.Frame) -> None:
        card = self._group(body, "Discord Rich Presence")
        control = settings_row(card, "Показывать активность в Discord", "Выключение очищает карточку и закрывает соединение.", first=True)
        Toggle(control, self.discord_enabled_var, command=self._save_discord_settings).pack(side="right")
        label(card.body, "Статус игры и место показываются всегда. Поселения (участки) — «Локация скрыта»; "
              "координаты не передаются. Остальное включается отдельно.",
              size=9, color="muted", bg=card.fill, wraplength=560, justify="left").pack(anchor="w")
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.discord_status).pack(
            anchor="w", pady=(10, 0))
        # the place: the game writes a code of it to Player.log; its name is told here once
        control = settings_row(card, "Где ты сейчас", "Место берётся из журнала игры, экран не читается. Новое место "
                               "назови один раз — дальше трекер узнает его сам.")
        self.place_combo = ttk.Combobox(control, textvariable=self.place_var, values=list(PLACE_CHOICES.values()),
                                        state="disabled", width=16)
        self.place_combo.pack(side="right")
        self.place_combo.bind("<<ComboboxSelected>>", lambda _e: self._name_place())
        self.place_note_label = label(control, "", size=9, color="green", bg=card.fill, textvariable=self.place_note)
        self.place_note_label.pack(side="right", padx=(0, 10))
        options = (
            ("Полный штат и название", "Если выключить: EU-?????? / NA-?????? без названия штата.", self.discord_show_server_var),
            ("Роль персонажа", "Берётся автоматически из игры.", self.discord_show_role_var),
            ("Соло / пати", "Берётся автоматически из игры.", self.discord_show_party_var),
            ("Заработок за вайп", "Сумма за текущий сезон.", self.discord_show_earnings_var),
            ("Время за вайп", "Часы в текущем сезоне.", self.discord_show_hours_var),
        )
        for title, hint, variable in options:
            control = settings_row(card, title, hint)
            Toggle(control, variable, command=self._save_discord_settings).pack(side="right")

    def _save_discord_settings(self) -> None:
        callback = self.callbacks.get("discord_settings_changed")
        if callback is not None:
            callback()

    def discord_settings(self) -> dict[str, bool]:
        return {
            "discord_enabled": self.discord_enabled_var.get(),
            "discord_show_server": self.discord_show_server_var.get(),
            "discord_show_role": self.discord_show_role_var.get(),
            "discord_show_party": self.discord_show_party_var.get(),
            "discord_show_earnings": self.discord_show_earnings_var.get(),
            "discord_show_hours": self.discord_show_hours_var.get(),
        }

    def set_discord_status(self, value: str) -> None:
        self.discord_status.set(value)

    def show_place(self, code: str | None, name: str | None) -> None:
        """``code`` None: a place that needs no name (``name`` says it). Otherwise ``name`` is the key or None."""
        self._place_code = code
        if code is None:
            self.place_note.set(name or "")
            self.place_note_label.configure(fg=COLORS["muted"])
            self.place_combo.configure(state="disabled")
            if self.place_var.get():
                self.place_var.set("")
            return
        self.place_note.set(f"{code} · НОВОЕ — НАЗОВИ" if name is None else code)
        self.place_note_label.configure(fg=COLORS["green"] if name is None else COLORS["muted"])
        self.place_combo.configure(state="readonly")
        if self.focus_get() is not self.place_combo:
            self.place_var.set(PLACE_CHOICES.get(name or "", ""))

    def _name_place(self) -> None:
        key = next((k for k, v in PLACE_CHOICES.items() if v == self.place_var.get()), None)
        callback = self.callbacks.get("place_named")
        if key and self._place_code and callback is not None:
            callback(self._place_code, key)
            self.place_note.set(self._place_code)
            self.place_note_label.configure(fg=COLORS["muted"])

    def _build_route(self, body: tk.Frame) -> None:
        card = self._group(body, "Путь до столов")
        label(card.body, "Встань в доме, нажми F2, дойди до плантации (у дверей жми E), пройди по рядам и у каждого стола "
              "нажми F6, потом F2 ещё раз. Один заход = один урок; несколько заходов из разных точек дома делают бота увереннее.",
              size=9, color="muted", bg=card.fill, wraplength=560, justify="left").pack(anchor="w", pady=(0, 8))
        record_row = tk.Frame(card.body, bg=card.fill)
        record_row.pack(fill="x")
        self.approach_button = PillButton(record_row, "Записать заход (F2)", self.callbacks["approach_record"], style="primary", padx=16, height=34, size=9)
        self.approach_button.pack(side="left")
        self.approach_note = tk.StringVar(value="")
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.approach_note, wraplength=560,
              justify="left").pack(anchor="w", pady=(6, 6))
        self.taught_rows = tk.Frame(card.body, bg=card.fill)
        self.taught_rows.pack(fill="x", pady=(0, 14))
        label(card.body, "Пройти путь · зрительная проверка", size=11, bg=card.fill).pack(anchor="w")
        label(card.body, "От спавна в доме через две двери до комнаты со столами. Столы и таймеры тест не трогает.",
              size=9, color="muted", bg=card.fill, wraplength=560, justify="left").pack(anchor="w", pady=(4, 10))
        visual_row = tk.Frame(card.body, bg=card.fill)
        visual_row.pack(fill="x")
        self.visual_test_button = PillButton(visual_row, "Тестировать путь (F3)", self.callbacks["visual_test"],
                                             style="secondary", padx=16, height=34, size=9)
        self.visual_test_button.pack(side="left")
        PillButton(visual_row, "Остановить тест", self.callbacks["visual_stop"], style="ghost",
                   padx=12, height=34, size=9).pack(side="left", padx=(8, 0))

    def _build_collect(self, body: tk.Frame) -> None:
        card = self._group(body, "Сбор урожая")
        control = settings_row(card, "Клавиша сбора", first=True)
        ttk.Entry(control, textvariable=self.key_var, width=6).pack(side="right")
        control = settings_row(card, "Ждать список пакетов", "Максимум секунд после нажатия")
        PillButton(control, "Сохранить", self._save_collect, style="secondary", padx=14, height=32, size=9).pack(side="right")
        ttk.Entry(control, textvariable=self.wait_var, width=6).pack(side="right", padx=(0, 8))
        control = settings_row(card, "Проверка распознавания", "Сделай сбор вручную и сразу нажми кнопку или клавишу в игре.")
        self.read_button = PillButton(control, "Прочитать", self.callbacks["read_loot"], style="secondary", padx=16, height=34, size=9)
        self.read_button.pack(side="right")
        self.loot_rows = tk.Frame(card.body, bg=card.fill)
        self.loot_rows.pack(fill="x", pady=(10, 0))
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.loot_result, justify="left", wraplength=560).pack(anchor="w")
        self.preview = tk.Label(card.body, bg=card.fill)
        self.preview.pack(anchor="w", pady=(8, 0))
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.collect_note).pack(anchor="w", pady=(6, 0))

    def _build_timers(self, body: tk.Frame) -> None:
        card = self._group(body, "Таймеры")
        control = settings_row(card, "Как идёт отсчёт", first=True)
        Segmented(control, self.clock_mode_var, [("wall_clock", "Всегда"), ("game_open", "Пока игра открыта")]).pack()
        control = settings_row(card, "Время роста без улучшений", "Минуты (40) или мин:сек (37:30). Улучшения сокращают его сами.", first=False)
        PillButton(control, "Сохранить", self._save_base, style="secondary", padx=14, height=32, size=9).pack(side="right")
        ttk.Entry(control, textvariable=self.base_var, width=7).pack(side="right", padx=(0, 8))
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.base_note).pack(anchor="w", pady=(6, 0))
        control = settings_row(card, "Автосбор", "Когда таймер кончается, цикл начинается заново сам. Без него — один цикл.")
        Toggle(control, self.auto_var, command=self.callbacks["toggle_auto"]).pack()
        control = settings_row(card, "Приостановить рост", "Таймеры замрут, автосбор будет ждать")
        Toggle(control, self.timer_pause_var, command=self.callbacks["timer_pause"]).pack()

    def _build_table_vision(self, body: tk.Frame) -> None:
        card = self._group(body, "Зрение плантации")
        control = settings_row(
            card, "Видеть столы",
            "Оверлей обводит стол по значку над ним: листок с часами, значит растёт, листок с зелёной галочкой, значит готов. "
            "После каждого сбора по маршруту хаб запоминает, как выглядит этот стол, и потом подписывает его именем и своим таймером. "
            "Работает, когда игра в фокусе или включено фоновое управление, и автопилот не идёт.",
            first=True,
        )
        Toggle(control, self.table_vision_var, command=self.callbacks["toggle_table_vision"]).pack()
        label(card.body, "", size=9, color="muted", bg=card.fill, textvariable=self.table_vision_note, justify="left",
              wraplength=560).pack(anchor="w", pady=(10, 0))

    def _build_keys(self, body: tk.Frame) -> None:
        from .dashboard_page import Collapsible, chevron

        head = tk.Frame(body, bg=COLORS["bg"], cursor="hand2")
        head.pack(fill="x", padx=6, pady=(0, 8))
        title = label(head, "ГОРЯЧИЕ КЛАВИШИ", size=13, font="Bahnschrift SemiBold Condensed", color="muted")
        title.pack(side="left")
        arrow = tk.Label(head, image=chevron(0), bg=COLORS["bg"], cursor="hand2")
        arrow.pack(side="left", padx=(6, 0))
        fold = Collapsible(body, COLORS["bg"], on_progress=lambda p: arrow.configure(image=chevron(p)))
        fold.pack(fill="x", pady=(0, 26))
        for widget in (head, title, arrow):
            widget.bind("<ButtonPress-1>", lambda _e: fold.toggle())
        self.keys_fold = fold
        card = Card(fold.inner, padding=(26, 20), radius=20)
        card.pack(fill="x")
        for index, (title, attribute) in enumerate(self.HOTKEYS):
            control = settings_row(card, title, first=index == 0)
            ttk.Entry(control, textvariable=self.hotkey_vars[attribute], width=12).pack()
        label(card.body, "WASD, E и Shift нужны боту в игре. Ctrl+F10 — привязка игры.",
              size=9, color="faint", bg=card.fill, justify="left", wraplength=560).pack(anchor="w", pady=(12, 0))
        row = tk.Frame(card.body, bg=card.fill)
        row.pack(fill="x", pady=(14, 0))
        PillButton(row, "Сохранить", self.callbacks["apply_settings"], style="primary", padx=26, height=40).pack(side="left")

    # ---- public API used by the app -----------------------------------------------------

    def _save_base(self) -> None:
        text = self.base_var.get().strip().replace(",", ".")
        try:
            if ":" in text:
                minutes, seconds = text.split(":")
                total = int(minutes) * 60 + int(seconds)
            else:
                total = round(float(text) * 60)
        except ValueError:
            self.base_note.set("Введи число минут, например 40 или 37:30")
            return
        if total < 60:
            self.base_note.set("Слишком мало: минимум 1 минута")
            return
        self.callbacks["set_base_interval"](total)
        self.base_note.set(f"Сохранено: {total // 60}:{total % 60:02d}. Уже идущие таймеры не менялись — поправь их кнопкой «Время».")

    def read_values(self) -> dict[str, str]:
        return {"clock_mode": self.clock_mode_var.get(), **{key: value.get().strip().lower() for key, value in self.hotkey_vars.items()}}

    def refresh(self, profile: Profile, window_status: str) -> None:
        self.profile = profile
        self.window_status.set(window_status)
        if self.background_control_var.get() != profile.background_control:
            self.background_control_var.set(profile.background_control)
        if self.timer_pause_var.get() != profile.manual_timer_paused:
            self.timer_pause_var.set(profile.manual_timer_paused)

    def _refresh_route(self, game_open: bool) -> None:
        count = max(0, self._taught_count)
        self.approach_note.set(
            f"Заходов обучения: {count}. F3 найдёт дорогу по ним." if count
            else "Заходов нет: запиши хотя бы один (F2), без них бот не найдёт дорогу."
        )
        self.visual_test_button.configure(state="normal" if game_open else "disabled")

    def update_hotkey_help(self, profile: Profile) -> None:
        self.hotkey_help.set("")

    # ---- route --------------------------------------------------------------------------

    def render_taught(self) -> None:
        for child in self.taught_rows.winfo_children():
            child.destroy()
        fill = self.taught_rows.cget("bg")
        runs = self.callbacks["taught_list"]()
        for run in runs:
            row = tk.Frame(self.taught_rows, bg=fill)
            row.pack(fill="x", pady=3)
            label(row, run.label, size=10, bg=fill).pack(side="left")
            label(row, f"{run.duration:.0f} с · кадров {len(run.keyframes)}", size=9, color="muted", bg=fill).pack(side="left", padx=(10, 0))
            PillButton(row, "×", lambda r=run.run_id: self.callbacks["taught_delete"](r), style="ghost", padx=12, height=28, size=10).pack(side="right")
        self._taught_count = len(runs)

    # ---- collect ------------------------------------------------------------------------

    def _refresh_collect(self, automation_active: bool = False) -> None:
        interaction = self.profile.interaction
        if not self.key_var.get():
            self.key_var.set(interaction.key)

    def _save_collect(self) -> None:
        key = self.key_var.get().strip().lower()
        try:
            wait = float(self.wait_var.get().replace(",", "."))
        except ValueError:
            self.collect_note.set("Время ожидания — число, например 6")
            return
        if not key or wait <= 0:
            self.collect_note.set("Укажи клавишу и время больше нуля")
            return
        if not self.callbacks["before_edit"]():
            return
        current = self.profile.interaction
        self.profile.interaction = InteractionCalibration(
            key=key, mode=current.mode, hold_seconds=current.hold_seconds,
            progress_timeout_seconds=current.progress_timeout_seconds, harvest_seconds=wait,
        )
        self.callbacks["profile_changed"]()
        self.collect_note.set("Сохранено")

    def show_loot(self, items: list[LootItem], crop: np.ndarray | None, error: str | None = None) -> None:
        for child in self.loot_rows.winfo_children():
            child.destroy()
        if error:
            self.loot_result.set(f"Не удалось распознать: {error}")
        elif not items:
            self.loot_result.set("Список пакетов не найден. Нажми сразу после сбора, пока он на экране.")
        else:
            self.loot_result.set("")
            for item in items:
                self._loot_row(self.loot_rows, item.color, item.name, item.count)
        if crop is not None and getattr(crop, "size", 0):
            image = Image.fromarray(crop)
            image.thumbnail((360, 190))
            self._preview = ImageTk.PhotoImage(image)
            self.preview.configure(image=self._preview)
        else:
            self.preview.configure(image="")

    @staticmethod
    def _loot_row(parent: tk.Frame, color: str, name: str, count: int) -> None:
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=2)
        tk.Frame(row, width=4, height=16, bg=SWATCH.get(color, COLORS["muted"])).pack(side="left", padx=(0, 10))
        label(row, f"{count} ×  {name}", size=10, bg=bg).pack(side="left")
        label(row, color, size=9, color="muted", bg=bg).pack(side="right")
