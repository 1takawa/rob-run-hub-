"""The way from the spawn to the tables, cut into zones.

 1. ``house``     – the room of the spawn (wooden interior, beige light)
 2. ``yard``      – outside: the porch and the sand up to the container (sun, sand colours)
 3. ``gate``      – the container with the red doors: the entrance to the plantation
 4. ``stairs``    – the stairs inside the container, down to the landing
 5. ``platform``  – the grow room with all the tables, to the LEFT of the landing (purple grow lamps)

A zone is told by the picture itself (light colour) and, where colour is not enough, by how many doors of the taught
way are already behind us (``stage``). Colour was checked on the recorded runs: the platform is the only place with
a real share of purple, the yard the only one with a lot of sand.
"""

from __future__ import annotations

import cv2
import numpy as np

ZONES = ("house", "yard", "gate", "stairs", "platform")
ZONE_TITLES = {
    "house": "дом",
    "yard": "двор",
    "gate": "вход (ворота)",
    "stairs": "лестница",
    "platform": "платформа со столами",
}

PLATFORM_PURPLE = 0.06   # share of purple pixels: the grow room has ~0.10, nowhere else more than 0.01
YARD_SAND = 0.20         # share of sand-coloured pixels: outside 0.3-0.7, inside never above 0.17


def color_stats(rgb: np.ndarray) -> dict[str, float]:
    small = cv2.resize(rgb, (160, 128), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return {
        "purple": float(((h >= 125) & (h <= 160) & (s > 50) & (v > 40)).mean()),
        "sand": float(((h >= 12) & (h <= 25) & (s > 90) & (v > 150)).mean()),
        "bright": float(v.mean()),
    }


def is_platform(rgb: np.ndarray) -> bool:
    """True in the purple light of the grow room, wherever on its floor we stand."""
    return color_stats(rgb)["purple"] >= PLATFORM_PURPLE


def zone_for(stage: int | None, rgb: np.ndarray | None = None) -> str:
    """Zone by colour first, then by the doors passed (0-1 house/yard, 2 yard, 3 gate, 4 stairs)."""
    if rgb is not None:
        stats = color_stats(rgb)
        if stats["purple"] >= PLATFORM_PURPLE:
            return "platform"
        if stats["sand"] >= YARD_SAND:
            return "yard"
    if stage is None:
        return "house"
    if stage <= 1:
        return "house"
    if stage == 2:
        return "yard"
    if stage == 3:
        return "gate"
    return "stairs"
