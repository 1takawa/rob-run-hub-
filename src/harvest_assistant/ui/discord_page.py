"""«Discord RPC» (Tools): the Discord card switches. The block itself is built by the settings page (it keeps the
values the app saves); this page is only the place it stands."""

from __future__ import annotations

import tkinter as tk

from .theme import COLORS, ScrollPage, page_header


class DiscordPage(tk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        scroll = ScrollPage(self, max_width=720)
        scroll.pack(fill="both", expand=True)
        page_header(scroll.inner, "Discord RPC", "карточка активности").pack(fill="x", pady=(34, 22), padx=4)
        self.host = tk.Frame(scroll.inner, bg=COLORS["bg"])
        self.host.pack(fill="x")

    def refresh(self) -> None:
        """Nothing changes by itself here."""
