"""The plan of the grow room (the platform with the tables), in metres, as drawn by the user.

Coordinates: x to the east, y to the north, the origin at the south-west corner of the room (14 m x 12 m).
The stairs come down along the south wall from the west and end at x = 5.2 m; from there the room opens to the north
(from the end of the stairs the way is "to the left"). Six tables stand in two rows of three, a pillar between the
rows under each column of tables. The vertical aisles (x = 5.2 and x = 8.8 m) lead past two tables each; the
horizontal aisle between the rows has a pillar in it.

The drawing is not quite to scale (the tables are drawn higher than wide); the positions are taken from it as it
shows them, with the room's own 14 x 12 m as the scale, and the heights of the tables as labelled (2 m).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

ROOM = (14.0, 12.0)
TABLE_SIZE = (1.4, 2.0)                      # across (x), along (y)
COLUMNS = (3.49, 7.02, 10.55)                # x of the three columns of tables
ROWS = (4.16, 7.99)                          # y of the south and the north row
PILLAR_SIZE = 0.6
PILLARS = tuple((x, 6.04) for x in COLUMNS)
AISLES = (5.2, 8.8)                          # x of the two vertical aisles between the columns
STAIRS_END = (5.2, 0.85)                     # where the stairs let out into the room
STAIRS_START = (-1.0, 0.85)                  # inside the container, at the closed red gates
HEADING_NORTH, HEADING_EAST, HEADING_SOUTH, HEADING_WEST = 0.0, 90.0, 180.0, 270.0

# other things that stand in the way: (x0, y0, x1, y1)
OBSTACLES = (
    (0.0, 2.8, 0.6, 9.8),        # the curtain along the west wall
    (0.0, 10.4, 14.0, 12.0),     # furniture along the north wall
    (13.5, 1.0, 14.0, 11.0),     # the pipe along the east wall
    (8.9, 0.2, 13.7, 1.3),       # the roll and the bucket by the south wall
    (-0.3, 1.35, 4.8, 1.5),      # the railing of the stairs (it ends before the exit into the room)
)

CLEARANCE = 0.4                  # how far the body of the player stays from everything


def table_rect(x: float, y: float) -> tuple[float, float, float, float]:
    return x - TABLE_SIZE[0] / 2, y - TABLE_SIZE[1] / 2, x + TABLE_SIZE[0] / 2, y + TABLE_SIZE[1] / 2


@dataclass(frozen=True)
class Side:
    """One table seen from a standing place: which way to face, which table (slot 0..5) and where it is."""
    heading: float
    slot: int
    table: tuple[float, float]


@dataclass(frozen=True)
class Spot:
    name: str
    x: float
    y: float
    sides: tuple[Side, ...]


def _spots() -> tuple[Spot, ...]:
    c1, c2, c3 = COLUMNS
    south, north = ROWS
    a1, a2 = AISLES
    return (
        Spot("A", a1, south, (Side(HEADING_WEST, 0, (c1, south)), Side(HEADING_EAST, 1, (c2, south)))),
        Spot("B", a1, north, (Side(HEADING_WEST, 2, (c1, north)), Side(HEADING_EAST, 3, (c2, north)))),
        Spot("C", a2, north, (Side(HEADING_EAST, 4, (c3, north)),)),
        Spot("D", a2, south, (Side(HEADING_EAST, 5, (c3, south)),)),
    )


SPOTS = _spots()
TABLE_SLOTS = {side.slot: side.table for spot in SPOTS for side in spot.sides}


class FloorPlan:
    """Free space of the room on a grid, and ways through it that keep clear of the tables and the pillars."""

    CELL = 0.1

    def __init__(self) -> None:
        self.x0, self.y0 = -0.5, -0.5
        self.width = int((ROOM[0] + 1.0) / self.CELL)
        self.height = int((ROOM[1] + 1.0) / self.CELL)
        self.blocked = [[False] * self.width for _ in range(self.height)]
        for x in range(self.width):
            for y in range(self.height):
                px, py = self.x0 + (x + 0.5) * self.CELL, self.y0 + (y + 0.5) * self.CELL
                self.blocked[y][x] = not self._free(px, py)

    # -- geometry -------------------------------------------------------------------------

    def rectangles(self) -> list[tuple[float, float, float, float]]:
        out = [table_rect(x, y) for y in ROWS for x in COLUMNS]
        out += [(x - PILLAR_SIZE / 2, y - PILLAR_SIZE / 2, x + PILLAR_SIZE / 2, y + PILLAR_SIZE / 2) for x, y in PILLARS]
        out += list(OBSTACLES)
        return out

    def _free(self, x: float, y: float) -> bool:
        # inside the room, or in the mouth of the stairs
        in_room = 0.0 <= x <= ROOM[0] and 0.0 <= y <= ROOM[1]
        in_exit = 4.8 <= x <= 5.7 and -0.3 <= y <= 1.6
        if not (in_room or in_exit):
            return False
        for left, bottom, right, top in self.rectangles():
            if left - CLEARANCE <= x <= right + CLEARANCE and bottom - CLEARANCE <= y <= top + CLEARANCE:
                if in_exit and 4.8 <= x <= 5.7:
                    continue  # the way out of the stairs stays open
                return False
        # keep off the walls too (except in the mouth of the stairs)
        return in_exit or min(x, ROOM[0] - x, y, ROOM[1] - y) >= CLEARANCE

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return (min(self.width - 1, max(0, int((x - self.x0) / self.CELL))),
                min(self.height - 1, max(0, int((y - self.y0) / self.CELL))))

    def _centre(self, cell: tuple[int, int]) -> tuple[float, float]:
        return self.x0 + (cell[0] + 0.5) * self.CELL, self.y0 + (cell[1] + 0.5) * self.CELL

    def _nearest_free(self, cell: tuple[int, int]) -> tuple[int, int] | None:
        if not self.blocked[cell[1]][cell[0]]:
            return cell
        for radius in range(1, 30):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    x, y = cell[0] + dx, cell[1] + dy
                    if 0 <= x < self.width and 0 <= y < self.height and not self.blocked[y][x]:
                        return (x, y)
        return None

    def free_at(self, x: float, y: float) -> bool:
        cx, cy = self._cell(x, y)
        return not self.blocked[cy][cx]

    # -- ways -----------------------------------------------------------------------------

    def route(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
        """Corner points of a way from `start` to `goal` (metres) that keeps clear of everything; [] if there is none."""
        a = self._nearest_free(self._cell(*start))
        b = self._nearest_free(self._cell(*goal))
        if a is None or b is None:
            return []
        best = {a: 0.0}
        came: dict[tuple[int, int], tuple[int, int]] = {}
        queue = [(0.0, a)]
        steps = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414))
        while queue:
            cost, cell = heapq.heappop(queue)
            if cell == b:
                break
            if cost > best.get(cell, math.inf):
                continue
            for dx, dy, length in steps:
                x, y = cell[0] + dx, cell[1] + dy
                if not (0 <= x < self.width and 0 <= y < self.height) or self.blocked[y][x]:
                    continue
                if dx and dy and (self.blocked[cell[1]][x] or self.blocked[y][cell[0]]):
                    continue  # no cutting corners
                total = cost + length
                if total < best.get((x, y), math.inf):
                    best[(x, y)] = total
                    came[(x, y)] = cell
                    heapq.heappush(queue, (total, (x, y)))
        if b not in best:
            return []
        cells = [b]
        while cells[-1] != a:
            cells.append(came[cells[-1]])
        cells.reverse()
        return self._straighten([start] + [self._centre(c) for c in cells] + [goal])

    def _clear_line(self, p: tuple[float, float], q: tuple[float, float]) -> bool:
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        steps = max(2, int(length / (self.CELL / 2)))
        return all(self.free_at(p[0] + (q[0] - p[0]) * i / steps, p[1] + (q[1] - p[1]) * i / steps) for i in range(steps + 1))

    def _straighten(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Fewest corners: from each point go as far along the way as a straight line stays clear."""
        out = [points[0]]
        i = 0
        while i < len(points) - 1:
            j = len(points) - 1
            while j > i + 1 and not self._clear_line(points[i], points[j]):
                j -= 1
            out.append(points[j])
            i = j
        return out

    @staticmethod
    def length(path: list[tuple[float, float]]) -> float:
        return sum(math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1]) for i in range(len(path) - 1))

    def tour(self, start: tuple[float, float]) -> list[Spot]:
        """The standing places in the order that is shortest to walk, nearest first, from where we are."""
        left = list(SPOTS)
        here = start
        order: list[Spot] = []
        while left:
            nearest = min(left, key=lambda s: self.length(self.route(here, (s.x, s.y))) or math.inf)
            order.append(nearest)
            left.remove(nearest)
            here = (nearest.x, nearest.y)
        return order

    # -- for the radar --------------------------------------------------------------------

    def view(self, bot: tuple[float, float, float] | None = None, goal: tuple[float, float] | None = None) -> dict:
        return {
            "plan": {
                "room": list(ROOM),
                "tables": [list(table_rect(x, y)) for y in ROWS for x in COLUMNS],
                "pillars": [[x, y, PILLAR_SIZE] for x, y in PILLARS],
                "stairs": [-0.3, 0.0, 5.3, 1.3],
            },
            "bot": [round(bot[0], 2), round(bot[1], 2), round(bot[2], 1)] if bot else None,
            "goal": [round(goal[0], 2), round(goal[1], 2)] if goal else None,
            "title": "ПЛАНТАЦИЯ · ПЛАН",
        }
