"""The police-service helper panel («Автокоп»), drawn in the look of ROB RUN.

Harvest Assistant writes ``autocop_feed.json`` (see harvest_assistant/overlay_bridge.py): on/off, attempts, refusals,
the step of the cycle, the last answer of the game. This module only draws it on a tkinter canvas; main.py shows the
window over the game and calls `draw` when the feed changes and `animate` a few times a second.

Look: a slanted colour tab with the name (like the game's menu headers), black strips, keys in light squares, the
class with the colour of its rank (as on the loadout screen), a big condensed number and a running strip at the bottom.
"""

from __future__ import annotations

import time
import tkinter as tk

BASE_WIDTH = 300
BASE_HEIGHT = 196

PANEL = "#262624"
EDGE = "#3a3a37"
STRIP = "#1b1b1a"
TEXT = "#e9e9e4"
MUTED = "#8d8d86"
DIM = "#77776f"
WHITE = "#ffffff"
KEY = "#d8d8d2"

STATES = {   # tab colour, text on the tab, status word, status colour
    "run": ("#c6fb33", "#141414", "ВКЛ", "#c6fb33"),
    "wait": ("#e9b93a", "#141414", "ЖДЁТ", "#e9b93a"),
    "done": ("#3da9f5", "#0c1a26", "НА СЛУЖБЕ", "#6cc4ff"),
    "duty": ("#3da9f5", "#0c1a26", "КОП", "#6cc4ff"),
}
MESSAGES = {   # bar, text
    "refusal": ("#e2493f", "#ff7a70"),
    "wait": ("#e9b93a", "#f1cf74"),
    "done": ("#3da9f5", "#9ad6ff"),
    "": ("#3a3a37", MUTED),
}
RANKS = {   # the colour of the class on the loadout screen
    "Патрульный": "#3f9b4b", "Сержант I": "#3f9b4b", "Сержант II": "#3f9b4b",
    "Лейтенант I": "#8e4fc4", "Капитан": "#c9a227",
}
STEPS = (("service", "СЛУЖБА"), ("confirm", "ПОДТВЕРДИТЬ"), ("done", "НА СЛУЖБЕ"))

HEAD_FONT = "Bahnschrift SemiBold Condensed"
BODY_FONT = "Bahnschrift Condensed"


def elapsed(data: dict, now: float | None = None) -> int:
    started = float(data.get("started_at") or 0)
    if not started:
        return 0
    end = data.get("ended_at")
    return max(0, int((float(end) if end else (now or time.time())) - started))


def clock_parts(seconds: int) -> list[tuple[str, str]]:
    """24 м 19 с; 1 ч 02 м 05 с: (number, unit) pairs, hours only once there are any."""
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return [(str(hours), "ч"), (f"{minutes:02d}", "м"), (f"{secs:02d}", "с")]
    return [(str(minutes), "м"), (f"{secs:02d}", "с")]


def draw(canvas: tk.Canvas, data: dict, scale: float) -> dict:
    """The whole panel; returns the items `animate` and `tick_clock` move."""
    px = lambda value: int(round(value * scale))
    font = lambda family, size, *style: (family, max(6, px(size)), *style)
    width, height = px(BASE_WIDTH), px(BASE_HEIGHT)
    canvas.delete("all")
    canvas.configure(width=width, height=height)
    state = data.get("state") if data.get("state") in STATES else "run"
    tab, tab_text, word, word_color = STATES[state]
    items: dict = {"state": state, "width": width, "scale": scale}

    canvas.create_rectangle(0, 0, width - 1, height - 1, fill=PANEL, outline=EDGE)
    # header: a slanted tab, the status, the key that stops it
    canvas.create_rectangle(0, 0, width, px(34), fill=STRIP, outline="")
    canvas.create_polygon(0, 0, px(118), 0, px(106), px(34), 0, px(34), fill=tab, outline="")
    canvas.create_text(px(12), px(17), text="АВТОКОП", anchor="w", fill=tab_text, font=font(HEAD_FONT, 16))
    items["dot"] = canvas.create_oval(px(128), px(13), px(136), px(21), fill=word_color, outline="")
    items["dot_color"] = word_color
    canvas.create_text(px(142), px(17), text=word, anchor="w", fill=word_color, font=font(HEAD_FONT, 13))
    if state == "done":
        canvas.create_text(width - px(10), px(17), text="ГОТОВО", anchor="e", fill=MUTED, font=font(BODY_FONT, 9))
    else:
        hotkey = str(data.get("hotkey") or "").upper()
        canvas.create_text(width - px(10), px(17), text="СТОП", anchor="e", fill=MUTED, font=font(BODY_FONT, 9))
        if hotkey:
            label = canvas.create_text(width - px(44), px(17), text=hotkey, anchor="e", fill=STRIP, font=font(HEAD_FONT, 9))
            x0, y0, x1, y1 = canvas.bbox(label)
            box = canvas.create_rectangle(x0 - px(4), px(9), x1 + px(4), px(25), fill=KEY, outline="")
            canvas.tag_raise(label, box)

    # the class and what it costs
    cls = str(data.get("class") or "")
    canvas.create_rectangle(px(12), px(45), px(16), px(67), fill=RANKS.get(cls, "#3f9b4b"), outline="")
    canvas.create_text(px(24), px(56), text=cls.upper(), anchor="w", fill=TEXT, font=font(HEAD_FONT, 14))
    # the tokens you have for this class, big: «54», as read over its button on the loadout screen
    tokens = str(data.get("tokens") or "")
    have = tokens.split("/")[0] if tokens and tokens[0].isdigit() else ""
    if tokens == "бесплатно":
        value, color = "БЕСПЛАТНО", TEXT
    elif tokens == "0" or have == "0":
        value, color = "0", "#ff7a70"
    elif have:
        value, color = have, "#c6fb33"
    else:
        value, color = "—", DIM
    number = canvas.create_text(width - px(12), px(57), text=value, anchor="e", fill=color, font=font(HEAD_FONT, 16))
    canvas.create_text(canvas.bbox(number)[0] - px(6), px(58), text="ЖЕТОНЫ", anchor="e", fill=MUTED, font=font(BODY_FONT, 8))

    # attempts and time in the cycle
    duty = state == "duty"
    canvas.create_text(px(12), px(80), text={"done": "С ПОПЫТКИ", "duty": "ИДЁТ"}.get(state, "ПОПЫТОК"), anchor="w",
                       fill=MUTED, font=font(BODY_FONT, 8))
    canvas.create_text(px(10), px(108), text="СЛУЖБА" if duty else str(int(data.get("tries") or 0)), anchor="w",
                       fill=WHITE, font=font(HEAD_FONT, 30 if duty else 34))
    canvas.create_text(width - px(12), px(80), text={"done": "ЗА", "duty": "НА СЛУЖБЕ"}.get(state, "В ЦИКЛЕ"), anchor="e",
                       fill=MUTED, font=font(BODY_FONT, 8))
    items["clock"] = []
    items["clock_right"] = width - px(12)
    tick_clock(canvas, items, data)

    # the steps of the cycle: the current one framed in the colour of the state
    current = data.get("step") or "service"
    left, right, gap = px(12), width - px(12), px(4)
    cell = (right - left - 2 * gap) / 3
    for index, (step, label) in enumerate(STEPS):
        x0 = left + index * (cell + gap)
        on = step == current
        canvas.create_rectangle(x0, px(132), x0 + cell, px(156), fill=STRIP, outline=tab if on else STRIP, width=max(1, px(1)))
        if step == "service":
            key = canvas.create_text(x0 + px(10), px(144), text="E", anchor="w", fill=STRIP, font=font(HEAD_FONT, 8))
            kx0, _ky0, kx1, _ky1 = canvas.bbox(key)
            canvas.tag_raise(key, canvas.create_rectangle(kx0 - px(3), px(137), kx1 + px(3), px(151), fill=KEY, outline=""))
            canvas.create_text(kx1 + px(7), px(144), text=label, anchor="w", fill=WHITE if on else DIM, font=font(HEAD_FONT, 9))
        else:
            canvas.create_text(x0 + cell / 2, px(144), text=label, fill=WHITE if on else DIM, font=font(HEAD_FONT, 9))

    # the last answer of the game
    message = str(data.get("message") or "")
    bar, color = MESSAGES.get(str(data.get("message_kind") or ""), MESSAGES[""])
    canvas.create_rectangle(px(12), px(162), right, px(184), fill=STRIP, outline="")
    canvas.create_rectangle(px(12), px(162), px(15), px(184), fill=bar, outline="")
    canvas.create_text(px(22), px(173), text=message or "Жду ответа игры", anchor="w", fill=color if message else DIM,
                       font=font(BODY_FONT, 10))

    # the running strip: the bot is alive (solid when the job is done)
    canvas.create_rectangle(0, height - px(4), width, height, fill=STRIP, outline="")
    if state == "done":
        canvas.create_rectangle(0, height - px(4), width, height, fill=tab, outline="")
    else:
        items["scan"] = canvas.create_rectangle(0, height - px(4), width * 0.35, height, fill=tab, outline="")
    items["height"] = height
    return items


def tick_clock(canvas: tk.Canvas, items: dict, data: dict) -> None:
    """The time in the cycle, right-aligned: big numbers, small units."""
    scale = items["scale"]
    px = lambda value: int(round(value * scale))
    for item in items.get("clock", []):
        canvas.delete(item)
    items["clock"] = []
    x = items["clock_right"]
    for number, unit in reversed(clock_parts(elapsed(data))):
        u = canvas.create_text(x, px(110), text=unit, anchor="se", fill=MUTED, font=(BODY_FONT, max(6, px(10))))
        x = canvas.bbox(u)[0] - px(2)
        n = canvas.create_text(x, px(112), text=number, anchor="se", fill=TEXT, font=(HEAD_FONT, max(6, px(20))))
        x = canvas.bbox(n)[0] - px(6)
        items["clock"] += [u, n]


def animate(canvas: tk.Canvas, items: dict, now: float | None = None) -> None:
    """The running strip and the blinking dot (not when the job is done)."""
    now = time.monotonic() if now is None else now
    if "scan" in items:
        width, height = items["width"], items["height"]
        bar = width * 0.35
        x = ((now % 1.6) / 1.6) * (width + bar) - bar
        canvas.coords(items["scan"], x, height - int(round(4 * items["scale"])), x + bar, height)
        canvas.itemconfigure(items["dot"], fill=items["dot_color"] if (now % 1.2) < 0.75 else STRIP)
