"""The live locator: the whole picture of the mini-map, not single points of it.

Key points (SIFT, see minimap_pose.MapLocator) work between two frames of one recording, but most of them sit on fine
detail that does not survive from a recorded frame to the live screen: in the first live trial the mini-map could not
be read. The mini-map is a simple flat picture (sand, darker patches, the white outline of the lot), so here it is laid
as a whole onto one picture of the area put together from the recordings (the mosaic): turned to every heading in turn
and slid to where it fits best. The poses are in the frame described in minimap_pose.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .minimap_pose import PIVOT, Pose, wrap
from .radar import CANVAS, prepare

PATCH = 95            # half the side of the square cut from the live mini-map around the player, canvas pixels
MOSAIC_MARGIN = 150   # the mosaic reaches this far beyond the outermost recorded place
BLUR = 1.5
MIN_SCORE = 0.50      # correlation below this is not a fit (recorded frames score 0.65-0.95, wrong pictures below 0.5)
MIN_DETAIL = 3.0      # grey levels of spread a mini-map must have: a flat picture fits anywhere
REACH = 18.0          # how far beyond the recorded places a pose is still believed, canvas pixels


def canvas_of(rgb: np.ndarray) -> np.ndarray:
    grey, _lime = prepare(rgb)
    return cv2.GaussianBlur(grey, (0, 0), BLUR).astype(np.float32)


class MosaicLocator:
    """One picture of the whole area in the frame of the poses; `locate` fits a live mini-map onto it."""

    usable = True

    def __init__(self, mosaic: np.ndarray, offset: tuple[float, float]) -> None:
        self.mosaic = mosaic.astype(np.float32)
        self.offset = (float(offset[0]), float(offset[1]))   # mosaic pixel of the origin of the poses
        self._half = cv2.resize(self.mosaic, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        self.last: Pose | None = None
        # the recorded places span the picture without its margin: only there (and a little around) can we be
        height, width = self.mosaic.shape[:2]
        self.bounds = (MOSAIC_MARGIN - self.offset[0] - REACH, MOSAIC_MARGIN - self.offset[1] - REACH,
                       width - MOSAIC_MARGIN - self.offset[0] + REACH, height - MOSAIC_MARGIN - self.offset[1] + REACH)

    @classmethod
    def build(cls, frames: list[tuple[np.ndarray, Pose]]) -> "MosaicLocator":
        """`frames`: pictures (RGB, any size) with their poses."""
        xs, ys = [pose.x for _f, pose in frames], [pose.y for _f, pose in frames]
        offset = (MOSAIC_MARGIN - min(xs), MOSAIC_MARGIN - min(ys))
        size = (int(max(xs) - min(xs) + 2 * MOSAIC_MARGIN), int(max(ys) - min(ys) + 2 * MOSAIC_MARGIN))
        total = np.zeros((size[1], size[0]), np.float32)
        count = np.zeros((size[1], size[0]), np.float32)
        # the rim of the canvas and the arrow of the player in its middle are not part of the map
        valid = np.zeros((CANVAS[1], CANVAS[0]), np.float32)
        cv2.rectangle(valid, (6, 6), (CANVAS[0] - 7, CANVAS[1] - 7), 1.0, -1)
        cv2.circle(valid, (int(PIVOT[0]), int(PIVOT[1])), 20, 0.0, -1)
        for rgb, pose in frames:
            canvas = canvas_of(rgb)
            a = math.radians(pose.heading)
            cos, sin = math.cos(a), math.sin(a)
            # world = P + R(h) (q - pivot); mosaic = world + offset
            tx = pose.x + offset[0] - (cos * PIVOT[0] - sin * PIVOT[1])
            ty = pose.y + offset[1] - (sin * PIVOT[0] + cos * PIVOT[1])
            matrix = np.float32([[cos, -sin, tx], [sin, cos, ty]])
            total += cv2.warpAffine(canvas * valid, matrix, size, flags=cv2.INTER_LINEAR)
            count += cv2.warpAffine(valid, matrix, size, flags=cv2.INTER_LINEAR)
        seen = count > 0.5
        mosaic = np.full_like(total, float(np.median(total[seen] / count[seen])) if seen.any() else 128.0)
        mosaic[seen] = total[seen] / count[seen]
        return cls(mosaic, offset)

    def save(self, path) -> None:
        cv2.imencode(".png", np.clip(np.rint(self.mosaic), 0, 255).astype(np.uint8))[1].tofile(str(path))

    @classmethod
    def load(cls, path, offset: tuple[float, float]) -> "MosaicLocator":
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise OSError(f"no mosaic at {path}")
        return cls(image, offset)

    @staticmethod
    def _patch(canvas: np.ndarray, heading: float, scale: float = 1.0) -> np.ndarray:
        """The live mini-map turned north-up (as the mosaic is), cut square around the player."""
        a = math.radians(heading)
        cos, sin = math.cos(a), math.sin(a)
        half = PATCH * scale
        # source = pivot + R(-h) (dst - half) / scale
        matrix = np.float32([[cos / scale, sin / scale, PIVOT[0] - (cos * half + sin * half) / scale],
                             [-sin / scale, cos / scale, PIVOT[1] - (-sin * half + cos * half) / scale]])
        side = int(round(2 * half))
        return cv2.warpAffine(canvas, matrix, (side, side), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)

    def _match(self, canvas: np.ndarray, heading: float, around: tuple[float, float] | None, reach: float,
               coarse: bool) -> tuple[float, float, float]:
        """(score, x, y) of the best place for the mini-map turned to `heading`."""
        scale = 0.5 if coarse else 1.0
        field = self._half if coarse else self.mosaic
        patch = self._patch(canvas, heading, scale)
        half = patch.shape[0] / 2.0
        if around is None:   # anywhere the recordings have been
            x0, y0, x1, y1 = self.bounds
        else:
            x0, y0 = max(self.bounds[0], around[0] - reach), max(self.bounds[1], around[1] - reach)
            x1, y1 = min(self.bounds[2], around[0] + reach), min(self.bounds[3], around[1] + reach)
        if x1 <= x0 or y1 <= y0:
            return -1.0, 0.0, 0.0
        left, top = int(max(0, (x0 + self.offset[0]) * scale - half)), int(max(0, (y0 + self.offset[1]) * scale - half))
        right = int(min(field.shape[1], (x1 + self.offset[0]) * scale + half + 1))
        bottom = int(min(field.shape[0], (y1 + self.offset[1]) * scale + half + 1))
        window = field[top:bottom, left:right]
        if window.shape[0] < patch.shape[0] or window.shape[1] < patch.shape[1]:
            return -1.0, 0.0, 0.0
        result = cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED)
        _low, score, _where, peak = cv2.minMaxLoc(result)
        px, py = float(peak[0]), float(peak[1])
        if 0 < peak[0] < result.shape[1] - 1 and 0 < peak[1] < result.shape[0] - 1:  # between the pixels: a parabola
            row, col = result[peak[1], peak[0] - 1:peak[0] + 2], result[peak[1] - 1:peak[1] + 2, peak[0]]
            bend = float(row[0] - 2 * row[1] + row[2])
            if bend < -1e-6:
                px += max(-0.5, min(0.5, 0.5 * float(row[0] - row[2]) / bend))
            bend = float(col[0] - 2 * col[1] + col[2])
            if bend < -1e-6:
                py += max(-0.5, min(0.5, 0.5 * float(col[0] - col[2]) / bend))
        return float(score), (left + px + half) / scale - self.offset[0], (top + py + half) / scale - self.offset[1]

    def _search(self, canvas: np.ndarray, headings, around, reach: float, coarse: bool) -> tuple[float, float, float, float]:
        best = (-1.0, 0.0, 0.0, 0.0)
        for heading in headings:
            score, x, y = self._match(canvas, heading, around, reach, coarse)
            if score > best[0]:
                best = (score, x, y, float(heading))
        return best

    def locate(self, rgb: np.ndarray, hint: Pose | None = None) -> Pose | None:
        """The pose on this picture (`inliers` carries the score in percent); None when the mini-map does not fit."""
        canvas = canvas_of(rgb)
        if float(self._patch(canvas, 0.0).std()) < MIN_DETAIL:
            return None  # no mini-map on this picture (a loading screen, a menu over it)
        hint = hint if hint is not None else self.last
        best = (-1.0, 0.0, 0.0, 0.0)
        if hint is not None:
            # we were just there: look near that place and that heading first (a few milliseconds)
            best = self._search(canvas, [hint.heading + d for d in range(-24, 25, 4)], (hint.x, hint.y), 14.0, False)
        if best[0] < MIN_SCORE + 0.1:
            # anywhere, any heading: coarse over the whole area, then exact around the best
            rough = self._search(canvas, range(-180, 180, 5), None, 0.0, True)
            wide = self._search(canvas, [rough[3] + d for d in (-4, -2, 0, 2, 4)], (rough[1], rough[2]), 6.0, False)
            if wide[0] > best[0]:
                best = wide
        if best[0] < MIN_SCORE:
            return None
        fine = self._search(canvas, [best[3] + d for d in (-3, -2, -1, 0, 1, 2, 3)], (best[1], best[2]), 3.0, False)
        if fine[0] > best[0]:
            best = fine
        # between the whole degrees: a parabola through the scores of the neighbours
        left = self._match(canvas, best[3] - 1.0, (best[1], best[2]), 2.0, False)[0]
        right = self._match(canvas, best[3] + 1.0, (best[1], best[2]), 2.0, False)[0]
        bend = left - 2 * best[0] + right
        heading = best[3] + (max(-0.5, min(0.5, 0.5 * (left - right) / bend)) if bend < -1e-6 else 0.0)
        pose = Pose(best[1], best[2], wrap(heading), int(round(best[0] * 100)))
        self.last = pose
        return pose
