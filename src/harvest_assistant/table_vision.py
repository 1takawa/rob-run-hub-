"""Look at the plantation: find the status badge that floats above every growing table.

The game shows a small shield above every table: a leaf with a CLOCK while the plants are growing and a
leaf with a green CHECK mark when the table is ready to be harvested, and a little TABLE with a pot when the table
stands empty (nothing planted). ``TableVision.scan`` finds those badges on a screenshot and says which each one is.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .taught import FEATURE_WIDTH, Extractor, Features

logger = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent / "visual_assets"
WORK_WIDTH = 640          # the search runs on a half-size picture
SCALES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.12, 1.25)  # 0.7 is a table about 9 m away, 1.0 is arm's length
READY_LIME_PIXELS = 40    # lime pixels in the badge (a ready one has ~140, a growing one 0)
BADGE_SCORE = 0.85      # full badge; real badges score 0.96+, HUD look-alikes stay below 0.78


@dataclass(frozen=True)
class TableBadge:
    x: int
    y: int
    width: int
    height: int
    state: str    # "growing" | "ready" | "idle" (an empty table)
    score: float

    @property
    def label(self) -> str:
        return {"ready": "Стол · готов", "idle": "Стол · пустой"}.get(self.state, "Стол · растёт")


class TableVision:
    def __init__(self, templates: list[np.ndarray] | None = None) -> None:
        if templates is None:
            templates = [cv2.imread(str(ASSETS / name), cv2.IMREAD_COLOR)
                         for name in ("table_badge.png", "table_badge_ready.png", "table_badge_idle.png")]
        if any(template is None for template in templates):
            raise FileNotFoundError("table badge templates not found")
        self._templates = [cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) for template in templates]

    def _match(self, gray: np.ndarray, source: np.ndarray, scale: float) -> tuple[float, tuple[int, int], tuple[int, int]]:  # noqa: E501
        # the template is cut from a full-size screenshot; the search picture is half size
        factor = scale * (WORK_WIDTH / 1280)
        template = cv2.resize(source, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        if template.shape[0] >= gray.shape[0] or template.shape[1] >= gray.shape[1]:
            return 0.0, (0, 0), (0, 0)
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _low, high, _low_at, high_at = cv2.minMaxLoc(result)
        return float(high), high_at, (template.shape[1], template.shape[0])

    def scan(self, rgb: np.ndarray) -> list[TableBadge]:
        height, width = rgb.shape[:2]
        ratio = WORK_WIDTH / width
        small = cv2.resize(rgb, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        found: list[TableBadge] = []
        work = gray.copy()
        for _ in range(8):  # one pass per badge: take the best, blank it, look again
            best, which = max(((self._match(work, source, scale), n) for n, source in enumerate(self._templates)
                               for scale in SCALES), key=lambda item: item[0][0])
            score, at, size = best
            # far badges are tiny and look more like noise: ask for a better match there
            small = size[0] < 0.68 * self._templates[0].shape[1] * WORK_WIDTH / 1280
            if score < BADGE_SCORE + (0.04 if small else 0.0):
                break
            found.append(self._badge(at, size, ratio, score, rgb, which == 2))
            cv2.rectangle(work, at, (at[0] + size[0], at[1] + size[1]), int(work.mean()), -1)
        return found

    @staticmethod
    def _badge(at: tuple[int, int], size: tuple[int, int], ratio: float, score: float, rgb: np.ndarray,
               idle: bool = False) -> TableBadge:
        x, y, w, h = int(at[0] / ratio), int(at[1] / ratio), int(size[0] / ratio), int(size[1] / ratio)
        if idle:
            return TableBadge(x, y, w, h, "idle", score)
        # the two icons look alike except for the lime-green check mark of a ready table
        hsv = cv2.cvtColor(rgb[max(0, y):y + h, max(0, x):x + w], cv2.COLOR_RGB2HSV)
        lime = int(((hsv[..., 0] >= 30) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 110) & (hsv[..., 2] > 140)).sum())
        return TableBadge(x, y, w, h, "ready" if lime >= READY_LIME_PIXELS else "growing", score)


@dataclass
class TableView:
    """One remembered look at a table: the whole scene around it and where its badge was."""
    table_id: str
    features: Features
    badge: tuple[float, float]   # badge centre in FEATURE_WIDTH pixels
    folder: Path | None = None


def format_countdown(seconds: int) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class TableMemory:
    """Which physical table is which.

    When the route harvests a table the hub knows its name, so it keeps the picture of the whole scene and the
    spot of the table's badge. Later, for a badge seen on the screen, the saved pictures are matched to the live
    one (a homography between the two scenes) and the saved badge spots are carried over: the badge that lands
    on the live one tells which table it is. Identical-looking tables are told apart by where they stand in
    the room, not by how they look.
    """

    KEEP = 3              # latest views per table
    MIN_INLIERS = 14
    MAX_JUMP = 0.12       # how far (fraction of the picture width) a carried-over badge may land from a live one

    def __init__(self, folder: Path, vision: "TableVision | None" = None) -> None:
        self.folder = folder
        self._vision = vision
        self._extractor = Extractor()
        self._brute = cv2.BFMatcher(cv2.NORM_L2)
        self._lock = threading.RLock()
        self._views: list[TableView] = []
        self.reload()

    # ---- storage -----------------------------------------------------------------------

    def reload(self) -> None:
        views: list[TableView] = []
        for meta in sorted(self.folder.glob("*/view_*.json")):
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                cache = np.load(meta.with_suffix(".npz"))
                views.append(TableView(str(info["table_id"]), Features(cache["points"], cache["descriptors"].astype(np.float32)),
                                       (float(info["badge"][0]), float(info["badge"][1])), meta.parent))
            except (OSError, ValueError, KeyError):
                continue
        with self._lock:
            self._views = views

    @property
    def known_tables(self) -> set[str]:
        with self._lock:
            return {view.table_id for view in self._views}

    @staticmethod
    def _clean(rgb: np.ndarray, badges: list[TableBadge]) -> np.ndarray:
        """The badges look the same on every table: blank them so they never pull a match."""
        clean = rgb.copy()
        fill = [int(v) for v in rgb.reshape(-1, 3).mean(axis=0)]
        for b in badges:
            cv2.rectangle(clean, (b.x - 6, b.y - 6), (b.x + b.width + 6, b.y + b.height + 6), fill, -1)
        return clean

    def remember(self, table_id: str, rgb: np.ndarray, badges: list[TableBadge]) -> bool:
        """Keep the view in `rgb` for `table_id`. The table being harvested is the one the badge of which
        floats near the middle of the screen."""
        if not badges:
            return False
        height, width = rgb.shape[:2]
        target = min(badges, key=lambda b: (b.x + b.width / 2 - width * 0.5) ** 2 + (b.y + b.height / 2 - height * 0.3) ** 2)
        feats = self._extractor.extract(self._clean(rgb, badges))
        if feats is None:
            return False
        scale = FEATURE_WIDTH / width
        centre = ((target.x + target.width / 2) * scale, (target.y + target.height / 2) * scale)
        folder = self.folder / table_id
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stem = folder / f"view_{stamp}"
        np.savez_compressed(stem.with_suffix(".npz"), points=feats.points,
                            descriptors=np.clip(np.rint(feats.descriptors), 0, 255).astype(np.uint8))
        stem.with_suffix(".json").write_text(json.dumps({"table_id": table_id, "badge": list(centre), "frame": [width, height]}),
                                             encoding="utf-8")
        for old in sorted(folder.glob("view_*.json"))[:-self.KEEP]:
            old.unlink(missing_ok=True)
            old.with_suffix(".npz").unlink(missing_ok=True)
        self.reload()
        return True

    def rename(self, old: str, new: str) -> None:
        """An unnamed table (seen while it was not ripe) turned out to be `new`: move its views under that name."""
        source = self.folder / old
        if not source.is_dir():
            return
        target = self.folder / new
        target.mkdir(parents=True, exist_ok=True)
        for meta in sorted(source.glob("view_*.json")):
            info = json.loads(meta.read_text(encoding="utf-8"))
            info["table_id"] = new
            (target / meta.name).write_text(json.dumps(info), encoding="utf-8")
            meta.with_suffix(".npz").replace(target / meta.with_suffix(".npz").name)
        import shutil
        shutil.rmtree(source, ignore_errors=True)
        for stale in sorted(target.glob("view_*.json"))[:-self.KEEP]:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".npz").unlink(missing_ok=True)
        self.reload()

    def forget(self, table_id: str) -> None:
        import shutil
        shutil.rmtree(self.folder / table_id, ignore_errors=True)
        self.reload()

    # ---- recognition -------------------------------------------------------------------

    def identify(self, rgb: np.ndarray, badges: list[TableBadge]) -> dict[int, str]:
        """Index of a live badge -> table id, for every badge that could be recognised."""
        with self._lock:
            views = list(self._views)
        if not views or not badges:
            return {}
        height, width = rgb.shape[:2]
        scale = FEATURE_WIDTH / width
        live = self._extractor.extract(self._clean(rgb, badges))
        if live is None:
            return {}
        centres = [((b.x + b.width / 2) * scale, (b.y + b.height / 2) * scale) for b in badges]
        best: dict[int, tuple[int, str]] = {}
        for view in views:
            pairs = self._brute.knnMatch(view.features.descriptors, live.descriptors, k=2)
            matches = [(m.queryIdx, m.trainIdx) for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
            if len(matches) < self.MIN_INLIERS:
                continue
            source = np.float32([view.features.points[q] for q, _t in matches]).reshape(-1, 1, 2)
            target = np.float32([live.points[t] for _q, t in matches]).reshape(-1, 1, 2)
            matrix, mask = cv2.findHomography(source, target, cv2.RANSAC, 6.0)
            if matrix is None or mask is None or int(mask.sum()) < self.MIN_INLIERS:
                continue
            carried = cv2.perspectiveTransform(np.float32([[view.badge]]).reshape(-1, 1, 2), matrix).reshape(2)
            distances = [math.hypot(carried[0] - cx, carried[1] - cy) for cx, cy in centres]
            nearest = int(np.argmin(distances))
            if distances[nearest] > self.MAX_JUMP * FEATURE_WIDTH:
                continue
            inliers = int(mask.sum())
            if nearest not in best or inliers > best[nearest][0]:
                best[nearest] = (inliers, view.table_id)
        # a table can be only one badge: the strongest claim wins
        result: dict[int, str] = {}
        taken: set[str] = set()
        for index, (_inliers, table_id) in sorted(best.items(), key=lambda item: -item[1][0]):
            if table_id not in taken:
                result[index] = table_id
                taken.add(table_id)
        return result


class TableWatcher(threading.Thread):
    """Background look at the screen: mark the tables in view, ready or growing, with the timer of each one."""

    PERIOD = 1.0

    def __init__(self, get_screen, enabled, busy, publish, report, memory: "TableMemory | None" = None, timers=None) -> None:
        super().__init__(name="table-vision", daemon=True)
        self._get_screen, self._enabled, self._busy = get_screen, enabled, busy
        self._publish, self._report = publish, report
        self._memory = memory
        self._timers = timers or (lambda: {})
        self._stop_event = threading.Event()
        self._shown = False
        self._last_seen = 0.0
        self._last_text = ""

    def stop(self) -> None:
        self._stop_event.set()

    def _clear(self) -> None:
        if self._shown:
            self._shown = False
            try:
                self._publish({"active": False, "ts": time.time()})
            except Exception:
                logger.debug("table vision: clearing failed", exc_info=True)

    def _say(self, text: str) -> None:
        if text != self._last_text:
            self._last_text = text
            try:
                self._report(text)
            except Exception:
                pass

    @staticmethod
    def describe(badge: TableBadge, table: tuple[str, int | None] | None) -> str:
        """The words over a table: what the picture says, and what the timer of THIS table says."""
        if table is None:
            return badge.label
        name, remaining = table
        if badge.state == "ready":
            if remaining is not None and remaining > 30:
                return f"{name} · готов (таймер: {format_countdown(remaining)})"
            return f"{name} · готов"
        if remaining is None:
            return f"{name} · растёт"
        if remaining == 0:
            return f"{name} · по таймеру готов"
        return f"{name} · {format_countdown(remaining)}"

    def run(self) -> None:
        try:
            vision = TableVision()
        except Exception:
            logger.exception("Table vision is unavailable")
            return
        while not self._stop_event.wait(self.PERIOD):
            try:
                if self._busy():
                    continue  # the autopilot draws its own picture
                screen = self._get_screen()
                if not self._enabled() or screen is None or not screen.is_automation_ready():
                    self._clear()
                    continue
                frame = screen.capture()
                badges = vision.scan(frame)
                now = time.monotonic()
                if badges:
                    self._last_seen = now
                    self._shown = True
                    names = self._memory.identify(frame, badges) if self._memory is not None else {}
                    timers = self._timers()
                    ready = sum(1 for b in badges if b.state == "ready")
                    growing = len(badges) - ready
                    text = " · ".join(part for part in (f"готово столов: {ready}" if ready else "",
                                                       f"растёт: {growing}" if growing else "") if part)
                    self._say("В кадре " + text + (f"; узнал по памяти: {len(names)}." if names else "."))
                    self._publish({
                        "active": True, "ts": time.time(), "mode": "tables", "frame": [frame.shape[1], frame.shape[0]],
                        "status": text[:1].upper() + text[1:], "ready": ready,
                        "tables": [{"x": b.x, "y": b.y, "w": b.width, "h": b.height, "state": b.state,
                                    "label": self.describe(b, timers.get(names.get(i, "")))}
                                   for i, b in enumerate(badges)],
                    })
                elif now - self._last_seen > 2.0:
                    self._clear()
                    self._say("В кадре нет значков над столами.")
            except Exception:
                logger.debug("table vision tick failed", exc_info=True)
