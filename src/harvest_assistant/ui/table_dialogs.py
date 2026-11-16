"""Compact themed dialogs for grow-table operations."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import apply_theme


class TableDialogs:
    @staticmethod
    def add(parent: tk.Misc, initial_name: str, initial_minutes: int) -> tuple[str, int] | None:
        dialog = _FormDialog(parent, "Новый стол", "Задай имя и время до ближайшего сбора.")
        name = dialog.add_entry("Название", initial_name)
        minutes = dialog.add_entry("Минут до сбора", str(initial_minutes), numeric=True)
        def read() -> tuple[str, int]:
            value = (name.get().strip(), int(minutes.get()))
            if not value[0]:
                raise ValueError("Укажи название стола.")
            if not 0 <= value[1] <= 240:
                raise ValueError("Время должно быть от 0 до 240 минут.")
            return value
        return dialog.run(read)  # type: ignore[return-value]

    @staticmethod
    def rename(parent: tk.Misc, current_name: str) -> str | None:
        dialog = _FormDialog(parent, "Переименовать стол", "Имя будет видно в списке и маршруте.")
        value = dialog.add_entry("Название", current_name)
        def read() -> str:
            result = value.get().strip()
            if not result:
                raise ValueError("Укажи название стола.")
            return result
        return dialog.run(read)  # type: ignore[return-value]

    @staticmethod
    def confirm_remove(parent: tk.Misc, table_name: str) -> bool:
        dialog = tk.Toplevel(parent)
        apply_theme(dialog)
        dialog.title("Удалить стол")
        dialog.resizable(False, False)
        dialog.transient(parent.winfo_toplevel())
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Удалить стол?", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text=f"Удалить «{table_name}»? Маршрут будет сброшен — его нужно записать заново.",
            wraplength=340,
        ).pack(anchor="w", pady=(9, 20))
        result = {"value": False}
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Отмена", style="Secondary.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Удалить", style="Danger.TButton", command=lambda: _accept(dialog, result)).pack(side="right", padx=(0, 8))
        _center(dialog, parent)
        dialog.grab_set()
        parent.wait_window(dialog)
        return result["value"]

    @staticmethod
    def set_initial_time(parent: tk.Misc, table_name: str, initial_minutes: int) -> int | None:
        dialog = _FormDialog(parent, "Задать время", f"Сколько осталось до сбора у «{table_name}»?")
        minutes = dialog.add_entry("Минут", str(initial_minutes), numeric=True)
        def read() -> int:
            value = int(minutes.get())
            if not 0 <= value <= 240:
                raise ValueError("Время должно быть от 0 до 240 минут.")
            return value
        return dialog.run(read)  # type: ignore[return-value]


class _FormDialog:
    def __init__(self, parent: tk.Misc, title: str, subtitle: str) -> None:
        self.parent = parent
        self.dialog = tk.Toplevel(parent)
        apply_theme(self.dialog)
        self.dialog.title(title)
        self.dialog.resizable(False, False)
        self.dialog.transient(parent.winfo_toplevel())
        self.values: list[tk.StringVar] = []
        self.result: object | None = None
        self.body = ttk.Frame(self.dialog, padding=20)
        self.body.pack(fill="both", expand=True)
        ttk.Label(self.body, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=subtitle, style="Muted.TLabel", wraplength=350).pack(anchor="w", pady=(5, 14))
        self.fields = ttk.Frame(self.body)
        self.fields.pack(fill="x")
        self.error = tk.StringVar(value="")
        ttk.Label(self.body, textvariable=self.error, foreground="#ee777d", wraplength=350).pack(anchor="w", pady=(6, 0))

    def add_entry(self, label: str, initial: str, numeric: bool = False) -> tk.StringVar:
        row = ttk.Frame(self.fields)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text=label, width=18).pack(side="left")
        variable = tk.StringVar(value=initial)
        entry = ttk.Entry(row, textvariable=variable, width=24)
        if numeric:
            entry.configure(validate="key", validatecommand=(self.dialog.register(lambda value: not value or value.isdigit()), "%P"))
        entry.pack(side="right", fill="x", expand=True)
        if not self.values:
            entry.focus_set()
        self.values.append(variable)
        return variable

    def run(self, read: object) -> object | None:
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="Отмена", style="Secondary.TButton", command=self.dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Сохранить", style="Primary.TButton", command=lambda: self._submit(read)).pack(side="right", padx=(0, 8))
        self.dialog.bind("<Return>", lambda _event: self._submit(read))
        _center(self.dialog, self.parent)
        self.dialog.grab_set()
        self.parent.wait_window(self.dialog)
        return self.result

    def _submit(self, read: object) -> None:
        try:
            value = read()  # type: ignore[operator]
        except (ValueError, TypeError) as exc:
            self.error.set(str(exc) or "Проверь введённые значения. Время должно быть от 0 до 240 минут.")
            return
        self.result = value
        self.dialog.destroy()


def _accept(dialog: tk.Toplevel, result: dict[str, bool]) -> None:
    result["value"] = True
    dialog.destroy()


def _center(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    dialog.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - dialog.winfo_width()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - dialog.winfo_height()) // 2)
    dialog.geometry(f"+{x}+{y}")
