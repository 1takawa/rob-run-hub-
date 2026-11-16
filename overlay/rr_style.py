"""The look of ROB RUN for the overlay blocks: colours, fonts and the slanted header tab.

Shared by the session card, the plantation, the laboratory and the bot's marks (the autocop panel has the same look,
see autocop_panel.py)."""

from __future__ import annotations

import tkinter as tk

PANEL = "#262624"
EDGE = "#3a3a37"
STRIP = "#1b1b1a"
TEXT = "#e9e9e4"
SOFT = "#c9c9c2"
MUTED = "#8d8d86"
DIM = "#77776f"
WHITE = "#ffffff"
KEY = "#d8d8d2"
INK = "#141414"
LIME = "#c6fb33"
GREEN = "#46c46a"
GREEN_INK = "#0d1f12"
AMBER = "#e9b93a"
AMBER_TEXT = "#f1cf74"
BLUE = "#3da9f5"
RED = "#ff7a70"

HEAD = "Bahnschrift SemiBold Condensed"
BODY = "Bahnschrift Condensed"
HEADER = 30


def font(family: str, size: float, scale: float) -> tuple:
    return family, max(6, int(round(size * scale)))


def header(canvas: tk.Canvas, width: int, scale: float, title: str, tab: str, ink: str, tab_width: float) -> None:
    """The black strip with the slanted colour tab and the name on it."""
    px = lambda value: int(round(value * scale))
    canvas.create_rectangle(0, 0, width, px(HEADER), fill=STRIP, outline="")
    canvas.create_polygon(0, 0, px(tab_width), 0, px(tab_width - 11), px(HEADER), 0, px(HEADER), fill=tab, outline="")
    canvas.create_text(px(10), px(HEADER / 2), text=title, anchor="w", fill=ink, font=font(HEAD, 15, scale))


def key(canvas: tk.Canvas, x_right: float, y: float, text: str, scale: float) -> float:
    """A key in a light square, right edge at `x_right`; returns its left edge."""
    px = lambda value: int(round(value * scale))
    label = canvas.create_text(x_right - px(4), y, text=text, anchor="e", fill=STRIP, font=font(HEAD, 10, scale))
    x0, _y0, x1, _y1 = canvas.bbox(label)
    box = canvas.create_rectangle(x0 - px(4), y - px(8), x1 + px(4), y + px(8), fill=KEY, outline="")
    canvas.tag_raise(label, box)
    return x0 - px(4)


def clock(seconds: float) -> str:
    """2 ч 14 м; 37 м."""
    minutes = max(0, int(seconds)) // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} м" if hours else f"{minutes} м"
