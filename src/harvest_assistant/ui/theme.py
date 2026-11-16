"""Hidden pool design kit: calm dark surfaces, one accent, generous space.

Principles (Apple HIG-style): content first, few borders, one accent colour, large type
for the one thing that matters, controls that answer instantly on press.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

COLORS = {
    "bg": "#0c0c0e",
    "rail": "#0c0c0e",
    "surface": "#1b1b1a",
    "surface_alt": "#141413",
    "surface_hi": "#262624",
    "border": "#2c2c2a",
    "text": "#e9e9e4",
    "muted": "#8d8d86",
    "faint": "#5f5f59",
    "green": "#c6fb33",
    "green_dark": "#22251a",
    "violet": "#a78bfa",
    "violet_dark": "#241f3f",
    "red": "#ff6b72",
    "amber": "#f3c86e",
    "yellow": "#f5c542",
    "purple": "#b06cf0",
    "pkg_green": "#46c46a",
}

FONT = "Bahnschrift"
FONT_SEMI = "Bahnschrift SemiBold"
FONT_LIGHT = "Bahnschrift Light"
HEAD = "Bahnschrift SemiBold Condensed"   # titles, buttons, tabs: the condensed type of the game
INK = "#141414"                          # text on lime


def blend(color_a: str, color_b: str, amount: float) -> str:
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


_image_cache: dict[tuple, ImageTk.PhotoImage] = {}


def shape_image(width: float, height: float, radius: float, fill: str, outline: str | None = None,
                outline_width: int = 1) -> ImageTk.PhotoImage:
    """Anti-aliased rounded rectangle (drawn 4x larger, then scaled down)."""
    width, height = max(2, int(round(width))), max(2, int(round(height)))
    key = (width, height, round(radius, 1), fill, outline, outline_width)
    cached = _image_cache.get(key)
    if cached is not None:
        return cached
    scale = 4 if width * height < 300_000 else 2
    big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=radius * scale, fill=fill,
        outline=outline, width=(outline_width * scale if outline else 0),
    )
    image = ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS))
    if len(_image_cache) > 300:
        _image_cache.clear()  # widgets keep their own reference to the image they show
    _image_cache[key] = image
    return image


def circle_image(diameter: float, fill: str) -> ImageTk.PhotoImage:
    return shape_image(diameter, diameter, diameter / 2, fill)


def apply_theme(root: tk.Tk) -> None:
    """ttk is only used for entries, combo boxes and scrollbars; everything else is custom."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    c = COLORS
    style.configure(".", background=c["bg"], foreground=c["text"], font=(FONT, 10), bordercolor=c["bg"],
                    lightcolor=c["bg"], darkcolor=c["bg"], focuscolor=c["bg"], troughcolor=c["surface_alt"])
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["text"])
    style.configure("TEntry", padding=(10, 8), fieldbackground=c["surface_hi"], foreground=c["text"], insertcolor=c["text"],
                    bordercolor=c["surface_hi"], lightcolor=c["surface_hi"], darkcolor=c["surface_hi"], borderwidth=1)
    style.map("TEntry", bordercolor=[("focus", c["green"])], lightcolor=[("focus", c["green"])], darkcolor=[("focus", c["green"])])
    style.configure("TCombobox", padding=(10, 7), fieldbackground=c["surface_hi"], background=c["surface_hi"],
                    foreground=c["text"], arrowcolor=c["muted"], bordercolor=c["surface_hi"], lightcolor=c["surface_hi"],
                    darkcolor=c["surface_hi"], selectbackground=c["surface_hi"], selectforeground=c["text"])
    style.map("TCombobox", fieldbackground=[("readonly", c["surface_hi"])], foreground=[("readonly", c["text"])],
              bordercolor=[("focus", c["green"])])
    style.configure("Vertical.TScrollbar", background=c["surface_hi"], troughcolor=c["bg"], bordercolor=c["bg"],
                    arrowcolor=c["bg"], relief="flat", gripcount=0, width=6)
    style.map("Vertical.TScrollbar", background=[("active", "#3a3a40")])
    # Dialogs (table rename / time) still use plain ttk widgets.
    style.configure("Section.TLabel", font=(FONT_SEMI, 13), foreground=c["text"])
    style.configure("Muted.TLabel", foreground=c["muted"])
    base = dict(borderwidth=0, focusthickness=0, relief="flat")
    style.configure("Secondary.TButton", padding=(16, 9), background=c["surface_hi"], foreground=c["text"], **base)
    style.map("Secondary.TButton", background=[("active", "#2f2f34")])
    style.configure("Danger.TButton", padding=(16, 9), background="#3a2226", foreground="#ffb3b8", **base)
    style.map("Danger.TButton", background=[("active", "#52303a")])
    style.configure("Primary.TButton", padding=(18, 9), background=c["green"], foreground="#12180a", **base)
    style.map("Primary.TButton", background=[("active", "#d6f78c")])
    root.option_add("*TCombobox*Listbox.background", c["surface_hi"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["green_dark"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["green"])
    root.configure(background=c["bg"])


# ---------------------------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------------------------

def label(parent: tk.Misc, text: str = "", *, size: int = 10, color: str = "text", font: str = FONT,
          bg: str | None = None, **options: object) -> tk.Label:
    background = bg or parent.cget("bg")
    return tk.Label(parent, text=text, bg=background, fg=COLORS.get(color, color), font=(font, size), **options)


def caption(parent: tk.Misc, text: str, bg: str | None = None) -> tk.Label:
    """Small upper-case section label."""
    return label(parent, text.upper(), size=8, color="muted", font=FONT_SEMI, bg=bg)


def page_header(parent: tk.Misc, title: str, subtitle: str = "") -> tk.Frame:
    """The title of a tab: a slanted lime tab with the name, the subtitle beside it."""
    header = tk.Frame(parent, bg=COLORS["bg"])
    canvas = tk.Canvas(header, bg=COLORS["bg"], highlightthickness=0, height=40)
    canvas.pack(fill="x")
    name = canvas.create_text(14, 20, text=title.upper(), anchor="w", fill=INK, font=(HEAD, 22))
    right = canvas.bbox(name)[2] + 24
    canvas.tag_raise(name, canvas.create_polygon(0, 0, right, 0, right - 12, 40, 0, 40, fill=COLORS["green"], outline=""))
    if subtitle:
        canvas.create_text(right + 12, 20, text=subtitle.lower(), anchor="w", fill=COLORS["muted"], font=(FONT, 11))
    return header


class Card(tk.Frame):
    """A flat dark plate with a hairline edge (the look of the game). Put children into ``card.body``."""

    def __init__(self, parent: tk.Misc, *, padding: tuple[int, int] = (24, 20), radius: int = 0,
                 fill: str | None = None) -> None:
        self._fill = fill or COLORS["surface"]
        super().__init__(parent, bg=self._fill, highlightthickness=1, highlightbackground=COLORS["border"])
        self.body = tk.Frame(self, bg=self._fill)
        self.body.grid(row=0, column=0, sticky="nsew", padx=max(14, padding[0] - 8), pady=max(10, padding[1] - 6))
        self.columnconfigure(0, weight=1)

    @property
    def fill(self) -> str:
        return self._fill


class Block(tk.Frame):
    """A dark plate with a head strip carrying a slanted colour tab with the name. Children go into ``block.body``;
    small things on the right of the head go into ``block.head``."""

    TABS = {   # colour of the tab, text on it
        "lime": (None, INK), "green": ("#46c46a", "#0d1f12"), "blue": ("#3da9f5", "#0c1a26"),
        "amber": ("#e9b93a", INK), "violet": ("#5865f2", "#ffffff"), "grey": ("#3a3a37", "#e9e9e4"),
    }

    def __init__(self, parent: tk.Misc, title: str, tone: str = "lime", *, padding: tuple[int, int] = (14, 12)) -> None:
        self.fill = COLORS["surface"]
        super().__init__(parent, bg=self.fill, highlightthickness=1, highlightbackground=COLORS["border"])
        color, ink = self.TABS.get(tone, self.TABS["lime"])
        color = color or COLORS["green"]
        self.head = tk.Frame(self, bg=COLORS["surface_alt"])
        self.head.pack(fill="x")
        tab = tk.Canvas(self.head, bg=COLORS["surface_alt"], highlightthickness=0, height=30)
        name = tab.create_text(10, 15, text=title.upper(), anchor="w", fill=ink, font=(HEAD, 14))
        right = tab.bbox(name)[2] + 20
        tab.tag_raise(name, tab.create_polygon(0, 0, right, 0, right - 10, 30, 0, 30, fill=color, outline=""))
        tab.configure(width=right + 2)
        tab.pack(side="left")
        self.body = tk.Frame(self, bg=self.fill)
        self.body.pack(fill="both", expand=True, padx=padding[0], pady=padding[1])

    def note(self, text: str = "", *, textvariable: tk.StringVar | None = None, color: str = "muted") -> tk.Label:
        """A small word on the right of the head."""
        widget = tk.Label(self.head, text=text.upper(), textvariable=textvariable, bg=COLORS["surface_alt"],
                          fg=COLORS.get(color, color), font=(FONT, 9))
        widget.pack(side="right", padx=10)
        return widget


def divider(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, height=1, bg=COLORS["border"])


class PillButton(tk.Canvas):
    """Pill button: primary (accent), secondary (filled grey) or ghost (text only)."""

    STYLES = {
        "primary": ("green", "#d6ff6a", "#a8d82a", INK),
        "secondary": ("surface", "#262624", "#111110", "text"),
        "ghost": (None, "#1f1f1d", "#262624", "muted"),
        "danger": (None, "#2a1517", "#341a1d", "red"),
    }

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None] | None = None, *,
                 style: str = "secondary", padx: int = 20, height: int = 38, size: int = 10, **_ignored: object) -> None:
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, height=height, cursor="hand2")
        self._style = style
        self._command = command
        self._text = text
        self._padx = padx
        self._height = height
        self._size = size
        self._enabled = True
        self._state = "idle"
        self._font = (HEAD, size + 2)
        self.bind("<Enter>", lambda _e: self._set_state("hover"))
        self.bind("<Leave>", lambda _e: self._set_state("idle"))
        self.bind("<ButtonPress-1>", lambda _e: self._set_state("press"))  # feedback on press, not on release
        self.bind("<ButtonRelease-1>", self._release)
        self._render()

    def _colors(self) -> tuple[str, str]:
        base, hover, press, text_key = self.STYLES[self._style]
        fill = COLORS[base] if base else self.master.cget("bg")
        if self._state == "hover":
            fill = hover
        elif self._state == "press":
            fill = press
        text_color = COLORS.get(text_key, text_key)
        if not self._enabled:
            fill = COLORS["surface_alt"] if base else self.master.cget("bg")
            text_color = COLORS["faint"]
        return fill, text_color

    def _render(self) -> None:
        import tkinter.font as tkfont
        text = self._text.upper()
        width = tkfont.Font(font=self._font).measure(text) + self._padx * 2
        self.configure(width=width)
        self.delete("all")
        fill, text_color = self._colors()
        edge = {"secondary": "#3a3a37", "primary": fill}.get(self._style)
        if fill != self.master.cget("bg") or edge:
            self.create_rectangle(0, 0, width - 1, self._height - 1, fill=fill, outline=edge or fill)
        self.create_text(width / 2, self._height / 2, text=text, fill=text_color, font=self._font)
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _set_state(self, state: str) -> None:
        if self._enabled and state != self._state:
            self._state = state
            self._render()

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set_state("hover" if inside else "idle")
        if inside and self._enabled and self._command:
            self._command()

    def configure(self, cnf: dict | None = None, **options: object) -> object:  # type: ignore[override]
        merged = {**(cnf or {}), **options}
        redraw = False
        if "text" in merged:
            self._text = str(merged.pop("text"))
            redraw = True
        if "state" in merged:
            self._enabled = merged.pop("state") != "disabled"
            self._state = "idle"
            redraw = True
        if "command" in merged:
            self._command = merged.pop("command")  # type: ignore[assignment]
        result = super().configure(**merged) if merged else None
        if redraw and hasattr(self, "_font"):
            self._render()
        return result

    config = configure


class Toggle(tk.Canvas):
    """A square switch: lime with a dark knob on the right when on, grey with the knob on the left when off."""

    W, H = 36, 18

    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, command: Callable[[], None] | None = None) -> None:
        super().__init__(parent, width=self.W, height=self.H, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self._var = variable
        self._command = command
        self._pos = 1.0 if variable.get() else 0.0
        self._job: str | None = None
        self.bind("<ButtonPress-1>", self._press)
        variable.trace_add("write", lambda *_: self._animate())
        self._draw()

    def _press(self, _event: tk.Event) -> None:
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _animate(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)  # interruptible: continue from the current position
        target = 1.0 if self._var.get() else 0.0

        def step() -> None:
            delta = target - self._pos
            if abs(delta) < 0.02:
                self._pos = target
                self._job = None
            else:
                self._pos += delta * 0.38
                self._job = self.after(16, step)
            self._draw()

        step()

    def _draw(self) -> None:
        self.delete("all")
        self.create_rectangle(0, 0, self.W - 1, self.H - 1, fill=blend("#2c2c2a", COLORS["green"], self._pos), outline="")
        knob = 3 + (self.W - self.H) * self._pos
        self.create_rectangle(knob, 3, knob + self.H - 7, self.H - 4,
                              fill=blend(COLORS["muted"], INK, self._pos), outline="")


class Slider(tk.Canvas):
    """Thin track, round knob, 1:1 pointer tracking."""

    def __init__(self, parent: tk.Misc, low: float, high: float, value: float, command: Callable[[float], None] | None = None,
                 *, width: int = 280) -> None:
        super().__init__(parent, width=width, height=28, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self._low, self._high, self._value, self._command = low, high, value, command
        self._width = width
        self.bind("<ButtonPress-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Configure>", lambda e: (setattr(self, "_width", e.width), self._draw()))
        self._draw()

    def get(self) -> float:
        return self._value

    def set(self, value: float) -> None:
        self._value = min(self._high, max(self._low, value))
        self._draw()

    def _drag(self, event: tk.Event) -> None:
        span = max(1, self._width - 20)
        fraction = min(1.0, max(0.0, (event.x - 10) / span))
        self._value = self._low + (self._high - self._low) * fraction
        self._draw()
        if self._command:
            self._command(self._value)

    def _draw(self) -> None:
        self.delete("all")
        span = max(1, self._width - 20)
        fraction = (self._value - self._low) / (self._high - self._low)
        x = 10 + span * fraction
        self._track = shape_image(self._width - 20, 4, 2, COLORS["surface_hi"])
        self.create_image(10, 12, anchor="nw", image=self._track)
        if x - 10 >= 4:
            self._fill = shape_image(x - 10, 4, 2, COLORS["green"])
            self.create_image(10, 12, anchor="nw", image=self._fill)
        self._knob = circle_image(18, "#ffffff")
        self.create_image(x - 9, 5, anchor="nw", image=self._knob)



class Segmented(tk.Frame):
    """Two-to-four choice control."""

    def __init__(self, parent: tk.Misc, variable: tk.StringVar, options: list[tuple[str, str]],
                 command: Callable[[], None] | None = None) -> None:
        super().__init__(parent, bg=COLORS["surface"], padx=2, pady=2, highlightthickness=1,
                         highlightbackground=COLORS["border"])
        self._var, self._command = variable, command
        self._buttons: dict[str, tk.Label] = {}
        for value, text in options:
            button = tk.Label(self, text=text.upper(), font=(HEAD, 11), padx=14, pady=5, cursor="hand2")
            button.pack(side="left")
            button.bind("<ButtonPress-1>", lambda _e, v=value: self._choose(v))
            self._buttons[value] = button
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _choose(self, value: str) -> None:
        self._var.set(value)
        if self._command:
            self._command()

    def _paint(self) -> None:
        for value, button in self._buttons.items():
            selected = value == self._var.get()
            button.configure(bg=COLORS["green"] if selected else COLORS["surface"],
                             fg=INK if selected else COLORS["muted"])


class ChipToggle(tk.Canvas):
    """Small selectable pill used for per-element switches."""

    HEIGHT = 32

    def __init__(self, parent: tk.Misc, text: str, variable: tk.BooleanVar, command: Callable[[], None] | None = None,
                 *, dot: str | None = None) -> None:
        import tkinter.font as tkfont
        font = (FONT, 9)
        width = tkfont.Font(font=font).measure(text) + 40 + (14 if dot else 0)
        super().__init__(parent, width=width, height=self.HEIGHT, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self._text, self._var, self._command, self._dot, self._font, self._cw = text, variable, command, dot, font, width
        self._enabled = True
        self.bind("<ButtonPress-1>", self._press)
        variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._draw()

    def _press(self, _event: tk.Event) -> None:
        if not self._enabled:
            return
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        on = self._var.get() and self._enabled
        fill = COLORS["surface_hi"] if on else COLORS["surface_alt"]
        outline = COLORS["green"] if on else None
        self._image = shape_image(self._cw, self.HEIGHT, self.HEIGHT / 2, fill, outline, 1)
        self.create_image(0, 0, anchor="nw", image=self._image)
        x = 16
        if self._dot:
            self._dot_image = circle_image(10, self._dot if on else COLORS["faint"])
            self.create_image(x, 11, anchor="nw", image=self._dot_image)
            x += 16
        self.create_text(x, self.HEIGHT / 2, text=self._text, anchor="w", font=self._font,
                         fill=COLORS["text"] if on else COLORS["faint"])



class Bar(tk.Canvas):
    """Hair-thin progress bar."""

    def __init__(self, parent: tk.Misc, *, height: int = 4, bg: str | None = None) -> None:
        super().__init__(parent, height=height, bg=bg or parent.cget("bg"), highlightthickness=0, bd=0)
        self._fraction, self._color, self._h = 0.0, COLORS["violet"], height
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, fraction: float, ready: bool = False) -> None:
        self._fraction = min(1.0, max(0.0, fraction))
        self._color = COLORS["green"] if ready else COLORS["violet"]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width < 4:
            return
        self._track = shape_image(width, self._h, self._h / 2, COLORS["surface_hi"])
        self.create_image(0, 0, anchor="nw", image=self._track)
        if self._fraction > 0:
            self._fill = shape_image(max(self._h, width * self._fraction), self._h, self._h / 2, self._color)
            self.create_image(0, 0, anchor="nw", image=self._fill)



class NavItem(tk.Canvas):
    """Sidebar entry with a soft selection pill."""

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None], *, badge: str = "",
                 enabled: bool = True) -> None:
        super().__init__(parent, height=40, bg=parent.cget("bg"), highlightthickness=0, bd=0,
                         cursor="hand2" if enabled else "arrow")
        self._text, self._command, self._badge = text, command, badge
        self._enabled = enabled
        self._selected = False
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._hovered(True))
        self.bind("<Leave>", lambda _e: self._hovered(False))
        self.bind("<ButtonPress-1>", lambda _e: self._command() if self._enabled else None)

    def _hovered(self, value: bool) -> None:
        self._hover = value and self._enabled
        self._draw()

    def select(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        if width > 4 and (self._selected or self._hover):
            fill = COLORS["surface_hi"] if self._selected else COLORS["surface"]
            self._image = shape_image(width, 40, 12, fill)
            self.create_image(0, 0, anchor="nw", image=self._image)
        color = COLORS["text"] if self._selected else (COLORS["muted"] if self._enabled else COLORS["faint"])
        if self._badge and not self._enabled:   # a tab that is not ready: name on top, the note under it
            self.create_text(18, 14, text=self._text, anchor="w", fill=color, font=(FONT, 11))
            self.create_text(18, 31, text=self._badge, anchor="w", fill=COLORS["amber"], font=(FONT, 7))
            return
        self.create_text(18, 20, text=self._text, anchor="w", fill=color, font=(FONT_SEMI if self._selected else FONT, 11))
        if self._badge:
            self.create_text(width - 16, 20, text=self._badge, anchor="e", fill=COLORS["faint"], font=(FONT, 8))



class HelpTip(tk.Canvas):
    """A «?» in a circle: the pointer over it opens a hint next to it, the pointer away closes it."""

    def __init__(self, parent: tk.Misc, text: str, *, size: int = 20, width: int = 320) -> None:
        super().__init__(parent, width=size, height=size, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="question_arrow")
        self._text, self._size, self._wrap = text, size, width
        self._tip: tk.Toplevel | None = None
        self._draw(False)
        self.bind("<Enter>", lambda _e: self._open())
        self.bind("<Leave>", lambda _e: self._close())

    def _draw(self, hover: bool) -> None:
        self.delete("all")
        size = self._size
        self.create_oval(1, 1, size - 1, size - 1, outline=COLORS["text"] if hover else COLORS["muted"], width=1.4,
                         fill=COLORS["surface_hi"] if hover else "")
        self.create_text(size / 2, size / 2 + 0.5, text="?", fill=COLORS["text"] if hover else COLORS["muted"],
                         font=(FONT_SEMI, max(7, int(size * 0.45))))

    def _open(self) -> None:
        self._draw(True)
        if self._tip is not None:
            return
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        frame = tk.Frame(tip, bg=COLORS["surface_hi"], highlightthickness=1, highlightbackground=COLORS["border"])
        frame.pack()
        tk.Label(frame, text=self._text, bg=COLORS["surface_hi"], fg=COLORS["text"], font=(FONT, 9), justify="left",
                 wraplength=self._wrap, padx=12, pady=10).pack()
        tip.update_idletasks()
        x = self.winfo_rootx() - tip.winfo_reqwidth() - 8           # to the left of the «?», level with it
        if x < 0:
            x = self.winfo_rootx() + self._size + 8
        y = self.winfo_rooty() + self._size // 2 - tip.winfo_reqheight() // 2
        tip.geometry(f"+{x}+{max(0, y)}")
        self._tip = tip

    def _close(self) -> None:
        self._draw(False)
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class StatusDot(tk.Frame):
    """Dot + text bound to a StringVar, no background."""

    def __init__(self, parent: tk.Misc, variable: tk.StringVar, color_for: Callable[[str], str],
                 command: Callable[[], None] | None = None) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self._color_for = color_for
        self.dot = tk.Canvas(self, width=8, height=8, bg=parent.cget("bg"), highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 7))
        self._oval = self.dot.create_oval(0, 0, 8, 8, fill=COLORS["muted"], outline="")
        tk.Label(self, textvariable=variable, bg=parent.cget("bg"), fg=COLORS["muted"], font=(FONT, 9)).pack(side="left")
        variable.trace_add("write", lambda *_: self._recolor(variable.get()))
        self._recolor(variable.get())
        if command:
            for widget in (self, self.dot, *self.winfo_children()):
                widget.configure(cursor="hand2")
                widget.bind("<ButtonPress-1>", lambda _e: command())

    def _recolor(self, text: str) -> None:
        self.dot.itemconfigure(self._oval, fill=self._color_for(text))


class Counter(tk.Frame):
    """A package counter: a colour bar like the in-game notification, big number, tiny caption."""

    def __init__(self, parent: tk.Misc, color: str, caption_text: str, *, size: int = 26) -> None:
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        tk.Frame(self, width=4, height=size + 16, bg=color).pack(side="left", padx=(0, 12))
        stack = tk.Frame(self, bg=bg)
        stack.pack(side="left")
        self.value = tk.Label(stack, text="0", bg=bg, fg=COLORS["text"], font=(FONT_SEMI, size))
        self.value.pack(anchor="w")
        tk.Label(stack, text=caption_text, bg=bg, fg=COLORS["muted"], font=(FONT, 8)).pack(anchor="w")

    def set(self, value: int) -> None:
        self.value.configure(text=str(value))


class ScrollPage(tk.Frame):
    """Vertically scrolling content area with a hairline scrollbar."""

    def __init__(self, parent: tk.Misc, *, max_width: int = 720) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.canvas = tk.Canvas(self, background=COLORS["bg"], highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=COLORS["bg"])
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="n")
        self._max_width = max_width
        # No visible scroll bar: the wheel scrolls, the page stays clean.
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda _e: self._update_region())
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")

    def _update_region(self) -> None:
        # Origin stays at x=0, otherwise the centred column drifts sideways.
        bottom = self.canvas.bbox("all")[3] if self.canvas.bbox("all") else 0
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), bottom))

    def _resize(self, event: tk.Event) -> None:
        width = min(self._max_width, event.width - 8)
        self.canvas.itemconfigure(self._window, width=width)
        self.canvas.coords(self._window, event.width / 2, 0)
        self._update_region()

    def _wheel(self, event: tk.Event) -> None:
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is self:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


def settings_row(card: Card, title: str, hint: str = "", *, first: bool = False) -> tk.Frame:
    """A labelled row inside a card; returns the frame that receives the control (packed right)."""
    if not first:
        divider(card.body).pack(fill="x", pady=12)
    row = tk.Frame(card.body, bg=card.fill)
    row.pack(fill="x")
    text = tk.Frame(row, bg=card.fill)
    text.pack(side="left", fill="x", expand=True)
    label(text, title, size=11, bg=card.fill).pack(anchor="w")
    if hint:
        label(text, hint, size=9, color="muted", bg=card.fill, justify="left", wraplength=360).pack(anchor="w", pady=(2, 0))
    control = tk.Frame(row, bg=card.fill)
    control.pack(side="right", padx=(16, 0))
    return control
