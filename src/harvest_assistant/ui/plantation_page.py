"""The «Плантация» tab: the tables and everything that is about the plantation only (the way to the tables, harvest,
timers, the eyes of the bot). Starting and stopping the work lives on the «Запуск» tab.

The page is only the frame: the tables card is put into `tables_host` by DashboardPage and the setting groups into
`settings_host` by SettingsPage, so the app keeps talking to those two as before."""

from __future__ import annotations

import tkinter as tk

from .theme import COLORS, ScrollPage, page_header


class PlantationPage(tk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        scroll = ScrollPage(self, max_width=760)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Плантация", "Столы и настройки сбора").pack(fill="x", pady=(34, 22), padx=4)
        self.tables_host = tk.Frame(body, bg=COLORS["bg"])
        self.tables_host.pack(fill="x", pady=(0, 26))
        self.settings_host = tk.Frame(body, bg=COLORS["bg"])
        self.settings_host.pack(fill="x")
        tk.Frame(body, height=34, bg=COLORS["bg"]).pack()
