"""Teach by demonstration: the user walks the way, the hub keeps what it saw, then finds its own way.

A *run* is one demonstration: the recorded input events plus a key frame every ~0.6 s. To repeat the
way the bot looks at the screen, finds the most similar key frame of any run (that is "where am I"),
corrects its heading to match, replays the short piece of the recording that follows that frame, and
looks again. Runs started from different spots of the spawn room (with a slow look around first)
make every spot a known place.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .models import RouteEvent
from .radar import RadarExtractor, RadarFeatures

FEATURE_WIDTH = 640
MIN_INLIERS = 14
DUPLICATE_DIFF = 1.5  # mean grey-level difference of two 32x26 thumbnails below which a frame is a repeat
CANDIDATES = 10  # key frames verified one by one per look
# Parts of the screen that carry the scene, as fractions (left, top, right, bottom). Everything else is
# HUD (minimap, chat, hotbar, compass, our own overlay) or the player model, which never moves on screen.
SCENE_REGIONS = ((0.20, 0.10, 0.98, 0.40), (0.50, 0.40, 0.98, 0.88))


@dataclass
class Features:
    points: np.ndarray       # (N, 2) float32, in FEATURE_WIDTH pixels
    descriptors: np.ndarray  # (N, 128) float32


@dataclass
class TaughtRun:
    run_id: str
    created: float
    duration: float
    events: list[RouteEvent]
    keyframes: list[dict]    # {"file": "frame_0001.jpg", "t": 0.6}
    folder: Path
    features: list[Features] = field(default_factory=list)
    radar: list = field(default_factory=list)  # RadarFeatures | None per key frame: the mini-map of that moment

    @property
    def label(self) -> str:
        return time.strftime("Заход %d.%m %H:%M", time.localtime(self.created))


@dataclass(frozen=True)
class Location:
    run: int
    index: int
    inliers: int
    shift_x: float  # screen px: how far the scene is displaced to the right compared with the key frame
    shift_y: float = 0.0  # screen px: how far the scene is displaced down compared with the key frame
    scale: float = 1.0    # how big the scene looks compared with the key frame (<1: we stand farther away)
    points: tuple = ()    # a sample of the matched points on the current screen, (x, y) in screen px


@dataclass(frozen=True)
class Projection:
    """Where a chosen part of a key frame (the door, the end of a walk) is on the current screen."""
    quad: tuple          # four (x, y) screen points, clockwise from the top left
    scale: float         # <1: that place is farther away than when it was recorded
    inliers: int

    @property
    def center(self) -> tuple[float, float]:
        return (sum(p[0] for p in self.quad) / 4, sum(p[1] for p in self.quad) / 4)


def scene_mask(height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), np.uint8)
    for left, top, right, bottom in SCENE_REGIONS:
        mask[int(top * height):int(bottom * height), int(left * width):int(right * width)] = 255
    return mask


class Extractor:
    def __init__(self) -> None:
        self._sift = cv2.SIFT_create(nfeatures=700)

    def extract(self, rgb: np.ndarray) -> Features | None:
        height, width = rgb.shape[:2]
        scale = FEATURE_WIDTH / width
        gray = cv2.cvtColor(cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        points, descriptors = self._sift.detectAndCompute(gray, scene_mask(*gray.shape))
        if descriptors is None or len(points) < 12:
            return None
        return Features(np.float32([p.pt for p in points]), descriptors.astype(np.float32))


def jpeg_frame(rgb: np.ndarray) -> bytes:
    scale = FEATURE_WIDTH / rgb.shape[1]
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return encoded.tobytes() if ok else b""


class TaughtStore:
    """Runs on disk: ``taught/<run id>/run.json`` + frames + cached features."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._extractor = Extractor()
        self._radar = RadarExtractor()

    @staticmethod
    def _save_radar(path: Path, feats: RadarFeatures | None) -> None:
        points = feats.points if feats is not None else np.zeros((0, 2), np.float32)
        descriptors = feats.descriptors if feats is not None else np.zeros((0, 128), np.float32)
        np.savez_compressed(path, points=points, descriptors=np.clip(np.rint(descriptors), 0, 255).astype(np.uint8))

    def _radar_of(self, jpg_path: Path, cache: Path, rgb: np.ndarray | None = None) -> RadarFeatures | None:
        """Mini-map features of one key frame, cached next to it (computed once)."""
        if cache.exists():
            try:
                data = np.load(cache)
                return RadarFeatures(data["points"], data["descriptors"].astype(np.float32)) if len(data["points"]) >= 6 else None
            except (OSError, ValueError, KeyError):
                pass
        if rgb is None:
            image = cv2.imread(str(jpg_path))
            if image is None:
                return None
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        feats = self._radar.extract(rgb)
        self._save_radar(cache, feats)
        return feats

    def add_run(self, events: list[RouteEvent], frames: list[tuple[float, bytes]], duration: float) -> TaughtRun:
        created = time.time()
        run_id = time.strftime("run_%Y%m%d_%H%M%S", time.localtime(created))
        folder = self.folder / run_id
        folder.mkdir(parents=True, exist_ok=True)
        keyframes, features, radar = [], [], []
        previous = None
        for number, (offset, data) in enumerate(frames, start=1):
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                continue
            thumb = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (32, 26)).astype(np.float32)
            if previous is not None and float(np.abs(thumb - previous).mean()) < DUPLICATE_DIFF:
                continue  # standing still: the same picture again adds nothing but weight
            previous = thumb
            feats = self._extractor.extract(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            if feats is None:
                continue  # a frame with nothing recognisable (dark, close to a wall) is useless as a landmark
            name = f"frame_{number:04d}.jpg"
            (folder / name).write_bytes(data)
            # SIFT descriptors are whole numbers 0..255: a quarter of the disk space, same matches
            np.savez_compressed(folder / f"frame_{number:04d}.npz", points=feats.points,
                                descriptors=np.clip(np.rint(feats.descriptors), 0, 255).astype(np.uint8))
            keyframes.append({"file": name, "t": float(offset)})
            features.append(feats)
            radar.append(self._radar_of(folder / name, folder / f"frame_{number:04d}.radar.npz", cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
        payload = {"created": created, "duration": duration, "keyframes": keyframes,
                   "events": [asdict(event) for event in events]}
        (folder / "run.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return TaughtRun(run_id, created, duration, events, keyframes, folder, features, radar)

    def load_runs(self) -> list[TaughtRun]:
        runs: list[TaughtRun] = []
        for path in sorted(self.folder.glob("run_*/run.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                events = []
                for raw in data["events"]:
                    raw = dict(raw)
                    if raw.get("kind") == "mouse_delta" and isinstance(raw.get("value"), list):
                        raw["value"] = tuple(raw["value"])
                    events.append(RouteEvent(**raw))
                features = []
                for item in data["keyframes"]:
                    cache = np.load(path.parent / item["file"].replace(".jpg", ".npz"))
                    features.append(Features(cache["points"], cache["descriptors"].astype(np.float32)))
                radar = [self._radar_of(path.parent / item["file"], path.parent / item["file"].replace(".jpg", ".radar.npz"))
                         for item in data["keyframes"]]
                runs.append(TaughtRun(path.parent.name, float(data["created"]), float(data["duration"]), events,
                                      data["keyframes"], path.parent, features, radar))
            except (OSError, ValueError, KeyError):
                continue
        return runs

    def delete_run(self, run_id: str) -> None:
        shutil.rmtree(self.folder / run_id, ignore_errors=True)


class Localizer:
    """Finds the key frame that looks most like the current screen."""

    def __init__(self, runs: list[TaughtRun], extractor: Extractor | None = None) -> None:
        self.runs = runs
        self._extractor = extractor or Extractor()
        self._owner: list[tuple[int, int]] = []
        descriptors, points = [], []
        for run_index, run in enumerate(runs):
            for key_index, feats in enumerate(run.features):
                descriptors.append(feats.descriptors)
                points.append(feats.points)
                self._owner.extend([(run_index, key_index)] * len(feats.points))
        self._empty = not descriptors
        if self._empty:
            return
        self._descriptors = np.vstack(descriptors)
        self._points = np.vstack(points)
        self._matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 48})
        self._matcher.add([self._descriptors])
        self._matcher.train()
        self._brute = cv2.BFMatcher(cv2.NORM_L2)

    def locate(self, rgb: np.ndarray, allowed: Callable[[int, int], bool] | None = None) -> Location | None:
        """Best matching key frame; `allowed(run, index)` rules key frames out (wrong stage of the way)."""
        if self._empty:
            return None
        frame = self._extractor.extract(rgb)
        self._last = (frame, rgb.shape[1] / FEATURE_WIDTH, rgb.shape[0] / rgb.shape[1])
        if frame is None:
            return None
        # Neighbouring key frames are nearly identical, so the "best vs second best" ratio test on the merged
        # index rejects almost everything. Instead: let the index nominate candidate key frames by votes,
        # then verify every candidate on its own (ratio test inside that single frame + RANSAC).
        near = self._matcher.knnMatch(frame.descriptors, k=6)
        votes: Counter = Counter()
        for neighbours in near:
            for rank, match in enumerate(neighbours):
                owner = self._owner[match.trainIdx]
                if allowed is None or allowed(*owner):
                    votes[owner] += 6 - rank
        best: Location | None = None
        scale = rgb.shape[1] / FEATURE_WIDTH
        for owner, _count in votes.most_common(CANDIDATES):
            key = self.runs[owner[0]].features[owner[1]]
            pairs = self._brute.knnMatch(frame.descriptors, key.descriptors, k=2)
            matches = [(m.queryIdx, m.trainIdx) for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
            if len(matches) < 8:
                continue
            key_points = np.float32([key.points[t] for _q, t in matches]).reshape(-1, 1, 2)
            frame_points = np.float32([frame.points[q] for q, _t in matches]).reshape(-1, 1, 2)
            matrix, mask = cv2.findHomography(key_points, frame_points, cv2.RANSAC, 6.0)
            if matrix is None or mask is None:
                continue
            inliers = int(mask.sum())
            if inliers < MIN_INLIERS or (best is not None and inliers <= best.inliers):
                continue
            keep = mask.ravel().astype(bool)
            shift = float(np.median(frame_points[keep, 0, 0] - key_points[keep, 0, 0]))
            shift_y = float(np.median(frame_points[keep, 0, 1] - key_points[keep, 0, 1]))
            size = math.sqrt(abs(float(np.linalg.det(matrix[:2, :2])))) if matrix is not None else 1.0
            sample = frame_points[keep, 0, :][:: max(1, inliers // 30)] * scale
            best = Location(owner[0], owner[1], inliers, shift * scale, shift_y * scale,
                            size if 0.2 < size < 5.0 else 1.0, tuple((float(x), float(y)) for x, y in sample))
        return best

    def project(self, run: int, index: int, box: tuple = (0.38, 0.30, 0.62, 0.62)) -> Projection | None:
        """Where `box` (fractions of the key frame) is on the screen last given to `locate`; None if unseen."""
        last = getattr(self, "_last", None)
        if self._empty or last is None or last[0] is None:
            return None
        frame, scale, aspect = last
        key = self.runs[run].features[index]
        pairs = self._brute.knnMatch(frame.descriptors, key.descriptors, k=2)
        matches = [(m.queryIdx, m.trainIdx) for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(matches) < 8:
            return None
        key_points = np.float32([key.points[t] for _q, t in matches]).reshape(-1, 1, 2)
        frame_points = np.float32([frame.points[q] for q, _t in matches]).reshape(-1, 1, 2)
        matrix, mask = cv2.findHomography(key_points, frame_points, cv2.RANSAC, 6.0)
        if matrix is None or mask is None or int(mask.sum()) < 12:
            return None
        height = FEATURE_WIDTH * aspect
        left, top, right, bottom = box[0] * FEATURE_WIDTH, box[1] * height, box[2] * FEATURE_WIDTH, box[3] * height
        corners = np.float32([[left, top], [right, top], [right, bottom], [left, bottom]]).reshape(-1, 1, 2)
        quad = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2) * scale
        width_px = FEATURE_WIDTH * scale
        if not np.isfinite(quad).all() or abs(quad).max() > 3 * width_px:
            return None
        area = 0.5 * abs(float(np.dot(quad[:, 0], np.roll(quad[:, 1], -1)) - np.dot(quad[:, 1], np.roll(quad[:, 0], -1))))
        if area < 100:
            return None
        size = math.sqrt(abs(float(np.linalg.det(matrix[:2, :2]))))
        return Projection(tuple((float(x), float(y)) for x, y in quad), size, int(mask.sum()))


# ---- the demonstration as a plan: where to look and what to do, not a movie of mouse moves ------------

# Recorded mouse counts per picture pixel of camera movement, measured on real recordings (the picture
# moved ~1 px for every 4.2 raw counts). Only used to turn the recorded camera motion into degrees.
COUNTS_PER_PX = 4.2
MERGE_GAP = 0.5  # taps of the same key closer than this are one walk


def pixels_to_degrees(pixels: float, width: int, fov: float) -> float:
    focal = (width / 2) / math.tan(math.radians(fov / 2))
    return math.degrees(math.atan(pixels / focal))


def recorded_degrees_per_count(width: int, fov: float) -> float:
    return pixels_to_degrees(1.0, width, fov) / COUNTS_PER_PX


def wrap_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class Step:
    kind: str       # "move" | "interact"
    t0: float
    t1: float
    key: str        # w/a/s/d for a move
    shift: bool
    hold: float     # seconds of key held in total
    expected: str   # "open"/"close" for an interact
    yaw: float      # where the camera looked (degrees, the recording's own zero, right = +)
    pitch: float    # degrees, down = +


class TaughtPlan:
    """One demonstration reduced to its meaning: walk steps and door presses, each with its heading.

    Camera movement is *not* replayed. The bot works out its own heading from the picture (against the
    key frames) and turns to the heading a step had in the demonstration, so it keeps working when the
    start differs a little or the demonstrator looked around for a long time.
    """

    def __init__(self, run: TaughtRun, width: int, fov: float) -> None:
        self.run = run
        per_count = recorded_degrees_per_count(width, fov)
        moves = sorted((e for e in run.events if e.kind == "mouse_delta"), key=lambda e: e.elapsed_offset)
        self._times = [e.elapsed_offset for e in moves]
        self._yaw: list[float] = []
        self._pitch: list[float] = []
        yaw = pitch = 0.0
        for event in moves:
            yaw += event.value[0] * per_count
            pitch += event.value[1] * per_count
            self._yaw.append(yaw)
            self._pitch.append(pitch)
        self.steps = self._build_steps(run)
        # Where the author pressed E at a table: these are goals for the map, not steps to replay. The way itself ends
        # at the first of them; from there the tables are reached over the map (see routemap.py / navigator.py).
        self.harvest_times = self._tables_marked(run)
        if self.harvest_times:
            first = self.harvest_times[0]
            self.steps = [s for s in self.steps if s.t0 < first - 0.5]
        self.key_times = [float(item["t"]) for item in run.keyframes]
        door_times = [step.t0 for step in self.steps if step.kind == "interact"]
        self.stages = len(door_times)
        # Stage of a key frame = doors already passed when it was taken. A frame taken at the very moment of
        # E still shows the door in front of you, so it counts as "before".
        self.stage_of = [sum(1 for moment in door_times if moment < t - 0.25) for t in self.key_times]

    @staticmethod
    def _tables_marked(run: TaughtRun) -> list[float]:
        """When the author stood at each table. Marks with no step taken between them are one table, pressed twice
        (F6 and E, or E twice)."""
        events = sorted(run.events, key=lambda e: e.elapsed_offset)
        marks: list[float] = []
        walked = True  # nothing before the first mark can be a duplicate
        for event in events:
            if event.kind == "key_down" and str(event.value) in ("w", "a", "s", "d"):
                walked = True
            elif event.kind == "interact" and str(event.value or "") == "harvest":
                if walked:
                    marks.append(event.elapsed_offset)
                walked = False
        return marks   # every pass over the rows counts: the places are clustered later (map_route)

    def yaw_at(self, t: float) -> float:
        i = bisect_right(self._times, t) - 1
        return self._yaw[i] if i >= 0 else 0.0

    def pitch_at(self, t: float) -> float:
        i = bisect_right(self._times, t) - 1
        return self._pitch[i] if i >= 0 else 0.0

    def _build_steps(self, run: TaughtRun) -> list[Step]:
        events = sorted(run.events, key=lambda e: e.elapsed_offset)
        down: dict[str, float] = {}
        holds: list[tuple[str, float, float]] = []
        shifts: list[tuple[float, float]] = []
        for event in events:
            if event.kind == "key_down":
                down[str(event.value)] = event.elapsed_offset
            elif event.kind == "key_up":
                start = down.pop(str(event.value), None)
                if start is None:
                    continue
                if str(event.value) == "shift":
                    shifts.append((start, event.elapsed_offset))
                else:
                    holds.append((str(event.value), start, event.elapsed_offset))
        merged: list[list] = []  # [key, t0, t1, hold]
        for key, t0, t1 in sorted(holds, key=lambda item: item[1]):
            for item in reversed(merged):
                if item[0] == key and t0 - item[2] <= MERGE_GAP:
                    item[3] += t1 - t0
                    item[2] = t1
                    break
            else:
                merged.append([key, t0, t1, t1 - t0])
        steps = [
            Step("move", t0, t1, key, any(a <= t0 <= b for a, b in shifts), hold, "", self.yaw_at(t0), self.pitch_at(t0))
            for key, t0, t1, hold in merged
        ]
        last_door = ""
        for event in events:
            # An E at a table («harvest») is not replayed: at the tables the pilot takes over (badge, check, harvest).
            if event.kind == "interact" and str(event.value or "") != "harvest":
                t = event.elapsed_offset
                value = str(event.value or "")
                if value not in ("open", "close") and last_door:
                    value = "open" if last_door == "close" else "close"   # the recorder did not read this prompt: doors alternate
                if value in ("open", "close"):
                    last_door = value
                steps.append(Step("interact", t, t, "", False, 0.0, value, self.yaw_at(t), self.pitch_at(t)))
        return sorted(steps, key=lambda step: step.t0)

    def after_open_ok(self, index: int, stage: int, window: float = 3.0) -> bool:
        """Right after a door was opened (and walked through by the bot itself) only the key frames of the last `window`
        seconds before the next door of the demonstration may be matched: those are the little steps and the turn that
        bring the author to the door to close it. Earlier ones are the walk through, which the bot has just done."""
        doors = [s.t0 for s in self.steps if s.kind == "interact"]
        return stage >= len(doors) or self.key_times[index] >= doors[stage] - window

    def next_step(self, t: float) -> Step | None:
        """What is left to do from the moment `t` of the demonstration (the step still running counts)."""
        for step in self.steps:
            if step.t1 > t or (step.kind == "interact" and step.t0 >= t - 0.25):
                return step
        return None

    def target_index(self, step: Step, stage: int) -> int | None:
        """The key frame that shows where this step ends: the door at its E press, the spot where a walk stops."""
        moment = step.t0 if step.kind == "interact" else step.t1
        candidates = [i for i, value in enumerate(self.stage_of) if value == stage]
        if not candidates:
            return None
        return min(candidates, key=lambda i: abs(self.key_times[i] - moment))

    def remaining_hold(self, step: Step, t: float) -> float:
        if step.kind != "move" or step.t1 <= step.t0:
            return step.hold
        done = max(0.0, min(1.0, (t - step.t0) / (step.t1 - step.t0)))
        return step.hold * (1.0 - done)
