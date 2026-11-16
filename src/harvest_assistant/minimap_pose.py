"""Where am I and which way do I look, read from the game's mini-map alone.

The mini-map in the corner turns with the camera around the player and slides as the player walks. Fitting the live
mini-map onto a stored one (a turn and a shift) therefore gives the camera heading AND the place of the player relative
to the stored one, whatever the 3D picture looks like: in the house, in the sun of the yard, in the dark container.

All poses are in one frame, the same for every taught run:
  * the origin is the spot in front of the house door where the door was opened in the reference run;
  * heading 0 looks at the house door from inside, positive is to the right (clockwise seen from above);
  * x is to the right and y is BACKWARDS at heading 0, in mini-map pixels of the working canvas (see radar.CANVAS).
    Walking forward at heading h moves by (sin h, -cos h).

Checked on the five recorded runs (650 key frames): every frame is located, the doors of all runs land within about
2 px of each other, and the heading agrees with the recorded camera turns within 1-2 degrees.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from .radar import CANVAS, RadarExtractor, RadarFeatures, register

logger = logging.getLogger(__name__)

# The point of the canvas the mini-map turns around (the player). It is not the exact middle of the cropped box;
# fitted on the stretches of the recorded runs where the author turned on the spot (see tests / calibrate_pivot).
PIVOT = (CANVAS[0] / 2 + 2.0, CANVAS[1] / 2 + 5.0)
PX_PER_SECOND = 9.2      # canvas pixels covered by one second of holding W (measured on the 8.8 s walk over the yard)
MIN_INLIERS = 8
REFERENCE_SPACING = 5.0  # canvas pixels between two stored maps
STRONG_INLIERS = 14


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    heading: float       # degrees, 0 = looking at the house door from inside, right = +
    inliers: int = 0

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.x, y - self.y)

    def bearing_to(self, x: float, y: float) -> float:
        """The heading that looks from here straight at (x, y)."""
        return math.degrees(math.atan2(x - self.x, -(y - self.y)))

    def turn_to(self, x: float, y: float) -> float:
        """Degrees to turn (right = +) to look at (x, y)."""
        return wrap(self.bearing_to(x, y) - self.heading)


def wrap(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def pose_from(reference: Pose, fit: tuple[float, float, float, int], pivot: tuple[float, float] = PIVOT) -> Pose:
    """The pose of the live mini-map, from the pose of a stored one and the fit `radar.register(stored, live)`."""
    rotation, dx, dy, inliers = fit
    # `register` reports how far the middle of the canvas moved; what matters is how far the pivot (the player of the
    # stored map) moved, which differs by the turn applied to the offset between the two points.
    off_x, off_y = pivot[0] - CANVAS[0] / 2, pivot[1] - CANVAS[1] / 2
    turned = _rotate(off_x, off_y, rotation)
    move_x, move_y = dx + turned[0] - off_x, dy + turned[1] - off_y
    heading = wrap(reference.heading - rotation)
    world = _rotate(move_x, move_y, heading)
    return Pose(reference.x - world[0], reference.y - world[1], heading, inliers)


class MapLocator:
    """Stored mini-maps with known poses; `locate` gives the pose of a live picture."""

    def __init__(self, references: list[tuple[RadarFeatures, Pose]], extractor: RadarExtractor | None = None,
                 sources: list[tuple[int, int]] | None = None) -> None:
        self.references = references
        self.sources = sources or []          # (run, key frame) of every reference, for the cache on disk
        self._extractor = extractor or RadarExtractor()
        self.last: Pose | None = None

    @property
    def usable(self) -> bool:
        return bool(self.references)

    def dump(self) -> list[list[float]]:
        return [[run, key, pose.x, pose.y, pose.heading] for (run, key), (_f, pose) in zip(self.sources, self.references)]

    @classmethod
    def restore(cls, runs: list, rows: list, extractor: RadarExtractor | None = None) -> "MapLocator":
        references, sources = [], []
        for run, key, x, y, heading in rows:
            feats = runs[int(run)].radar[int(key)]
            if feats is None:
                raise ValueError("a cached reference has no mini-map")
            references.append((feats, Pose(float(x), float(y), float(heading), 999)))
            sources.append((int(run), int(key)))
        return cls(references, extractor, sources)

    @classmethod
    def from_runs(cls, runs: list, plans: list, extractor: RadarExtractor | None = None) -> "MapLocator":
        """Build the references from the taught runs. The origin is the first door of the run with the most
        mini-map detail at that door; the other key frames get their pose by being fitted onto what is already known."""
        best: tuple[int, int, int] | None = None  # (detail, run, key frame)
        for run_index, (run, plan) in enumerate(zip(runs, plans)):
            doors = [step.t0 for step in plan.steps if step.kind == "interact"]
            if not doors or not plan.key_times:
                continue
            index = min(range(len(plan.key_times)), key=lambda i: abs(plan.key_times[i] - doors[0]))
            feats = run.radar[index] if index < len(run.radar) else None
            if feats is not None and (best is None or len(feats.points) > best[0]):
                best = (len(feats.points), run_index, index)
        if best is None:
            return cls([], extractor)
        _detail, origin_run, origin_index = best
        radar = runs[origin_run].radar
        poses: dict[int, Pose] = {origin_index: Pose(0.0, 0.0, 0.0, 999)}

        def chain(order: list[int]) -> None:
            """Walk along the run: every key frame is fitted onto the few before it. Neighbours differ by a small step,
            so these fits are strong and the poses stay consistent with each other from one end of the way to the other."""
            done: list[int] = [origin_index]
            for index in order:
                if radar[index] is None:
                    continue
                best_fit: tuple[tuple, int] | None = None
                for previous in done[-4:]:
                    fit = register(radar[previous], radar[index])
                    if fit is not None and fit[3] >= 10 and (best_fit is None or fit[3] > best_fit[0][3]):
                        best_fit = (fit, previous)
                if best_fit is None:
                    continue
                poses[index] = pose_from(poses[best_fit[1]], best_fit[0])
                done.append(index)

        chain(list(range(origin_index + 1, len(radar))))
        chain(list(range(origin_index - 1, -1, -1)))
        locator = cls([], extractor)
        for index in sorted(poses, key=lambda i: (i != origin_index, -len(radar[i].points))):
            pose = poses[index]
            if all(math.hypot(pose.x - other.x, pose.y - other.y) >= REFERENCE_SPACING for _f, other in locator.references):
                locator.references.append((radar[index], pose))
                locator.sources.append((origin_run, index))
        # places only the other runs visited (another corner of the room below, another spot of the house)
        for run_index, run in enumerate(runs):
            if run_index == origin_run:
                continue
            for key_index, feats in enumerate(run.radar):
                if feats is None or len(locator.references) >= 80:
                    continue
                pose = locator._locate(feats, None)
                if pose is not None and pose.inliers >= STRONG_INLIERS and all(
                        math.hypot(pose.x - other.x, pose.y - other.y) >= 2 * REFERENCE_SPACING
                        for _f, other in locator.references):
                    locator.references.append((feats, pose))
                    locator.sources.append((run_index, key_index))
        logger.info("mini-map locator: %d stored maps", len(locator.references))
        return locator

    def _locate(self, live: RadarFeatures, hint: Pose | None) -> Pose | None:
        candidates: list[Pose] = []
        for feats, pose in self.references:
            fit = register(feats, live)
            if fit is not None:
                candidates.append(pose_from(pose, fit))
        if not candidates:
            return None
        candidates.sort(key=lambda p: -p.inliers)

        def agree(a: Pose, b: Pose) -> bool:
            return math.hypot(a.x - b.x, a.y - b.y) < 7.0 and abs(wrap(a.heading - b.heading)) < 6.0

        for candidate in candidates:
            backed = sum(1 for other in candidates if other is not candidate and agree(candidate, other))
            near_hint = hint is not None and math.hypot(candidate.x - hint.x, candidate.y - hint.y) < 25.0
            if candidate.inliers >= STRONG_INLIERS or backed >= 1 or (near_hint and candidate.inliers >= 10):
                group = [candidate] + [o for o in candidates if o is not candidate and agree(candidate, o)]
                weight = float(sum(p.inliers for p in group))
                x = sum(p.x * p.inliers for p in group) / weight
                y = sum(p.y * p.inliers for p in group) / weight
                turn = sum(wrap(p.heading - candidate.heading) * p.inliers for p in group) / weight
                return Pose(x, y, wrap(candidate.heading + turn), candidate.inliers)
        return None

    def locate(self, rgb: np.ndarray, hint: Pose | None = None) -> Pose | None:
        """The pose of the player on this picture; None when the mini-map cannot be fitted (covered, loading)."""
        if not self.references:
            return None
        live = self._extractor.extract(rgb)
        if live is None:
            return None
        pose = self._locate(live, hint if hint is not None else self.last)
        if pose is not None:
            self.last = pose
        return pose
