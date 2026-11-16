"""The mini radar: the game's own mini-map as a second way to know where we are and which way we face.

The mini-map in the top left corner keeps the player in its middle and TURNS with the camera: the buildings
and sand patches on it rotate when the camera turns and slide when we walk. Registering two mini-maps (a
rotation + a shift that make one fit the other) therefore gives the turn of the camera and the step taken between
the two moments, whatever the light or the picture of the world looks like (inside the dark container as well).

On recorded runs this agreed with the recorded camera turns within about 1 degree, and it found the mini-map of
one run among those of another on every frame tried.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

MINIMAP_BOX = (0.028, 0.03, 0.145, 0.155)  # left, top, right, bottom as fractions of the frame
WORK_WIDTH = 640                           # frames are looked at at this width, like the stored key frames
CANVAS = (360, 300)                        # the mini-map is brought to this size (width, height)
MIN_INLIERS = 8


@dataclass
class RadarFeatures:
    points: np.ndarray       # (N, 2) float32 in CANVAS pixels
    descriptors: np.ndarray  # (N, 128) float32


@dataclass(frozen=True)
class RadarFix:
    run: int
    index: int
    inliers: int
    rotation: float   # degrees the map of the live picture is turned compared with the key frame's map
    shift: float      # pixels (CANVAS) between the two positions: the smaller, the nearer the key frame


def prepare(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Grey mini-map of a fixed size without the lime icons (the player arrow and the home marker)."""
    height, width = rgb.shape[:2]
    if width != WORK_WIDTH:
        scale = WORK_WIDTH / width
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = rgb.shape[:2]
    left, top, right, bottom = MINIMAP_BOX
    crop = rgb[int(top * height):int(bottom * height), int(left * width):int(right * width)]
    crop = cv2.resize(crop, CANVAS, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    lime = ((hsv[..., 0] >= 28) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).astype(np.uint8) * 255
    lime = cv2.dilate(lime, np.ones((7, 7), np.uint8))
    grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return cv2.inpaint(grey, lime, 5, cv2.INPAINT_TELEA), lime


class RadarExtractor:
    def __init__(self) -> None:
        self._sift = cv2.SIFT_create(nfeatures=300, contrastThreshold=0.02)

    def extract(self, rgb: np.ndarray) -> RadarFeatures | None:
        grey, lime = prepare(rgb)
        points, descriptors = self._sift.detectAndCompute(grey, 255 - lime)
        if descriptors is None or len(points) < 6:
            return None
        return RadarFeatures(np.float32([p.pt for p in points]), descriptors.astype(np.float32))


_BRUTE = cv2.BFMatcher()


def register(a: RadarFeatures, b: RadarFeatures) -> tuple[float, float, float, int] | None:
    """The turn (degrees), the shift (CANVAS pixels along x and y) and the inlier count that carry map `a` onto map `b`."""
    pairs = _BRUTE.knnMatch(a.descriptors, b.descriptors, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.8 * n.distance]
    if len(good) < MIN_INLIERS:
        return None
    source = np.float32([a.points[m.queryIdx] for m in good])
    target = np.float32([b.points[m.trainIdx] for m in good])
    matrix, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if matrix is None or inliers is None or int(inliers.sum()) < MIN_INLIERS:
        return None
    scale = math.hypot(matrix[0, 0], matrix[1, 0])
    if not 0.85 < scale < 1.18:  # the mini-map never zooms: a different scale is a wrong match
        return None
    centre = np.float32([[CANVAS[0] / 2, CANVAS[1] / 2]]).reshape(-1, 1, 2)
    moved = cv2.transform(centre, matrix).reshape(2)
    return (math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])), float(moved[0] - centre[0, 0, 0]),
            float(moved[1] - centre[0, 0, 1]), int(inliers.sum()))


class RadarIndex:
    """Where on a taught way is this mini-map? The mini-maps of all key frames of all runs, searched like pictures."""

    CANDIDATES = 10

    def __init__(self, runs: list, extractor: RadarExtractor | None = None) -> None:
        self.runs = runs
        self._extractor = extractor or RadarExtractor()
        self._owner: list[tuple[int, int]] = []
        descriptors = []
        for run_index, run in enumerate(runs):
            for key_index, feats in enumerate(getattr(run, "radar", [])):
                if feats is None:
                    continue
                descriptors.append(feats.descriptors)
                self._owner.extend([(run_index, key_index)] * len(feats.points))
        self._empty = not descriptors
        if self._empty:
            return
        self._matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 48})
        self._matcher.add([np.vstack(descriptors)])
        self._matcher.train()

    @property
    def usable(self) -> bool:
        return not self._empty

    def locate(self, rgb: np.ndarray, allowed: Callable[[int, int], bool] | None = None) -> RadarFix | None:
        if self._empty:
            return None
        live = self._extractor.extract(rgb)
        if live is None:
            return None
        votes: Counter = Counter()
        for neighbours in self._matcher.knnMatch(live.descriptors, k=6):
            for rank, match in enumerate(neighbours):
                owner = self._owner[match.trainIdx]
                if allowed is None or allowed(*owner):
                    votes[owner] += 6 - rank
        verified: list[RadarFix] = []
        for (run_index, key_index), _count in votes.most_common(self.CANDIDATES):
            key = self.runs[run_index].radar[key_index]
            result = register(key, live)
            if result is None:
                continue
            rotation, dx, dy, inliers = result
            verified.append(RadarFix(run_index, key_index, inliers, rotation, math.hypot(dx, dy)))
        if not verified:
            return None
        # Several key frames fit the map (it looks alike along the way): take the one at the nearest spot
        solid = [fix for fix in verified if fix.inliers >= max(MIN_INLIERS, 0.5 * max(f.inliers for f in verified))]
        return min(solid, key=lambda fix: fix.shift)
