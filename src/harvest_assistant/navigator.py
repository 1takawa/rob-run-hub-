"""Walking to a place on the learned map.

The map (routemap.py) knows the ways that were really walked: round the pillars, through the doors, down the stairs.
The navigator asks where we are (a pose from the pictures), takes the way the map has to the goal, aims the camera
at a point some way ahead on it, walks, and asks again. Because the ways come from the author's own walks, a
detour around a pillar is part of the way and nothing has to be discovered.
"""

from __future__ import annotations

import logging
import math
from typing import Callable

logger = logging.getLogger(__name__)

Pose = tuple[float, float, float]  # x, y (map units), camera heading (degrees clockwise)


def wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


class MapNavigator:
    def __init__(
        self,
        route_map,
        get_pose: Callable[[], Pose | None],
        look: Callable[[float], None],
        walk: Callable[[float], None],
        *,
        reach: float = 0.9,
        lookahead: float = 1.6,
        longest_walk: float = 2.0,
        max_steps: int = 80,
    ) -> None:
        self.map = route_map
        self.get_pose, self.look, self.walk = get_pose, look, walk
        self.reach, self.lookahead, self.longest_walk, self.max_steps = reach, lookahead, longest_walk, max_steps
        self.trace: list[Pose] = []  # where the bot was at every look, for the radar picture and for tests

    def goto(self, goal: tuple[float, float], on_step: Callable[[Pose, list], None] | None = None) -> str:
        """"arrived" | "lost" (no pose for several looks) | "no_way" (the map has no way there) | "stuck" | "timeout"."""
        blind = 0
        best = math.inf
        stalled = 0
        for _ in range(self.max_steps):
            pose = self.get_pose()
            if pose is None:
                blind += 1
                if blind >= 5:
                    return "lost"
                continue
            blind = 0
            self.trace.append(pose)
            x, y, heading = pose
            distance = math.hypot(goal[0] - x, goal[1] - y)
            if distance <= self.reach:
                return "arrived"
            path = self.map.route((x, y), goal)
            if on_step is not None:
                on_step(pose, path)
            if not path:
                return "no_way"
            if distance < best - 0.25:
                best, stalled = distance, 0
            else:
                stalled += 1
                if stalled >= 8:
                    return "stuck"
            ahead = next((p for p in path if math.hypot(p[0] - x, p[1] - y) >= self.lookahead), path[-1])
            bearing = math.degrees(math.atan2(ahead[0] - x, ahead[1] - y))
            error = wrap(bearing - heading)
            if abs(error) > 4.0:
                self.look(max(-90.0, min(90.0, error)))
            if abs(error) <= 50.0:  # walk only when roughly facing the way; otherwise turn first and look again
                self.walk(max(0.3, min(self.longest_walk, math.hypot(ahead[0] - x, ahead[1] - y))))
        return "timeout"
