"""The loadout screen («Выбрать экипировку»): the five «ПОДТВЕРДИТЬ» buttons found by what they look like.

The buttons are found by colour and shape, not by fixed fractions of the screen, so the layout may be stretched to any
window size: 1280x1024, 1920x1080, 1080x1080 or a letterboxed one. An available class has a lime button, an
unavailable one a black button of the same size; both are found and told apart, and the five are numbered from the left.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

CLASSES = 5
LIME_HUE = (28, 50)        # OpenCV hue (0-179) of the lime of the buttons, about 38
LIME_MIN_SATURATION = 110
LIME_MIN_VALUE = 170
BLACK_MAX_VALUE = 16        # the unavailable buttons are pure black; the cards around them are grey (about 40)


@dataclass(frozen=True, slots=True)
class ConfirmButton:
    x: int
    y: int
    width: int
    height: int
    active: bool            # lime: the class can be confirmed

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def _boxes(mask: np.ndarray, frame_width: int) -> list[tuple[int, int, int, int]]:
    """Solid, wide rectangles of a mask (the label inside a button is closed over)."""
    size = max(3, round(frame_width / 250)) | 1
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (size, size)))
    found = []
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < 6 or not 0.05 * frame_width <= w <= 0.35 * frame_width:
            continue
        if not 2.8 <= w / h <= 12.0:
            continue
        if cv2.contourArea(contour) < 0.8 * w * h:
            continue
        found.append((x, y, w, h))
    return found


def find_confirm_buttons(frame: np.ndarray) -> list[ConfirmButton]:
    """The row of confirm buttons, left to right (empty when the screen is not the loadout screen)."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    height, width = frame.shape[:2]
    lime = cv2.inRange(hsv, (LIME_HUE[0], LIME_MIN_SATURATION, LIME_MIN_VALUE), (LIME_HUE[1], 255, 255))
    black = cv2.inRange(hsv, (0, 0, 0), (179, 255, BLACK_MAX_VALUE))
    candidates = [ConfirmButton(*box, True) for box in _boxes(lime, width)]
    candidates += [ConfirmButton(*box, False) for box in _boxes(black, width)]
    if len(candidates) < 3:
        return []
    # the row: the most buttons of one height level and one size
    best: list[ConfirmButton] = []
    for anchor in candidates:
        row = [b for b in candidates
               if abs((b.y + b.height / 2) - (anchor.y + anchor.height / 2)) <= 0.35 * anchor.height
               and abs(b.width - anchor.width) <= 0.15 * anchor.width and abs(b.height - anchor.height) <= 0.25 * anchor.height]
        if len(row) > len(best):
            best = row
    best.sort(key=lambda b: b.x)
    row: list[ConfirmButton] = []
    for button in best:
        if row and button.x < row[-1].x + row[-1].width * 0.5:
            continue   # the same button twice (its lime and its black frame)
        row.append(button)
    return row if len(row) >= 3 else []


def class_button(frame: np.ndarray, index: int) -> ConfirmButton | None:
    """The confirm button of the class with this number (0 = the first from the left), or None when the five buttons
    of the screen are not all in view."""
    row = find_confirm_buttons(frame)
    if len(row) == CLASSES:
        return row[index] if 0 <= index < CLASSES else None
    if len(row) >= 3 and 0 <= index < CLASSES:
        # a button was not found: the columns are evenly spaced, fill the gap between the ones that were
        pitch = float(np.median([b2.x - b1.x for b1, b2 in zip(row, row[1:]) if b2.x - b1.x < 1.6 * min(
            c2.x - c1.x for c1, c2 in zip(row, row[1:]))]))
        span = round((row[-1].x - row[0].x) / pitch) + 1
        if span == CLASSES:
            slots = [round((b.x - row[0].x) / pitch) for b in row]
            if index in slots:
                return row[slots.index(index)]
            gap = row[0]
            return ConfirmButton(round(row[0].x + index * pitch), gap.y, gap.width, gap.height, False)
    return None


def duty_badge(frame: np.ndarray) -> bool:
    """On duty the game shows a counter in the top right corner: a dark plate with a number and a blue police shield.
    Seen anywhere in the world, not only at the counter of the police."""
    height, width = frame.shape[:2]
    x_from, y_to = int(width * 0.80), int(height * 0.14)
    region = frame[:y_to, x_from:]
    if not region.size:
        return False
    hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
    blue = cv2.inRange(hsv, (90, 140, 120), (114, 255, 255))
    contours, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not 0.008 * height <= h <= 0.035 * height or not 0.35 <= w / h <= 1.6:
            continue
        if cv2.contourArea(contour) < 0.45 * w * h:
            continue
        # the plate around it: flat dark grey, with a light number on its left
        x0, x1 = max(0, x - int(2.6 * h)), min(region.shape[1], x + w + int(0.9 * h))
        y0, y1 = max(0, y - int(0.8 * h)), min(region.shape[0], y + h + int(0.8 * h))
        plate = hsv[y0:y1, x0:x1].reshape(-1, 3)
        inside = blue[y0:y1, x0:x1].reshape(-1) == 0
        grey = plate[inside & (plate[:, 2] < 150)]
        if len(grey) < 20:
            continue
        if not (np.median(grey[:, 1]) < 45 and 25 <= np.median(grey[:, 2]) <= 110 and np.std(grey[:, 2].astype(float)) < 22):
            continue
        number = hsv[y:y + h, max(0, x - int(1.8 * max(h, w * 1.4))):x]
        light = (number[:, :, 2] > 170) & (number[:, :, 1] < 70)
        if light.sum() >= 0.06 * h * h:
            return True
    return False
