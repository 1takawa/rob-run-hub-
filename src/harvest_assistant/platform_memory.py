"""What the bot has learned about the platform by walking it: pictures that go with a place.

Every walk over the plan (see vision_cycle.py) can leave data behind: what the bot saw and where it stood. The place
is only trusted when it is certain, which on the plan means: the bot stands at a table with its menu on the screen
(then the standing place and the way it faces are known exactly), and the pictures taken on the way to that spot are
given places by spreading them along the straight stretch that ended there.

With such pictures the bot can find itself from a picture anywhere in the room (the same way it finds itself on the
taught way before the stairs): start from the middle of the room, or recover after it was pushed off its way. The more
walks, the more pictures, the closer they lie to every place the bot can really stand on.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .taught import Extractor, Features, Localizer, jpeg_frame, pixels_to_degrees

logger = logging.getLogger(__name__)

MIN_VIEWS = 20          # below this the memory is too thin to be believed
MAX_RUNS = 40           # the oldest walks are dropped first
MIN_INLIERS = 22        # a place is believed only with this many matching points


@dataclass
class View:
    x: float
    y: float
    heading: float
    features: Features


class _Walk:
    """A stand-in for a taught run: the Localizer wants `.features` and `.keyframes`."""

    def __init__(self, views: list[View]) -> None:
        self.features = [v.features for v in views]
        self.keyframes = [{"t": float(i)} for i in range(len(views))]


class PlatformMemory:
    def __init__(self, folder: Path, fov: float = 88.0) -> None:
        self.folder = folder
        self.fov = fov
        self._extractor = Extractor()
        self._views: list[View] = []
        self._walks = 0
        self._localizer: Localizer | None = None
        self.load()

    # -- storage ---------------------------------------------------------------------------

    def load(self) -> None:
        views: list[View] = []
        walks = 0
        for meta in sorted(self.folder.glob("walk_*/views.json")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            walks += 1
            for item in data.get("views", []):
                try:
                    cache = np.load(meta.parent / item["file"].replace(".jpg", ".npz"))
                    views.append(View(float(item["x"]), float(item["y"]), float(item["heading"]),
                                      Features(cache["points"], cache["descriptors"].astype(np.float32))))
                except (OSError, ValueError, KeyError):
                    continue
        self._views, self._walks, self._localizer = views, walks, None

    @property
    def size(self) -> int:
        return len(self._views)

    @property
    def walks(self) -> int:
        return self._walks

    @property
    def usable(self) -> bool:
        return len(self._views) >= MIN_VIEWS

    def describe(self) -> str:
        return f"{len(self._views)} видов из {self._walks} обходов" + ("" if self.usable else " (пока мало)")

    def add_walk(self, views: list[tuple[np.ndarray, tuple[float, float, float]]]) -> int:
        """Keep the pictures of one walk with their places [(rgb frame, (x, y, heading))]. Returns how many were kept."""
        if len(views) < 3:
            return 0
        stem = time.strftime("walk_%Y%m%d_%H%M%S")
        folder, counter = self.folder / stem, 1
        while folder.exists():  # two walks in the same second still keep apart
            folder, counter = self.folder / f"{stem}_{counter}", counter + 1
        folder.mkdir(parents=True, exist_ok=True)
        items = []
        for number, (rgb, (x, y, heading)) in enumerate(views, start=1):
            feats = self._extractor.extract(cv2.resize(rgb, (1280, 1024)) if rgb.shape[1] != 1280 else rgb)
            if feats is None:
                continue
            name = f"view_{number:04d}.jpg"
            (folder / name).write_bytes(jpeg_frame(rgb))
            np.savez_compressed(folder / name.replace(".jpg", ".npz"), points=feats.points,
                                descriptors=np.clip(np.rint(feats.descriptors), 0, 255).astype(np.uint8))
            items.append({"file": name, "x": round(x, 2), "y": round(y, 2), "heading": round(heading % 360.0, 1)})
        (folder / "views.json").write_text(json.dumps({"views": items}), encoding="utf-8")
        for old in sorted(self.folder.glob("walk_*"))[:-MAX_RUNS]:
            for file in old.glob("*"):
                file.unlink(missing_ok=True)
            old.rmdir()
        self.load()
        return len(items)

    # -- finding the place --------------------------------------------------------------------

    def locate(self, rgb: np.ndarray) -> tuple[float, float, float, int] | None:
        """(x, y, heading, inliers): where the most similar remembered picture was taken, turned by what the live
        picture is displaced from it; None while the memory is thin or nothing matches well."""
        if not self.usable:
            return None
        if self._localizer is None:
            self._localizer = Localizer([_Walk(self._views)])
        frame = cv2.resize(rgb, (1280, 1024)) if rgb.shape[1] != 1280 else rgb
        hit = self._localizer.locate(frame)
        if hit is None or hit.inliers < MIN_INLIERS:
            return None
        view = self._views[hit.index]
        return view.x, view.y, (view.heading - pixels_to_degrees(hit.shift_x, 1280, self.fov)) % 360.0, hit.inliers
