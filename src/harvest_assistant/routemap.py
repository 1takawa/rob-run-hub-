"""The map the bot builds itself from the routes that were walked.

Every recorded run says where its author walked: which keys were held and how the camera turned. Put together
that is a path on the floor (dead reckoning: heading from the camera, distance from the time a key was held).
Different runs are laid over each other with the places where the pictures match, and the picture of the whole
way grows with every run: the doors, the stairs, the tables, and which way round the pillars the way goes.

Positions are in "seconds of walking" (1 unit = the distance covered in one second of holding W), so the map needs
no scale from the game; only the proportions matter.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

SPRINT = 1.6  # a second of Shift+W covers this many units


@dataclass
class Track:
    """One run as a path: samples (t, x, y, yaw) at every input event of the run."""
    times: list[float]
    xs: list[float]
    ys: list[float]
    yaws: list[float]       # camera heading, degrees clockwise, in the run's own zero
    vx: list[float]         # velocity held from this sample on
    vy: list[float]

    def at(self, t: float) -> tuple[float, float, float]:
        """Position and heading at the moment `t` of the run."""
        i = max(0, bisect_right(self.times, t) - 1)
        dt = max(0.0, t - self.times[i])
        return self.xs[i] + self.vx[i] * dt, self.ys[i] + self.vy[i] * dt, self.yaws[i]


def dead_reckon(events: list, degrees_per_count: float) -> Track:
    """The path of one run from its keys and camera movements."""
    ordered = sorted(events, key=lambda e: e.elapsed_offset)
    times, xs, ys, yaws, vxs, vys = [0.0], [0.0], [0.0], [0.0], [0.0], [0.0]
    x = y = yaw = 0.0
    held: set[str] = set()
    now = 0.0

    def velocity() -> tuple[float, float]:
        forward = (1.0 if "w" in held else 0.0) - (1.0 if "s" in held else 0.0)
        side = (1.0 if "d" in held else 0.0) - (1.0 if "a" in held else 0.0)
        speed = SPRINT if "shift" in held else 1.0
        heading = math.radians(yaw)
        # forward is (sin, cos) of the heading, right is (cos, -sin)
        return (speed * (forward * math.sin(heading) + side * math.cos(heading)),
                speed * (forward * math.cos(heading) - side * math.sin(heading)))

    for event in ordered:
        dt = event.elapsed_offset - now
        if dt > 0:
            vx, vy = velocity()
            x, y, now = x + vx * dt, y + vy * dt, event.elapsed_offset
        if event.kind == "key_down":
            held.add(str(event.value))
        elif event.kind == "key_up":
            held.discard(str(event.value))
        elif event.kind == "mouse_delta":
            yaw += event.value[0] * degrees_per_count
        else:
            continue
        vx, vy = velocity()
        times.append(now); xs.append(x); ys.append(y); yaws.append(yaw); vxs.append(vx); vys.append(vy)
    return Track(times, xs, ys, yaws, vxs, vys)


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Turn the point clockwise (the same sense as the camera heading)."""
    a = math.radians(degrees)
    return x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)


@dataclass
class Placement:
    """Where a run lies in the common frame: turn by `heading`, then shift by (dx, dy)."""
    heading: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    pairs: int = 0
    error: float = 0.0   # median distance between matched places after the placement, units
    valid: bool = True   # False: this run shares no place with the others, so it cannot be put on the map

    def point(self, x: float, y: float) -> tuple[float, float]:
        rx, ry = rotate(x, y, self.heading)
        return rx + self.dx, ry + self.dy


def place(reference: Track, other: Track, pairs: list[tuple[float, float, float]]) -> Placement | None:
    """Lay `other` over `reference`. `pairs` = (time in reference, time in other, heading of other as seen from the
    reference's heading, i.e. yaw_ref(t_ref) - yaw_other(t_other), degrees) for places both runs know."""
    if len(pairs) < 3:
        return None
    offsets = np.array([p[2] for p in pairs], float)
    middle = float(np.median(offsets))
    spread = np.abs(((offsets - middle) + 180) % 360 - 180)
    keep = [p for p, s in zip(pairs, spread) if s <= 12.0]  # pairs that disagree with the rest are wrong matches
    if len(keep) < 3:
        return None
    heading = float(np.median([k[2] for k in keep]))
    shifts = []
    for t_ref, t_other, _ in keep:
        rx, ry, _ = reference.at(t_ref)
        ox, oy, _ = other.at(t_other)
        px, py = rotate(ox, oy, heading)
        shifts.append((rx - px, ry - py))
    dx, dy = float(np.median([s[0] for s in shifts])), float(np.median([s[1] for s in shifts]))
    errors = [math.hypot(sx - dx, sy - dy) for sx, sy in shifts]
    return Placement(heading, dx, dy, len(keep), float(np.median(errors)))


# ---- the map of all runs together -----------------------------------------------------------------

import heapq
import json
from pathlib import Path


@dataclass
class Node:
    run: int
    index: int          # key frame index inside the run
    t: float
    x: float
    y: float
    heading: float      # camera heading in the common frame, degrees


class RouteMap:
    """All taught runs laid over each other in one frame: where every key frame is, which way the camera looked,
    where the doors and the tables are, and which key frames neighbour each other (the ways that can be walked)."""

    JOIN = 0.9   # key frames of different runs closer than this (units) are neighbours, so a way can switch runs

    def __init__(self, runs: list, plans: list, tracks: list[Track], placements: list[Placement]) -> None:
        self.runs, self.plans, self.tracks, self.placements = runs, plans, tracks, placements
        self.nodes: list[Node] = []
        self._by_run: list[list[int]] = []
        for r, (run, track, placement) in enumerate(zip(runs, tracks, placements)):
            ids = []
            for i, item in enumerate(run.keyframes if placement.valid else []):
                x, y, yaw = track.at(float(item["t"]))
                px, py = placement.point(x, y)
                ids.append(len(self.nodes))
                self.nodes.append(Node(r, i, float(item["t"]), px, py, yaw + placement.heading))
            self._by_run.append(ids)
        self._neighbours = self._link()
        self.stairs: tuple[float, float] | None = None  # see stairs_calibration; set by `build`

    # -- landmarks ------------------------------------------------------------------------

    def _marks(self, kinds: set[str]) -> list[list[tuple[float, float]]]:
        """Positions of the landmark of each kind, run by run, in the order they come in the run."""
        found: list[list[tuple[float, float]]] = []
        for r, plan in enumerate(self.plans):
            marks = []
            for step in plan.steps:
                if step.kind == "interact" and step.expected in kinds:
                    x, y, _ = self.tracks[r].at(step.t0)
                    marks.append(self.placements[r].point(x, y))
            found.append(marks)
        return found

    def doors(self) -> list[tuple[float, float]]:
        """Each door (open/close pairs in the order they are met), averaged over the runs that have it."""
        return self._average(self._marks({"open", "close"}))

    def table_marks(self) -> list[tuple[float, float]]:
        """Every place where the author marked a table (F6 or E on the table menu), in the common frame."""
        marks = []
        for r, plan in enumerate(self.plans):
            if not self.placements[r].valid:
                continue
            for t in getattr(plan, "harvest_times", []):
                x, y, _ = self.tracks[r].at(t)
                marks.append(self.placements[r].point(x, y))
        return marks

    def tables(self) -> list[tuple[float, float]]:
        """The tables in the order the author went to them (the same order in every run), averaged over the runs.
        A table pressed twice is already one mark (see TaughtPlan), and there are at most six."""
        per_run = []
        for r, plan in enumerate(self.plans):
            if not self.placements[r].valid:
                continue
            marks = []
            for t in getattr(plan, "harvest_times", []):
                x, y, _ = self.tracks[r].at(t)
                marks.append(self.placements[r].point(x, y))
            per_run.append(marks)
        return self._average(per_run)[:6]

    def table_area(self) -> tuple[float, float] | None:
        """The middle of the place where the tables stand: from here every badge within ~9 m is in view."""
        marks = self.table_marks()
        if not marks:
            return None
        return (sum(p[0] for p in marks) / len(marks), sum(p[1] for p in marks) / len(marks))

    @staticmethod
    def _average(per_run: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
        longest = max((len(m) for m in per_run), default=0)
        out = []
        for k in range(longest):
            pts = [m[k] for m in per_run if len(m) > k]
            out.append((float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))))
        return out

    # -- the graph of ways ----------------------------------------------------------------

    def _link(self) -> list[list[tuple[int, float]]]:
        neighbours: list[list[tuple[int, float]]] = [[] for _ in self.nodes]

        def connect(a: int, b: int) -> None:
            d = math.hypot(self.nodes[a].x - self.nodes[b].x, self.nodes[a].y - self.nodes[b].y)
            neighbours[a].append((b, d))
            neighbours[b].append((a, d))

        for ids in self._by_run:
            for a, b in zip(ids, ids[1:]):
                connect(a, b)
        if self.nodes:
            xy = np.array([[n.x, n.y] for n in self.nodes])
            for a in range(len(self.nodes)):
                near = np.where(np.hypot(xy[:, 0] - xy[a, 0], xy[:, 1] - xy[a, 1]) < self.JOIN)[0]
                for b in near:
                    if b > a and self.nodes[b].run != self.nodes[a].run:
                        connect(a, int(b))
        return neighbours

    def position(self, run: int, index: int) -> tuple[float, float] | None:
        if not self._by_run[run]:
            return None  # a run that could not be laid over the others
        node = self.nodes[self._by_run[run][index]]
        return node.x, node.y

    def plantation_bounds(self, margin: float = 1.2, least: float = 7.0) -> tuple[float, float, float, float] | None:
        """The part of the map past the last door (the red gates closed behind us): the stairs and the platform with the
        tables. (min x, min y, max x, max y), at least `least` units on each side so that it never zooms in too far."""
        xs: list[float] = []
        ys: list[float] = []
        for r, ids in enumerate(self._by_run):
            plan = self.plans[r]
            for i in ids:
                node = self.nodes[i]
                if plan.stage_of[node.index] >= plan.stages:
                    xs.append(node.x)
                    ys.append(node.y)
        for x, y in self.tables():
            xs.append(x)
            ys.append(y)
        if not xs:
            return None
        left, right, low, high = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
        if right - left < least:
            middle = (left + right) / 2
            left, right = middle - least / 2, middle + least / 2
        if high - low < least:
            middle = (low + high) / 2
            low, high = middle - least / 2, middle + least / 2
        return left, low, right, high

    def view(self, bot: tuple[float, float, float] | None = None, goal: tuple[float, float] | None = None,
             zoom: bool = False) -> dict:
        """The map in a form the overlay draws as its radar: thinned ways, doors, tables, the bot. With `zoom` only the
        plantation is shown (see `plantation_bounds`), large, so that the tables and the ways round the pillars are clear."""
        bounds = self.plantation_bounds() if zoom else None

        def inside(x: float, y: float) -> bool:
            return bounds is None or (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])

        paths = []
        for r, ids in enumerate(self._by_run):
            segment: list[list[float]] = []
            plan = self.plans[r]
            for i in ids[::2 if bounds else 3]:
                node = self.nodes[i]
                past_gate = plan.stage_of[node.index] >= plan.stages
                if inside(node.x, node.y) and (bounds is None or past_gate):  # zoomed: only what lies past the last door
                    segment.append([round(node.x, 2), round(node.y, 2)])
                elif segment:
                    paths.append(segment)
                    segment = []
            if segment:
                paths.append(segment)
        return {
            "paths": paths,
            "doors": [[round(x, 1), round(y, 1)] for x, y in self.doors() if inside(x, y)],
            "tables": [[round(x, 2), round(y, 2)] for x, y in self.tables()],
            "bot": [round(bot[0], 2), round(bot[1], 2), round(bot[2], 1)] if bot else None,
            "goal": [round(goal[0], 2), round(goal[1], 2)] if goal else None,
            "bounds": [round(b, 2) for b in bounds] if bounds else None,
            "title": "ПЛАНТАЦИЯ" if bounds else "РАДАР",
        }

    def nearest(self, x: float, y: float) -> int | None:
        if not self.nodes:
            return None
        return min(range(len(self.nodes)), key=lambda i: (self.nodes[i].x - x) ** 2 + (self.nodes[i].y - y) ** 2)

    def route(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
        """Points to walk through from `start` to `goal` along the ways that were really walked (a way around the
        pillars stays a way around the pillars). Empty when the map has no way."""
        a, b = self.nearest(*start), self.nearest(*goal)
        if a is None or b is None:
            return []
        best = {a: 0.0}
        came: dict[int, int] = {}
        queue = [(0.0, a)]
        while queue:
            cost, node = heapq.heappop(queue)
            if node == b:
                break
            if cost > best.get(node, math.inf):
                continue
            for other, d in self._neighbours[node]:
                if cost + d < best.get(other, math.inf):
                    best[other] = cost + d
                    came[other] = node
                    heapq.heappush(queue, (cost + d, other))
        if b not in best:
            return []
        path = [b]
        while path[-1] != a:
            path.append(came[path[-1]])
        return [(self.nodes[i].x, self.nodes[i].y) for i in reversed(path)] + [goal]

    # -- pose from a fix --------------------------------------------------------------------

    def pose(self, run: int, index: int, yaw_in_run: float) -> tuple[float, float, float] | None:
        """Common-frame position and heading for 'the camera of run `run` at key frame `index`, now turned to `yaw_in_run`'."""
        if not self._by_run[run]:
            return None
        node = self.nodes[self._by_run[run][index]]
        return node.x, node.y, yaw_in_run + self.placements[run].heading

    # -- the stairs: which way they run on this map and how long they are --------------------

    def stairs_calibration(self) -> tuple[float, float] | None:
        """(bearing in degrees clockwise from the map's north, length in units) of the way from the closed red gates to the
        end of the stairs (where the purple light of the platform first shows). The stairs are one straight way of known
        length on the plan, so this gives the turn and the scale between the map and the plan."""
        import cv2
        from .zones import is_platform

        bearings, lengths = [], []
        for r, plan in enumerate(self.plans):
            if not self.placements[r].valid:
                continue
            doors = [s for s in plan.steps if s.kind == "interact" and s.expected in ("open", "close")]
            if not doors:
                continue
            close = doors[-1]
            x, y, _ = self.tracks[r].at(close.t0)
            gate = self.placements[r].point(x, y)
            for i, item in enumerate(self.runs[r].keyframes):
                if float(item["t"]) <= close.t0:
                    continue
                image = cv2.imread(str(self.runs[r].folder / item["file"]))
                if image is None or not is_platform(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)):
                    continue
                end = self.position(r, i)
                if end is None:
                    break
                dx, dy = end[0] - gate[0], end[1] - gate[1]
                if math.hypot(dx, dy) > 0.5:
                    bearings.append(math.atan2(dx, dy))
                    lengths.append(math.hypot(dx, dy))
                break
        if not bearings:
            return None
        mean = math.degrees(math.atan2(sum(math.sin(b) for b in bearings), sum(math.cos(b) for b in bearings)))
        return mean, float(np.median(lengths))

    # -- saving ---------------------------------------------------------------------------

    def signature(self) -> list:
        return [[run.run_id, len(run.keyframes)] for run in self.runs]


def build(runs: list, plans: list, width: int, fov: float, align=None, cache: Path | None = None) -> RouteMap:
    """The map of `runs`. `align(reference_index, other_index)` -> list of (t_ref, t_other, heading offset) pairs
    for the places both runs know (see `place`). The placements are cached in `cache` (they need a few seconds of
    picture matching) and recomputed only when the set of runs changes."""
    from .taught import recorded_degrees_per_count

    per_count = recorded_degrees_per_count(width, fov)
    tracks = [dead_reckon(run.events, per_count) for run in runs]
    reference = max(range(len(runs)), key=lambda i: len(runs[i].keyframes)) if runs else 0
    signature = [[run.run_id, len(run.keyframes)] for run in runs] + [reference]
    placements: list[Placement] | None = None
    if cache is not None and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("signature") == signature:
                placements = [Placement(p["heading"], p["dx"], p["dy"], p["pairs"],
                                        math.inf if p["error"] is None else p["error"], p.get("valid", True))
                              for p in data["placements"]]
        except (OSError, ValueError, KeyError):
            placements = None
    if placements is None:
        placements = []
        for index in range(len(runs)):
            if index == reference:
                placements.append(Placement())
                continue
            placement = place(tracks[reference], tracks[index], align(reference, index)) if align is not None else None
            placements.append(placement or Placement(pairs=0, error=math.inf, valid=False))
        if cache is not None:
            try:
                cache.write_text(json.dumps({"signature": signature, "placements": [
                    {"heading": p.heading, "dx": p.dx, "dy": p.dy, "pairs": p.pairs, "valid": p.valid,
                     "error": None if math.isinf(p.error) else p.error} for p in placements]}), encoding="utf-8")
            except OSError:
                pass
    route_map = RouteMap(runs, plans, tracks, placements)
    stairs = None
    if cache is not None and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            stairs = tuple(data["stairs"]) if data.get("stairs") else None
        except (OSError, ValueError, KeyError):
            stairs = None
    if stairs is None and route_map.nodes:
        stairs = route_map.stairs_calibration()
        if stairs is not None and cache is not None and cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                data["stairs"] = list(stairs)
                cache.write_text(json.dumps(data), encoding="utf-8")
            except (OSError, ValueError):
                pass
    route_map.stairs = stairs
    return route_map


def align_by_pictures(runs: list, plans: list, width: int, fov: float):
    """The `align` of `build`: the places two runs know in common, found by matching their key frames."""
    import cv2
    from .taught import Localizer, pixels_to_degrees

    def align(reference: int, other: int) -> list[tuple[float, float, float]]:
        localizer = Localizer([runs[reference]])
        pairs = []
        for j in range(0, len(runs[other].keyframes), 2):
            item = runs[other].keyframes[j]
            image = cv2.imread(str(runs[other].folder / item["file"]))
            if image is None:
                continue
            rgb = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (width, int(width * 0.8)))
            hit = localizer.locate(rgb)
            if hit is None or hit.inliers < 20:
                continue
            t_ref = float(runs[reference].keyframes[hit.index]["t"])
            heading = plans[reference].yaw_at(t_ref) - pixels_to_degrees(hit.shift_x, width, fov) - plans[other].yaw_at(float(item["t"]))
            pairs.append((t_ref, float(item["t"]), heading))
        return pairs

    return align


def render(route_map: RouteMap, size: tuple[int, int] = (900, 640), bot: tuple[float, float, float] | None = None,
           goal: tuple[float, float] | None = None, route: list | None = None, zoom: bool = False) -> "np.ndarray":
    """A picture of the map (RGB): the ways of every run, the doors, the tables, the bot and where it is going."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    image = Image.new("RGB", size, (16, 23, 25))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("segoeui.ttf", 15)
        small = ImageFont.truetype("segoeui.ttf", 12)
    except OSError:
        font = small = ImageFont.load_default()
    if not route_map.nodes:
        return np.asarray(image)
    bounds = route_map.plantation_bounds() if zoom else None
    if bounds is not None:
        xs, ys = [bounds[0], bounds[2]], [bounds[1], bounds[3]]
    else:
        xs = [n.x for n in route_map.nodes]
        ys = [n.y for n in route_map.nodes]
    pad = 50
    scale = min((width - 2 * pad) / max(1e-6, max(xs) - min(xs)), (height - 2 * pad) / max(1e-6, max(ys) - min(ys)))

    def to_px(x: float, y: float) -> tuple[float, float]:
        return pad + (x - min(xs)) * scale, height - pad - (y - min(ys)) * scale  # north up

    colours = [(96, 112, 107), (121, 207, 255), (176, 108, 240), (241, 198, 109), (199, 243, 106)]
    for r, ids in enumerate(route_map._by_run):
        plan = route_map.plans[r]
        if bounds is not None:  # zoomed: only what lies past the last door, as separate pieces
            pieces: list[list] = [[]]
            for i in ids:
                node = route_map.nodes[i]
                if plan.stage_of[node.index] >= plan.stages:
                    pieces[-1].append(to_px(node.x, node.y))
                elif pieces[-1]:
                    pieces.append([])
        else:
            pieces = [[to_px(route_map.nodes[i].x, route_map.nodes[i].y) for i in ids]]
        for points in pieces:
            if len(points) > 1:
                draw.line(points, fill=colours[r % len(colours)], width=2)
    for k, (x, y) in enumerate(route_map.doors()):
        px, py = to_px(x, y)
        draw.rectangle((px - 6, py - 6, px + 6, py + 6), outline=(121, 207, 255), width=2)
        draw.text((px + 9, py - 9), f"дверь {k // 2 + 1}{'а' if k % 2 == 0 else 'б'}", fill=(121, 207, 255), font=small)
    for k, (x, y) in enumerate(route_map.tables()):
        px, py = to_px(x, y)
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), outline=(241, 198, 109), width=3)
        draw.text((px + 10, py - 9), f"стол {k + 1}", fill=(241, 198, 109), font=font)
    if route:
        draw.line([to_px(x, y) for x, y in route], fill=(255, 255, 255), width=3)
    if goal is not None:
        px, py = to_px(*goal)
        draw.line((px - 9, py, px + 9, py), fill=(199, 243, 106), width=3)
        draw.line((px, py - 9, px, py + 9), fill=(199, 243, 106), width=3)
    if bot is not None:
        px, py = to_px(bot[0], bot[1])
        a = math.radians(bot[2])
        tip = (px + 14 * math.sin(a), py - 14 * math.cos(a))
        left = (px + 8 * math.sin(a + 2.5), py - 8 * math.cos(a + 2.5))
        right = (px + 8 * math.sin(a - 2.5), py - 8 * math.cos(a - 2.5))
        draw.polygon([tip, left, right], fill=(199, 243, 106))
    if bounds is not None:  # a grid of one unit (one second of walking) so that distances can be judged
        for gx in range(math.ceil(bounds[0]), int(bounds[2]) + 1):
            draw.line([to_px(gx, bounds[1]), to_px(gx, bounds[3])], fill=(32, 42, 44), width=1)
        for gy in range(math.ceil(bounds[1]), int(bounds[3]) + 1):
            draw.line([to_px(bounds[0], gy), to_px(bounds[2], gy)], fill=(32, 42, 44), width=1)
    draw.text((14, 10), ("Плантация (приближено) · клетка = 1 секунда ходьбы" if bounds else
                         "Карта по пройденным маршрутам · 1 деление = 1 секунда ходьбы"), fill=(147, 160, 153), font=font)
    return np.asarray(image)
