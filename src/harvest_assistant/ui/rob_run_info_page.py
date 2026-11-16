"""«Рейд калькулятор» (in the «Tools» part of the menu): the decoder calculator, in the look of the game.

Pick a structure (a tile), set the fuses in it with − / + (the cells fill up), and see the defense and how many
decoders of each quality it takes and how long they install."""

from __future__ import annotations

import tkinter as tk

from ..rob_run_tools import DECODER_LABELS, FUSE_POINTS, STRUCTURES, raid_defense
from .theme import COLORS, Block, ScrollPage, page_header

LIME, INK, PANEL, TEXT, MUTED, DIM, EDGE = "#c6fb33", "#141414", "#0f0f0e", "#e9e9e4", "#8d8d86", "#5f5f59", "#2c2c2a"
HEAD, BODY = "Bahnschrift SemiBold Condensed", "Bahnschrift Condensed"
RARITY = {   # the colours of the qualities, as the items of the game
    "uncommon": ("#46c46a", "Обычные"),
    "epic": ("#b06cf0", "Эпические"),
    "legendary": ("#f5c542", "Легендарные"),
}


def _duration(seconds: int) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} мин {rest:02d} с" if minutes else f"{rest} с"


class ROBRunInfoPage(tk.Frame):
    def __init__(self, parent: tk.Misc, *_unused) -> None:
        super().__init__(parent, bg=COLORS["bg"])
        self.structure = next(iter(STRUCTURES))
        self.fuses = {key: 0 for key in FUSE_POINTS}
        self.tiles: dict[str, tk.Frame] = {}

        scroll = ScrollPage(self, max_width=980)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        page_header(body, "Рейд калькулятор", "калькулятор декодеров").pack(fill="x", pady=(34, 20), padx=4)

        # 1. the structure
        pick = Block(body, "Строение", "lime")
        pick.pack(fill="x", pady=(0, 10))
        grid = tk.Frame(pick.body, bg=pick.fill)
        grid.pack(fill="x")
        for index, (key, item) in enumerate(STRUCTURES.items()):
            grid.grid_columnconfigure(index % 4, weight=1, uniform="tiles")
            tile = tk.Frame(grid, bg=PANEL, highlightthickness=1, highlightbackground=EDGE, cursor="hand2")
            tile.grid(row=index // 4, column=index % 4, sticky="nsew", padx=(0 if index % 4 == 0 else 6, 0), pady=(0, 6))
            name = tk.Label(tile, text=str(item["name"]).upper(), bg=PANEL, fg=TEXT, font=(HEAD, 13), anchor="w", cursor="hand2")
            name.pack(fill="x", padx=10, pady=(7, 0))
            info = tk.Label(tile, text=f"база {item['base']} · ячеек {item['slots']}", bg=PANEL, fg=MUTED, font=(BODY, 10),
                            anchor="w", cursor="hand2")
            info.pack(fill="x", padx=10, pady=(0, 7))
            for widget in (tile, name, info):
                widget.bind("<ButtonPress-1>", lambda _e, k=key: self._pick(k))
            self.tiles[key] = tile

        # 2. the fuses in it
        fuses = Block(body, "Предохранители", "grey")
        fuses.pack(fill="x", pady=(0, 10))
        self.cells_note = fuses.note("")
        row = tk.Frame(fuses.body, bg=fuses.fill)
        row.pack(fill="x")
        self.counts: dict[str, tk.Label] = {}
        for index, (key, (color, name)) in enumerate(RARITY.items()):
            row.grid_columnconfigure(index, weight=1, uniform="fuses")
            cell = tk.Frame(row, bg=PANEL)
            cell.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Frame(cell, bg=color, width=4).pack(side="left", fill="y")
            text = tk.Frame(cell, bg=PANEL)
            text.pack(side="left", padx=10, pady=8)
            tk.Label(text, text=name.upper(), bg=PANEL, fg=color, font=(HEAD, 12)).pack(anchor="w")
            tk.Label(text, text=f"+{FUSE_POINTS[key]} к защите", bg=PANEL, fg=MUTED, font=(BODY, 10)).pack(anchor="w")
            stepper = tk.Frame(cell, bg=PANEL)
            stepper.pack(side="right", padx=10)
            self._step_button(stepper, "−", lambda k=key: self._change(k, -1)).pack(side="left")
            count = tk.Label(stepper, text="0", bg=PANEL, fg=TEXT, font=(HEAD, 20), width=3)
            count.pack(side="left")
            self._step_button(stepper, "+", lambda k=key: self._change(k, +1)).pack(side="left")
            self.counts[key] = count
        self.cells = tk.Canvas(fuses.body, bg=fuses.fill, height=16, highlightthickness=0)
        self.cells.pack(fill="x", pady=(10, 0))
        self.cells.bind("<Configure>", lambda _e: self._draw_cells())

        # 3. the result
        result = Block(body, "Сколько декодеров", "green")
        result.pack(fill="x", pady=(0, 10))
        line = tk.Frame(result.body, bg=result.fill)
        line.pack(fill="x")
        defense = tk.Frame(line, bg=result.fill)
        defense.pack(side="left", padx=(0, 18))
        tk.Label(defense, text="ЗАЩИТА", bg=result.fill, fg=MUTED, font=(BODY, 10)).pack(anchor="w")
        self.defense = tk.Label(defense, text="0", bg=result.fill, fg=LIME, font=(HEAD, 44))
        self.defense.pack(anchor="w")
        self.defense_note = tk.Label(defense, text="", bg=result.fill, fg=MUTED, font=(BODY, 10))
        self.defense_note.pack(anchor="w")
        cards = tk.Frame(line, bg=result.fill)
        cards.pack(side="left", fill="x", expand=True)
        self.decoders: dict[str, tuple[tk.Label, tk.Label]] = {}
        for index, (key, (color, _name)) in enumerate(RARITY.items()):
            cards.grid_columnconfigure(index, weight=1, uniform="decoders")
            card = tk.Frame(cards, bg=PANEL)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Frame(card, bg=color, height=3).pack(fill="x")
            tk.Label(card, text=DECODER_LABELS[key].upper(), bg=PANEL, fg=color, font=(HEAD, 12)).pack(anchor="w", padx=10, pady=(8, 0))
            number = tk.Label(card, text="0", bg=PANEL, fg=TEXT, font=(HEAD, 34))
            number.pack(anchor="w", padx=10)
            time_label = tk.Label(card, text="", bg=PANEL, fg=MUTED, font=(BODY, 11))
            time_label.pack(anchor="w", padx=10, pady=(0, 8))
            self.decoders[key] = (number, time_label)
        tk.Label(result.body, text="Один декодер ставится 12 секунд. Проверь правила игры перед рейдом.",
                 bg=result.fill, fg=DIM, font=(BODY, 10)).pack(anchor="w", pady=(10, 0))
        tk.Frame(body, height=24, bg=COLORS["bg"]).pack()
        self._pick(self.structure)

    @staticmethod
    def _step_button(parent: tk.Misc, text: str, command) -> tk.Label:
        button = tk.Label(parent, text=text, bg="#1b1b1a", fg=TEXT, font=(HEAD, 16), width=2, cursor="hand2",
                          highlightthickness=1, highlightbackground="#3a3a37")
        button.bind("<ButtonPress-1>", lambda _e: command())
        return button

    # ---- actions ------------------------------------------------------------------------------------------------

    def _pick(self, key: str) -> None:
        self.structure = key
        for name, tile in self.tiles.items():
            on = name == key
            tile.configure(highlightbackground=LIME if on else EDGE, bg="#22251a" if on else PANEL)
            for child in tile.winfo_children():
                child.configure(bg="#22251a" if on else PANEL)
        slots = int(STRUCTURES[key]["slots"])
        while sum(self.fuses.values()) > slots:   # a smaller structure: drop the extra fuses, the cheapest first
            for rarity in ("uncommon", "epic", "legendary"):
                if self.fuses[rarity] and sum(self.fuses.values()) > slots:
                    self.fuses[rarity] -= 1
        self._update()

    def _change(self, rarity: str, step: int) -> None:
        slots = int(STRUCTURES[self.structure]["slots"])
        if step > 0 and sum(self.fuses.values()) >= slots:
            return   # every cell is taken
        self.fuses[rarity] = max(0, self.fuses[rarity] + step)
        self._update()

    def _update(self) -> None:
        result = raid_defense(self.structure, self.fuses)
        for key, label in self.counts.items():
            label.configure(text=str(self.fuses[key]))
        self.cells_note.configure(text=f"ЯЧЕЙКИ {result['fuse_count']} / {result['slots']}")
        self.defense.configure(text=str(result["defense"]))
        self.defense_note.configure(text=f"база {result['base']} + фьюзы {result['defense'] - result['base']}")
        for key, (number, time_label) in self.decoders.items():
            number.configure(text=str(result["decoders"][key]))
            time_label.configure(text=f"установка {_duration(result['install_seconds'][key])}")
        self._draw_cells()

    def _draw_cells(self) -> None:
        """The cells of the structure: filled with the colours of the fuses put in."""
        canvas = self.cells
        canvas.delete("all")
        slots = int(STRUCTURES[self.structure]["slots"])
        width = max(100, canvas.winfo_width())
        gap = 3
        size = min(28.0, (width - gap * (slots - 1)) / slots)
        colors = [RARITY[k][0] for k in ("legendary", "epic", "uncommon") for _ in range(self.fuses[k])]
        for index in range(slots):
            x = index * (size + gap)
            fill = colors[index] if index < len(colors) else "#1b1b1a"
            canvas.create_rectangle(x, 0, x + size, 14, fill=fill, outline="#3a3a37" if index >= len(colors) else fill)

    def refresh(self) -> None:
        """Nothing changes by itself here."""
