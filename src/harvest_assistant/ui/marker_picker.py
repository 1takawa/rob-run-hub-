"""Select a small region of an existing game screenshot."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

from ..models import ScreenRegion


def select_marker_region(parent: tk.Misc, filename: str) -> tuple[np.ndarray, ScreenRegion | None]:
    with Image.open(filename) as source:
        image = source.convert("RGB")
    frame = np.asarray(image)
    preview = image.copy()
    preview.thumbnail((min(1040, parent.winfo_screenwidth() - 100), parent.winfo_screenheight() - 190))
    dialog = tk.Toplevel(parent)
    dialog.title("Выбор ориентира")
    dialog.transient(parent)
    ttk.Label(dialog, text="Обведи небольшую деталь, которая однозначно показывает это состояние.").pack(pady=8)
    canvas = tk.Canvas(dialog, width=preview.width, height=preview.height, highlightthickness=0)
    canvas.pack()
    photo = ImageTk.PhotoImage(preview, master=dialog)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    box = canvas.create_rectangle(0, 0, 0, 0, outline="#b7f16e", width=2)
    origin = (0, 0)
    selection: ScreenRegion | None = None
    result: ScreenRegion | None = None

    def point(event: tk.Event) -> tuple[int, int]:
        return min(preview.width, max(0, event.x)), min(preview.height, max(0, event.y))

    def start(event: tk.Event) -> None:
        nonlocal origin
        origin = point(event)
        drag(event)

    def drag(event: tk.Event) -> None:
        nonlocal selection
        x, y = point(event)
        left, right = sorted((origin[0], x))
        top, bottom = sorted((origin[1], y))
        canvas.coords(box, left, top, right, bottom)
        x1, x2 = (round(v * image.width / preview.width) for v in (left, right))
        y1, y2 = (round(v * image.height / preview.height) for v in (top, bottom))
        selection = (x1, y1, x2 - x1, y2 - y1)

    def accept() -> None:
        nonlocal result
        if selection is not None and min(selection[2:]) >= 12:
            result = selection
            dialog.destroy()

    canvas.bind("<Button-1>", start)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", drag)
    row = ttk.Frame(dialog, padding=8)
    row.pack(fill="x")
    ttk.Button(row, text="Сохранить", command=accept).pack(side="right")
    ttk.Button(row, text="Отмена", command=dialog.destroy).pack(side="right", padx=8)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.grab_set()
    parent.wait_window(dialog)
    return frame, result
