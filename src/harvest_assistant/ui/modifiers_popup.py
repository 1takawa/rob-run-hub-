"""Small pop-up in the style of the in-game «Стол для рассады» panel: pick the table upgrades."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk

from ..models import MODIFIERS, TableProfile, effective_interval
from .theme import COLORS, FONT, FONT_SEMI, shape_image

ASSETS = Path(__file__).resolve().parents[1] / "assets"
QUALITY = {"green": COLORS["pkg_green"], "purple": COLORS["purple"], "legendary": COLORS["yellow"]}
_icons: dict[tuple[str, int], ImageTk.PhotoImage] = {}


def icon(key: str, size: int) -> ImageTk.PhotoImage | None:
    """Upgrade picture at the given pixel size (cached)."""
    if (key, size) not in _icons:
        path = ASSETS / f"mod_{key}.png"
        if not path.is_file():
            return None
        image = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        _icons[(key, size)] = ImageTk.PhotoImage(image)
    return _icons[(key, size)]


def display_font(size: int) -> tuple:
    """The game uses a condensed italic face; Bahnschrift is the closest one that ships with Windows."""
    families = set(tkfont.families())
    return ("Bahnschrift SemiBold Condensed" if "Bahnschrift SemiBold Condensed" in families else FONT_SEMI, size, "italic")


def format_duration(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


class ModifiersPopup(tk.Toplevel):
    TILE = 116

    def __init__(self, parent: tk.Misc, anchor: tk.Misc, table: TableProfile,
                 on_toggle: Callable[[str, list[str]], None]) -> None:
        super().__init__(parent)
        self.table = table
        self._on_toggle = on_toggle
        self._tiles: dict[str, tk.Canvas] = {}
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=COLORS["border"])
        body = tk.Frame(self, bg=COLORS["surface"], padx=22, pady=20)
        body.pack(padx=1, pady=1)

        tk.Label(body, text="СТОЛ ДЛЯ РАССАДЫ", bg=COLORS["surface"], fg=COLORS["text"], font=display_font(15)).pack(anchor="w")
        tk.Label(body, text=table.name, bg=COLORS["surface"], fg=COLORS["muted"], font=(FONT_SEMI, 9)).pack(anchor="w", pady=(2, 0))
        tk.Label(body, text="Ускорение процесса", bg=COLORS["surface"], fg=COLORS["muted"], font=(FONT_SEMI, 10)).pack(anchor="w", pady=(10, 10))

        row = tk.Frame(body, bg=COLORS["surface"])
        row.pack()
        for index, key in enumerate(MODIFIERS):
            tile = tk.Canvas(row, width=self.TILE, height=self.TILE + 26, bg=COLORS["surface"], highlightthickness=0, cursor="hand2")
            tile.grid(row=0, column=index, padx=(0 if index == 0 else 10, 0))
            tile.bind("<ButtonPress-1>", lambda _e, k=key: self._toggle(k))
            self._tiles[key] = tile
        self.summary = tk.Label(body, bg=COLORS["surface"], fg=COLORS["muted"], font=(FONT, 9), justify="left")
        self.summary.pack(anchor="w", pady=(14, 0))
        self._draw()

        self.update_idletasks()
        x = anchor.winfo_rootx() + anchor.winfo_width() - self.winfo_reqwidth()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 8
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{max(8, min(x, screen_w - self.winfo_reqwidth() - 8))}+{max(8, min(y, screen_h - self.winfo_reqheight() - 8))}")
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", self._focus_out)
        self.after(30, self.focus_force)

    # ------------------------------------------------------------------------------------

    def _toggle(self, key: str) -> None:
        current = list(self.table.modifiers)
        if key in current:
            current.remove(key)
        else:
            current.append(key)
        self._on_toggle(self.table.table_id, current)
        self._draw()

    def _draw(self) -> None:
        size = self.TILE
        for key, tile in self._tiles.items():
            name, quality, bonus = MODIFIERS[key]
            color = QUALITY[quality]
            on = key in self.table.modifiers
            tile.delete("all")
            tile._bg = shape_image(size, size, 14, "#2a2a2e", color if on else None, 2)
            tile.create_image(0, 0, anchor="nw", image=tile._bg)
            picture = icon(key, 76)
            if picture is not None:
                tile._pic = picture
                tile.create_image(size / 2, size / 2 - 4, image=picture)
            # quality stripe, like the coloured corner of an item in the game
            tile.create_rectangle(14, size - 9, size - 14, size - 6, fill=color, outline="")
            if on:
                tile.create_oval(size - 24, 8, size - 8, 24, fill=color, outline="")
                tile.create_text(size - 16, 16, text="✓", fill="#101010", font=(FONT_SEMI, 9))
            tile.create_text(size / 2, size + 12, text=f"+{round(bonus * 100)}%", fill=COLORS["text"] if on else COLORS["muted"],
                             font=(FONT_SEMI, 10))
        installed = [MODIFIERS[k][0] for k in self.table.modifiers if k in MODIFIERS]
        interval = effective_interval(self.table)
        self.summary.configure(
            text=(", ".join(installed) if installed else "Улучшений нет") + f"\nЦикл: {format_duration(interval)} вместо {format_duration(self.table.interval_seconds)}"
        )

    def _focus_out(self, _event: tk.Event) -> None:
        self.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self) -> None:
        try:
            focus = self.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is None or focus.winfo_toplevel() is not self:
            self.close()

    def close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass
