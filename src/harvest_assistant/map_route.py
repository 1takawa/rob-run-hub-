"""The way from the house to the plantation, walked by coordinates.

The mini-map gives the place and the heading of the player on every frame (see minimap_pose). The taught runs, laid
into that same frame, give the points that matter: the spot in front of the house door, the spot on the porch, the way
over the yard, the spots in front of and behind the container gate, the stairs. The pilot walks from point to point,
checking its place several times a second, and uses the game's own prompt («ДВЕРЬ · Открыть / Закрыть») only as the
confirmation at a door. Nothing depends on recognising a 3D picture, so the light, the time of day and the exact
starting spot do not matter.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .minimap_pose import PIVOT, MapLocator, Pose, wrap
from .models import RouteEvent
from .zones import is_platform

logger = logging.getLogger(__name__)

CACHE_VERSION = 6
Point = tuple[float, float]


@dataclass
class RouteData:
    door_in: Pose                 # in front of the house door, inside; heading = looking at the door
    porch: Pose                   # on the porch where the door is closed from; heading = looking at the open door
    gate_out: Pose                # in front of the container gate; heading = looking at the gate
    gate_in: Pose                 # inside the container; heading = looking back at the gate
    stairs_bearing: float         # the heading the stairs are walked down with
    stairs_end: Point             # where the authors stopped at the bottom
    px_per_second: float          # mini-map pixels covered by one second of W
    house_paths: list[list[Point]] = field(default_factory=list)   # each ends in front of the house door
    yard_path: list[Point] = field(default_factory=list)           # from the porch to the gate
    # the room below: where the authors stood at each table (x, y, heading), and the ground they walked on as a net of
    # points joined where a recording went from one to the next. The pilot walks only along this net.
    tables: list[tuple[float, float, float]] = field(default_factory=list)
    nodes: list[Point] = field(default_factory=list)
    edges: list[tuple[int, int]] = field(default_factory=list)

    def to_json(self) -> dict:
        def pose(p: Pose) -> list[float]:
            return [p.x, p.y, p.heading]
        return {"door_in": pose(self.door_in), "porch": pose(self.porch), "gate_out": pose(self.gate_out),
                "gate_in": pose(self.gate_in), "stairs_bearing": self.stairs_bearing, "stairs_end": list(self.stairs_end),
                "px_per_second": self.px_per_second, "house_paths": self.house_paths, "yard_path": self.yard_path,
                "tables": [list(t) for t in self.tables], "nodes": [list(n) for n in self.nodes],
                "edges": [list(e) for e in self.edges]}

    @classmethod
    def from_json(cls, data: dict) -> "RouteData":
        def pose(row) -> Pose:
            return Pose(float(row[0]), float(row[1]), float(row[2]))
        return cls(pose(data["door_in"]), pose(data["porch"]), pose(data["gate_out"]), pose(data["gate_in"]),
                   float(data["stairs_bearing"]), (float(data["stairs_end"][0]), float(data["stairs_end"][1])),
                   float(data["px_per_second"]), [[(float(x), float(y)) for x, y in path] for path in data["house_paths"]],
                   [(float(x), float(y)) for x, y in data["yard_path"]],
                   [(float(x), float(y), float(h)) for x, y, h in data.get("tables", [])],
                   [(float(x), float(y)) for x, y in data.get("nodes", [])],
                   [(int(a), int(b)) for a, b in data.get("edges", [])])


def _median_pose(poses: list[Pose]) -> Pose:
    base = poses[0].heading
    return Pose(float(np.median([p.x for p in poses])), float(np.median([p.y for p in poses])),
                wrap(base + float(np.median([wrap(p.heading - base) for p in poses]))))


def _thin(points: list[Point], spacing: float) -> list[Point]:
    kept: list[Point] = []
    for point in points:
        if not kept or math.hypot(point[0] - kept[-1][0], point[1] - kept[-1][1]) >= spacing:
            kept.append(point)
    return kept


def build_route(runs: list, plans: list, locator: MapLocator) -> RouteData | None:
    """Read the points of the way out of the taught runs. A run counts when it has the four doors in order:
    house open, house close, gate open, gate close."""
    door_in, porch, gate_out, gate_in, ends, bearings, speeds = [], [], [], [], [], [], []
    nodes: list[Point] = []
    edges: set[tuple[int, int]] = set()
    stands: list[list[Pose]] = []   # per table: where the authors stood when they opened its menu

    def node_at(pose: Pose) -> int:
        for index, (x, y) in enumerate(nodes):
            if math.hypot(pose.x - x, pose.y - y) < NODE_SPACING:
                return index
        nodes.append((pose.x, pose.y))
        return len(nodes) - 1
    house_paths: list[list[Point]] = []
    yard_paths: list[tuple[float, list[Point]]] = []
    for run, plan in zip(runs, plans):
        doors = [step for step in plan.steps if step.kind == "interact"]
        if len(doors) < 4 or [d.expected for d in doors[:4]] != ["open", "close", "open", "close"]:
            continue
        poses: list[Pose | None] = []
        previous: Pose | None = None
        for feats in run.radar:
            pose = locator._locate(feats, previous) if feats is not None else None
            poses.append(pose)
            previous = pose or previous

        def at(moment: float) -> Pose | None:
            near = [(abs(t - moment), i) for i, t in enumerate(plan.key_times) if poses[i] is not None and abs(t - moment) <= 0.8]
            return poses[min(near)[1]] if near else None

        marks = [at(d.t0) for d in doors[:4]]
        if any(mark is None for mark in marks):
            continue
        descent = next((s for s in plan.steps if s.kind == "move" and s.key == "w" and s.t0 > doors[3].t0 and s.hold >= 1.5), None)
        bottom = at(descent.t1) if descent is not None else None
        if bottom is None:
            continue
        door_in.append(marks[0]); porch.append(marks[1]); gate_out.append(marks[2]); gate_in.append(marks[3])
        # the ground walked on behind the gate: the stairs and the room with the tables
        last: tuple[int, Pose] | None = None
        for pose, moment in zip(poses, plan.key_times):
            if pose is None or moment < doors[3].t0:
                continue
            index = node_at(pose)
            if last is not None and last[0] != index and last[1].distance_to(pose.x, pose.y) < 9.0:
                edges.add((min(last[0], index), max(last[0], index)))
            last = (index, pose)
        for moment in plan.harvest_times:
            stand = at(moment)
            if stand is None:
                continue
            for group in stands:
                if group[0].distance_to(stand.x, stand.y) < 3.0 and abs(wrap(group[0].heading - stand.heading)) < 60.0:
                    group.append(stand)
                    break
            else:
                stands.append([stand])
        ends.append(bottom)
        bearings.append(marks[3].bearing_to(bottom.x, bottom.y))
        house = [(p.x, p.y) for p, t in zip(poses, plan.key_times) if p is not None and t <= doors[0].t0]
        house_paths.append(_thin(house, 2.5) + [(marks[0].x, marks[0].y)])
        yard = [(p.x, p.y) for p, t in zip(poses, plan.key_times) if p is not None and doors[1].t0 <= t <= doors[2].t0]
        yard_paths.append((0.0, _thin(yard, 3.0)))
        long_walk = max((s for s in plan.steps if s.kind == "move" and s.key == "w"), key=lambda s: s.hold, default=None)
        if long_walk is not None and long_walk.hold >= 4.0:
            a, b = at(long_walk.t0 + 1.0), at(long_walk.t1 - 0.5)
            if a is not None and b is not None:
                speeds.append(math.hypot(b.x - a.x, b.y - a.y) / max(0.5, long_walk.t1 - long_walk.t0 - 1.5))
    if not door_in:
        return None
    route = RouteData(_median_pose(door_in), _median_pose(porch), _median_pose(gate_out), _median_pose(gate_in),
                      wrap(bearings[0] + float(np.median([wrap(b - bearings[0]) for b in bearings]))),
                      (float(np.median([p.x for p in ends])), float(np.median([p.y for p in ends]))),
                      float(np.median(speeds)) if speeds else 9.5)
    target = (route.door_in.x, route.door_in.y)
    for path in house_paths:
        while path and math.hypot(path[-1][0] - target[0], path[-1][1] - target[1]) < 3.0:
            path.pop()  # the last steps in front of the door: the common spot replaces them
        path.append(target)
    route.house_paths = house_paths
    # the yard is walked the way the run that ended closest to the common gate spot walked it
    best = min(yard_paths, key=lambda item: math.hypot(item[1][-1][0] - route.gate_out.x, item[1][-1][1] - route.gate_out.y))[1]
    route.yard_path = [p for p in best if math.hypot(p[0] - route.gate_out.x, p[1] - route.gate_out.y) > 4.0] \
        + [(route.gate_out.x, route.gate_out.y)]
    route.tables = [(p.x, p.y, p.heading) for p in (_median_pose(group) for group in stands)]
    route.nodes = nodes
    route.edges = sorted(edges)
    return route


NODE_SPACING = 1.6   # mini-map pixels: recorded places nearer than this are one point of the net


def net_path(nodes: list[Point], edges: list[tuple[int, int]], start: int, goal: int) -> list[int] | None:
    """The shortest way from one point of the net to another along its joins."""
    import heapq

    near: dict[int, list[tuple[int, float]]] = {}
    for a, b in edges:
        length = math.hypot(nodes[a][0] - nodes[b][0], nodes[a][1] - nodes[b][1])
        near.setdefault(a, []).append((b, length))
        near.setdefault(b, []).append((a, length))
    best = {start: 0.0}
    came: dict[int, int] = {}
    queue = [(0.0, start)]
    while queue:
        cost, here = heapq.heappop(queue)
        if here == goal:
            way = [goal]
            while way[-1] != start:
                way.append(came[way[-1]])
            return way[::-1]
        if cost > best.get(here, math.inf):
            continue
        for there, length in near.get(here, []):
            if cost + length < best.get(there, math.inf):
                best[there] = cost + length
                came[there] = here
                heapq.heappush(queue, (cost + length, there))
    return None


def _mosaic_from(runs: list, plans: list, sift: MapLocator):
    """The picture of the whole area, put together from the key frames of the runs at the poses the key points give."""
    import cv2

    from .minimap_mosaic import MosaicLocator

    frames = []
    for run in runs:
        previous = None
        for item, feats in zip(run.keyframes, run.radar):
            pose = sift._locate(feats, previous) if feats is not None else None
            previous = pose or previous
            image = cv2.imread(str(run.folder / item["file"])) if pose is not None else None
            if image is not None:
                frames.append((cv2.cvtColor(image, cv2.COLOR_BGR2RGB), pose))
    return MosaicLocator.build(frames) if frames else None


def table_groups(spots: list[tuple[float, float, float]], radius: float = 3.0, ahead: float = 3.0) -> list[int]:
    """Which table each spot belongs to (numbered in the order of their first spot). A table may be marked from both
    sides of an aisle: the spots then face each other and the tables they face lie in the same place."""
    centres = [(x + ahead * math.sin(math.radians(h)), y - ahead * math.cos(math.radians(h))) for x, y, h in spots]
    table = list(range(len(spots)))
    for i in range(len(spots)):
        for j in range(i):
            if math.hypot(centres[i][0] - centres[j][0], centres[i][1] - centres[j][1]) <= radius:
                old_id, new_id = table[i], table[j]
                table = [new_id if t == old_id else t for t in table]
    order = sorted(set(table), key=table.index)
    return [order.index(t) for t in table]


def load_or_build(runs: list, plans: list, cache: Path | None):
    """The live locator and the route of these runs; computed once (under a minute) and kept next to the runs.

    The route is read from the recordings with key points (they work well between recorded frames). The live screen is
    located on the mosaic: key points of the recordings did not survive on the live picture in the first trial."""
    from .minimap_mosaic import MosaicLocator

    if not runs or not any(any(f is not None for f in getattr(run, "radar", [])) for run in runs):
        return None
    signature = [[run.run_id, len(run.keyframes)] for run in runs] + [list(PIVOT), CACHE_VERSION]
    picture = cache.with_name("minimap_mosaic.png") if cache is not None else None
    if cache is not None and cache.exists() and picture.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("signature") == signature:
                live = MosaicLocator.load(picture, tuple(data["mosaic_offset"]))
                live.sift = MapLocator.restore(runs, data["references"])
                return live, RouteData.from_json(data["route"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            logger.info("map route: the cache is unreadable, building again")
    locator = MapLocator.from_runs(runs, plans)
    if not locator.usable:
        return None
    route = build_route(runs, plans, locator)
    if route is None:
        return None
    live = _mosaic_from(runs, plans, locator)
    if live is None:
        return None
    live.sift = locator
    if cache is not None:
        try:
            live.save(picture)
            cache.write_text(json.dumps({"signature": signature, "references": locator.dump(), "route": route.to_json(),
                                         "mosaic_offset": list(live.offset)}), encoding="utf-8")
        except OSError:
            logger.exception("map route: could not write the cache")
    return live, route


class MapPilot:
    """Mixed into the visual route runner: walks the way by the poses of the mini-map."""

    MAP_ARRIVE = 2.2          # mini-map pixels: close enough to a point of the way
    MAP_BLOCK_LIMIT = 6       # times something may be in the way before giving up on a leg

    _locator: MapLocator | None = None
    _route: RouteData | None = None
    _map_seen: tuple[Pose, float] | None = None
    _turn_scale = 1.0

    # ---- reading the place ------------------------------------------------------------------

    def _map_ready(self, runs: list, plans: list) -> bool:
        if self._locator is None or self._route is None:
            folder = getattr(runs[0], "folder", None) if runs else None
            cache = folder.parent / "maproute.json" if folder is not None else None
            built = load_or_build(runs, plans, cache)
            if built is None:
                return False
            self._locator, self._route = built
            route = self._route
            logger.info("map route: house door (%.0f,%.0f) h %.0f, porch (%.0f,%.0f), gate (%.0f,%.0f) h %.0f, inside "
                        "(%.0f,%.0f), stairs bearing %.0f to (%.0f,%.0f), %.1f px/s", route.door_in.x, route.door_in.y,
                        route.door_in.heading, route.porch.x, route.porch.y, route.gate_out.x, route.gate_out.y,
                        route.gate_out.heading, route.gate_in.x, route.gate_in.y, route.stairs_bearing, *route.stairs_end,
                        route.px_per_second)
        return True

    def _map_pose(self, tries: int = 5) -> Pose | None:
        """The pose right now. A reading far from where we can have got since the last one must repeat itself to be
        believed: one wrong fit of the mini-map must not send the bot across the yard."""
        doubt: Pose | None = None
        for _ in range(tries):
            pose = self._locator.locate(self.screen.capture(), self._map_seen[0] if self._map_seen else None)
            now = time.monotonic()
            if pose is not None:
                if self._map_seen is None:
                    self._map_seen = (pose, now)
                    return pose
                last, when = self._map_seen
                reach = 6.0 + 16.0 * (now - when)
                if last.distance_to(pose.x, pose.y) <= reach or (doubt is not None and doubt.distance_to(pose.x, pose.y) < 4.0):
                    self._map_seen = (pose, now)
                    return pose
                doubt = pose
            self.driver.wait(0.1)
        return None

    def _map_need(self) -> Pose:
        pose = self._map_pose()
        if pose is None:
            from .visual_route import VisualRouteError
            raise VisualRouteError("Не читается мини-карта: не могу понять, где стою.")
        return pose

    # ---- moving -----------------------------------------------------------------------------

    def _map_face(self, heading: float, tolerance: float = 3.0) -> Pose:
        """Turn to `heading`, measuring every turn on the mini-map (the mouse-to-degrees rate corrects itself)."""
        pose = self._map_need()
        for _ in range(8):
            error = wrap(heading - pose.heading)
            if abs(error) <= tolerance:
                break
            before = pose.heading
            self._look(error * self._turn_scale, 0.0)
            self.driver.wait(0.22)
            pose = self._map_need()
            turned = wrap(pose.heading - before)
            if abs(error) >= 12.0 and turned * error > 0 and abs(turned) > 0.25 * abs(error):
                wanted = error * self.TURN_GAIN
                self._turn_scale = max(0.4, min(2.5, self._turn_scale * (wanted / turned) ** 0.7))
        return pose

    def _map_walk(self, x: float, y: float, name: str, arrive: float | None = None, timeout: float = 30.0,
                  blocks: int | None = None) -> Pose:
        """Walk to the point (x, y): W stays down while the place is read several times a second and the heading is
        eased towards the point. Not getting nearer with W down means something is in the way: step back and aside."""
        from .visual_route import VisualRouteError

        arrive = self.MAP_ARRIVE if arrive is None else arrive
        speed = max(4.0, self._route.px_per_second)
        started = time.monotonic()
        blocked = 0
        pose = self._map_need()
        mark, pressed = pose, 0.0   # where we were, and how long W has been down since then
        while True:
            distance = pose.distance_to(x, y)
            if distance <= arrive:
                return pose
            if time.monotonic() - started > timeout:
                raise VisualRouteError(f"Не дошёл до точки «{name}» за {timeout:.0f} с (осталось {distance / speed * 2.6:.1f} м).")
            error = pose.turn_to(x, y)
            if abs(error) > (12.0 if distance < 6.0 else 22.0):
                pose = self._map_face(pose.bearing_to(x, y), 4.0)
                continue
            burst = max(0.12, min(3.0, (distance - 0.5 * arrive) / speed))
            begun = time.monotonic()
            seen_at = begun
            stuck = False
            blind_since: float | None = None
            try:
                self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
                while time.monotonic() - begun < burst:
                    self.driver.wait(0.02)
                    live = self._locator.locate(self.screen.capture(), pose)
                    now = time.monotonic()
                    if live is None or pose.distance_to(live.x, live.y) > 6.0 + 16.0 * (now - seen_at + 0.3):
                        blind_since = blind_since or now
                        if now - blind_since > 0.9:
                            break
                        continue
                    blind_since = None
                    pressed += now - seen_at
                    pose, seen_at = live, now
                    self._map_seen = (pose, now)
                    distance = pose.distance_to(x, y)
                    if distance <= arrive:
                        break
                    burst = min(burst, now - begun + max(0.05, (distance - 0.5 * arrive) / speed))
                    if pressed >= 0.9:
                        # W has been down for most of a second (over one or several short pushes): a quarter of the
                        # ground it should have covered is the least that counts as walking
                        if mark.distance_to(pose.x, pose.y) < 0.25 * speed * pressed:
                            stuck = True
                            break
                        mark, pressed = pose, 0.0
                    error = pose.turn_to(x, y)
                    if abs(error) > 35.0 and distance > arrive + 1.0:
                        break  # walked past or pushed aside: stop and turn properly
                    if abs(error) > 3.0 and distance > 4.0:
                        self._look(max(-7.0, min(7.0, error * 0.6)) * self._turn_scale, 0.0)
            finally:
                self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))
            self.driver.wait(0.12)
            pose = self._map_need()
            if stuck:
                blocked += 1
                logger.info("map: something in the way to «%s» at (%.0f,%.0f), try %d", name, pose.x, pose.y, blocked)
                if blocked > (self.MAP_BLOCK_LIMIT if blocks is None else blocks):
                    raise VisualRouteError(f"Упёрся по пути к точке «{name}»: место не меняется.")
                self._show(status="Упёрся · обхожу", mode="search", stage=name, frame=self._map_frame)
                self._hold("s", 0.25)
                # two tries round one side, then two round the other, a little wider each time
                self._hold("d" if ((blocked - 1) // 2) % 2 == 0 else "a", 0.3 + 0.1 * blocked)
                pose = self._map_need()
                mark, pressed = pose, 0.0

    def _map_follow(self, points: list[Point], name: str) -> Pose:
        """Along a way of points, starting at the one nearest to where we stand."""
        pose = self._map_need()
        start = min(range(len(points)), key=lambda i: pose.distance_to(*points[i]))
        if start + 1 < len(points):
            ahead = points[start + 1]
            if pose.distance_to(*ahead) < math.hypot(ahead[0] - points[start][0], ahead[1] - points[start][1]):
                start += 1  # already past that point
        for index in range(start, len(points)):
            if index == len(points) - 1:
                arrive = None
            else:
                # a corner of the way (the porch steps, the end of a wall) is walked exactly, a straight stretch loosely
                before = points[index - 1] if index else (pose.x, pose.y)
                bend = wrap(math.degrees(math.atan2(points[index + 1][0] - points[index][0], -(points[index + 1][1] - points[index][1])))
                            - math.degrees(math.atan2(points[index][0] - before[0], -(points[index][1] - before[1]))))
                arrive = 2.0 if abs(bend) > 25.0 else 4.0
            pose = self._map_walk(points[index][0], points[index][1], name, arrive)
        return pose

    def _map_go(self, x: float, y: float, name: str, arrive: float = 1.3) -> Pose:
        """To a place of the room below, along the ground the recordings walked on: from the point of the net nearest
        to us, join by join, to the point nearest to the goal, then the last step to the goal itself. The tables, the
        pillars and the rails are never crossed, because no recording crossed them."""
        from .visual_route import VisualRouteError

        route = self._route
        pose = self._map_need()
        if pose.distance_to(x, y) <= max(2.5, arrive) or not route.nodes:
            return self._map_walk(x, y, name, arrive, timeout=8.0, blocks=2)
        start = min(range(len(route.nodes)), key=lambda i: pose.distance_to(*route.nodes[i]))
        if pose.distance_to(*route.nodes[start]) > 12.0:
            raise VisualRouteError(f"Стою далеко от известных проходов ({pose.x:.0f},{pose.y:.0f}): не знаю безопасного пути.")
        goal = min(range(len(route.nodes)), key=lambda i: math.hypot(route.nodes[i][0] - x, route.nodes[i][1] - y))
        way = net_path(route.nodes, route.edges, start, goal)
        if way is None:
            raise VisualRouteError(f"Нет записанного прохода к месту «{name}».")
        points = [route.nodes[i] for i in way]
        # the first point may lie behind us on the way: start with the next one then
        if len(points) > 1 and pose.distance_to(*points[1]) < math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]):
            points = points[1:]
        # straight stretches are walked in one go: a point that lies on the line between its neighbours is left out
        kept: list[Point] = []
        for index, point in enumerate(points):
            if 0 < index < len(points) - 1 and kept:
                a, b = kept[-1], points[index + 1]
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                off = abs((b[0] - a[0]) * (a[1] - point[1]) - (a[0] - point[0]) * (b[1] - a[1])) / max(length, 1e-6)
                if off < 0.7 and length < 14.0:
                    continue
            kept.append(point)
        for point in kept:
            if math.hypot(point[0] - x, point[1] - y) > 1.5:
                self._map_walk(point[0], point[1], name, 1.6, timeout=15.0, blocks=3)
        try:
            return self._map_walk(x, y, name, arrive, timeout=6.0, blocks=1)
        except VisualRouteError:
            pose = self._map_need()
            if pose.distance_to(x, y) <= 3.0:
                return pose  # a table edge keeps us a step short of the exact spot: near enough to look and to harvest
            raise

    # ---- doors ------------------------------------------------------------------------------

    def _map_prompt(self) -> tuple[str | None, bool]:
        """(action, titled): the door action on screen and whether the title «ДВЕРЬ» was read with it."""
        frame = self.screen.capture()
        detail = getattr(self.prompt_reader, "read_detail", None)
        if detail is not None:
            return detail(frame)
        return self.prompt_reader.read(frame), True

    def _map_door(self, expected: str, name: str, heading: float, creep: int = 0, wide: bool = True) -> bool:
        """Standing at a door, facing it by the mini-map: find its prompt with small looks to the sides and press E if
        the door is not yet in the wanted state. True: the door is now as wanted. E is pressed only on a door prompt."""
        offsets = (0.0, 8.0, -8.0, 16.0, -16.0, 26.0, -26.0) if wide else (0.0, 8.0, -8.0, 16.0, -16.0)
        tilted = 0.0
        tilts = (0.0, 7.0, -7.0)  # the prompt needs the crosshair ON the door: also try a little lower and higher
        for attempt in range(max(creep + 1, len(tilts))):
            tilt = tilts[attempt % len(tilts)]
            if tilt != tilted:
                self._look(0.0, (tilt - tilted) / self.TURN_GAIN)
                tilted = tilt
            for offset in offsets:
                self._map_face(heading + offset, 2.5)
                for _read in range(2):
                    action, titled = self._map_prompt()
                    if action is not None:
                        break
                    self.driver.wait(0.12)
                # off to the side only a prompt that names itself «ДВЕРЬ» counts: next to the house door is a window
                if action is None or (not titled and abs(offset) > 10.0):
                    continue
                if action == expected:
                    self.driver.play_key("e", 0.12)
                    self.driver.wait(0.3)
                    logger.info("door done: %s, pressed E for «%s» (look %+.0f deg)", name, expected, offset)
                else:
                    logger.info("door done: %s is already %s, E not needed", name, "open" if expected == "open" else "closed")
                if tilted:
                    self._look(0.0, -tilted / self.TURN_GAIN)
                return True
            if attempt < creep:
                logger.info("door: %s shows no prompt yet, a short step closer (%d)", name, attempt + 1)
                self._map_face(heading, 3.0)
                self._hold("w", 0.16)
                self.driver.wait(0.15)
        if tilted:
            self._look(0.0, -tilted / self.TURN_GAIN)
        return False

    def _map_pass(self, target: Pose, heading: float, name: str) -> None:
        """Through a door that was just opened, to the point behind it. If the way is shut (the door did not open, or
        E closed an open one), look at the door again and open it."""
        from .visual_route import VisualRouteError

        self.driver.wait(0.6)  # the door swings open
        for attempt in range(2):
            try:
                self._map_walk(target.x, target.y, name, 2.5, timeout=7.0, blocks=1)
                return
            except VisualRouteError:
                if attempt:
                    raise
                logger.info("door: %s did not let us through, checking it again", name)
                self._hold("s", 0.3)
                self._map_door("open", name, heading, creep=2)
                self.driver.wait(0.6)

    # ---- the stages -------------------------------------------------------------------------

    def _map_stage(self, pose: Pose, frame: np.ndarray) -> str:
        """Which part of the way a place belongs to. By the place alone: the colours of the picture change with the
        time of day and with what the camera happens to look at (a window full of sand is not the yard)."""
        route = self._route
        gate = math.radians(route.gate_out.bearing_to(route.gate_in.x, route.gate_in.y))
        along = (pose.x - route.gate_out.x) * math.sin(gate) - (pose.y - route.gate_out.y) * math.cos(gate)
        aside = (pose.x - route.gate_out.x) * math.cos(gate) + (pose.y - route.gate_out.y) * math.sin(gate)
        depth = route.gate_out.distance_to(route.gate_in.x, route.gate_in.y)
        length = math.hypot(route.stairs_end[0] - route.gate_in.x, route.stairs_end[1] - route.gate_in.y)
        if 0.5 * depth <= along <= depth + length + 8.0 and abs(aside) <= 8.0:
            return "stairs"
        door = math.radians(route.door_in.heading)
        through = (pose.x - route.door_in.x) * math.sin(door) - (pose.y - route.door_in.y) * math.cos(door)
        porch = (route.porch.x - route.door_in.x) * math.sin(door) - (route.porch.y - route.door_in.y) * math.cos(door)
        if through < 0.5 * porch and pose.distance_to(route.door_in.x, route.door_in.y) < 32.0:
            return "house"
        return "yard"

    def run_by_map(self, runs: list, plans: list) -> bool:
        """The whole way by the mini-map. False: the mini-map cannot be used here (no data, not readable), nothing was
        done and the caller may fall back to the taught pictures."""
        from .visual_route import VisualRouteError

        if not self._map_ready(runs, plans):
            logger.info("map route: no mini-map data in the taught runs")
            return False
        frame = self.screen.capture()
        if is_platform(frame):
            return False  # the purple light of the grow room: we are below already, the caller knows what to do there
        self._map_frame = [frame.shape[1], frame.shape[0]]
        self._map_seen = None
        pose = self._map_pose()
        if pose is None:
            logger.info("map route: the mini-map is not readable here")
            return False
        stage = self._map_stage(pose, frame)
        logger.info("map route: start at (%.1f,%.1f) heading %.0f (%d points) -> stage %s", pose.x, pose.y, pose.heading,
                    pose.inliers, stage)
        try:
            if stage == "house":
                self._map_house()
                self._map_porch()
                stage = "yard"
            if stage == "yard":
                self._map_yard()
                self._map_gate()
                stage = "stairs"
            self._map_stairs()
        except VisualRouteError as exc:
            where = self._map_seen[0] if self._map_seen else None
            logger.warning("map route failed at stage «%s»%s: %s", self._stage,
                           f" at ({where.x:.1f},{where.y:.1f}) heading {where.heading:.0f}" if where else "", exc)
            self._save_trace(f"сбой_{self._stage}")
            raise
        return True

    def _map_say(self, status: str, stage: str, mode: str = "ok") -> None:
        self._show(status=status, mode=mode, stage=stage, frame=self._map_frame)

    def _map_house(self) -> None:
        from .visual_route import VisualRouteError

        route = self._route
        self._mark("дом: иду к двери")
        self._map_say("Иду к двери дома", "дом")
        pose = self._map_need()
        if pose.distance_to(route.door_in.x, route.door_in.y) > 3.0 and route.house_paths:
            path = min(route.house_paths, key=lambda points: min(pose.distance_to(*p) for p in points))
            self._map_follow(path, "дверь дома")
        self._mark("дом: дверь")
        self._map_say("Открываю дверь дома", "дом")
        if not self._map_door("open", "дверь дома", route.door_in.heading, creep=4):
            # no prompt at all: the door may stand wide open with its leaf out of sight. The way through tells.
            logger.info("door: дверь дома shows no prompt, trying the doorway itself")
        try:
            self._map_pass(route.porch, route.door_in.heading, "крыльцо")
        except VisualRouteError as exc:
            raise VisualRouteError(f"Не вышел из дома через дверь: {exc}") from exc
        logger.info("map: out of the house, on the porch")

    def _map_porch(self) -> None:
        """Close the house door behind us. Worth a few seconds, never worth failing the whole way."""
        from .visual_route import VisualRouteError

        route = self._route
        self._mark("крыльцо: закрываю дверь дома")
        self._map_say("Закрываю дверь дома", "крыльцо")
        try:
            self._map_walk(route.porch.x, route.porch.y, "крыльцо", 1.8, timeout=5.0)
            closed = self._map_door("close", "дверь дома (с крыльца)", route.porch.heading, wide=True)
        except VisualRouteError as exc:
            logger.info("porch: %s", exc)
            closed = False
        if not closed:
            logger.warning("door: the house door could not be closed from the porch (no prompt); going on")

    def _map_yard(self) -> None:
        self._mark("двор: иду к контейнеру")
        self._map_say("Иду к красному контейнеру", "двор")
        self._map_follow(self._route.yard_path, "ворота контейнера")

    def _map_gate(self) -> None:
        from .visual_route import VisualRouteError

        route = self._route
        self._mark("ворота контейнера: открываю")
        self._map_say("Открываю ворота контейнера", "ворота")
        if not self._map_door("open", "ворота контейнера", route.gate_out.heading, creep=5):
            raise VisualRouteError("Стою у ворот контейнера, но игра не показывает подсказку «ДВЕРЬ».")
        self._mark("ворота контейнера: вхожу")
        self._map_pass(route.gate_in, route.gate_out.heading, "контейнер")
        self._mark("ворота контейнера: закрываю")
        self._map_say("Закрываю ворота за собой", "ворота")
        closed = self._map_door("close", "ворота контейнера (изнутри)", route.gate_in.heading)
        if not closed:
            self._hold("w", 0.2)  # we face the gate: a short step nearer to it
            closed = self._map_door("close", "ворота контейнера (изнутри)", route.gate_in.heading)
        if not closed:
            logger.warning("door: the container gate could not be closed from inside (no prompt); going on")

    def _map_stairs(self) -> None:
        """Down the stairs and a quarter turn to the left: the room with the tables is to the left of their end."""
        from .visual_route import VisualRouteError

        route = self._route
        self._mark("лестница")
        self._map_say("Спускаюсь по лестнице", "лестница")
        length = self.STAIRS_METRES / max(0.5, self.metres_per_second) * route.px_per_second
        bearing = math.radians(route.stairs_bearing)
        end = (route.gate_in.x + length * math.sin(bearing), route.gate_in.y - length * math.cos(bearing))
        pose = self._map_need()
        gone = (pose.x - route.gate_in.x) * math.sin(bearing) - (pose.y - route.gate_in.y) * math.cos(bearing)
        if gone < length - self.MAP_ARRIVE:
            if gone < 3.0:
                try:  # onto the middle line of the stairs first: the stairwell is not much wider than the character
                    self._map_walk(route.gate_in.x, route.gate_in.y, "начало лестницы", 1.2, timeout=4.0, blocks=1)
                except VisualRouteError as exc:
                    logger.info("stairs: %s", exc)
                pose = self._map_need()
            self._map_face(pose.bearing_to(*end), 2.5)
            self._map_walk(end[0], end[1], "конец лестницы", timeout=self.DESCENT_MAX)
        self._map_say("Лестница кончилась: поворачиваю налево", "лестница")
        pose = self._map_face(route.stairs_bearing - 90.0, 2.5)
        logger.info("map: end of the stairs at (%.1f,%.1f), heading %.0f (stairs %.0f, left turn done)%s", pose.x, pose.y,
                    pose.heading, route.stairs_bearing, "" if is_platform(self.screen.capture()) else "; no purple light yet")
        self.at_platform_entry = True
        self.stairs_taken = True
        self._came_down = False
        self._mark("конец лестницы: дальше по плану")
