"""Seeing a door: the door of the house and the doors of the container are big, plain-coloured slabs.

The bot used to open whatever showed an «Открыть / Закрыть» prompt, and to look for a door only where the taught way
said it must be. A few steps off, that was a window. Now a door is also found by what it looks like (its colour and its
shape), so the bot turns to it and walks up to it until the game itself calls it «ДВЕРЬ».
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# HSV (OpenCV: hue 0..179). Measured on screenshots of the game: the orange wood of the house door has hue 10 and
# saturation ~200 (the wallpaper has hue 20 / sat 120, the floor is much darker, the deck boards much lighter).
PROFILES = {
    "house": {"hue": (5, 15), "sat": (150, 255), "val": (75, 175)},
    # the same door seen from the porch, in the shade of the roof: much darker, and the trailer wall / rails around it
    # are nearly colourless (sat 50-65), so saturation still tells them apart
    "porch": {"hue": (6, 13), "sat": (115, 230), "val": (30, 80)},
    "container": {"hue": (0, 9), "sat": (90, 230), "val": (40, 115)},
}


@dataclass
class DoorSight:
    center_x: float      # fraction of the picture width (0 = left edge)
    center_y: float      # fraction of the picture height
    height: float        # share of the picture height the door covers
    width: float
    cut: bool            # the door runs off the edge of the picture


def find_door(frame: np.ndarray, kind: str = "house") -> DoorSight | None:
    """The biggest door-coloured, door-shaped slab on the picture, or None."""
    profile = PROFILES[kind]
    height, width = frame.shape[:2]
    scale = min(1.0, 480.0 / width)
    small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else frame
    h, w = small.shape[:2]
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    low = np.array([profile["hue"][0], profile["sat"][0], profile["val"][0]], np.uint8)
    high = np.array([profile["hue"][1], profile["sat"][1], profile["val"][1]], np.uint8)
    mask = cv2.inRange(hsv, low, high)
    # the screen furniture of the game: mini-map, the statistics card, chat, hotbar
    mask[: int(h * 0.20), : int(w * 0.20)] = 0
    mask[int(h * 0.18): int(h * 0.46), : int(w * 0.21)] = 0
    mask[int(h * 0.76):, : int(w * 0.26)] = 0
    mask[int(h * 0.90):, :] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    best = None
    for index in range(1, count):
        x, y, bw, bh, area = stats[index]
        if area < 0.02 * w * h or bh < 0.22 * h:
            continue
        fill = area / float(bw * bh)
        if fill < 0.45:  # a door is a slab, not a scatter
            continue
        cut = x <= 1 or x + bw >= w - 1
        if bh < 1.15 * bw and not cut:  # taller than wide (a door that is cut by the picture edge may look wider)
            continue
        if best is None or area > best[0]:
            best = (area, x, y, bw, bh, cut)
    if best is None:
        return None
    _area, x, y, bw, bh, cut = best
    return DoorSight((x + bw / 2) / w, (y + bh / 2) / h, bh / h, bw / w, cut)
