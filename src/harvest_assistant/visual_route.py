"""Experimental landmark-guided route through the standard house and grow room."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from .door_vision import find_door
from .loot import LootReader
from .map_route import MapPilot
from .models import RouteEvent, RunSnapshot, RunState
from .radar import RadarExtractor, RadarIndex, register as radar_register
from .zones import ZONE_TITLES, zone_for


logger = logging.getLogger(__name__)


class VisualRouteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LandmarkMatch:
    center_x: float
    center_y: float
    width: float
    height: float
    inliers: int


class LandmarkMatcher:
    """Locate a photographed object at a different scale or viewing angle."""

    def __init__(self, asset_dir: Path) -> None:
        self.asset_dir = asset_dir
        self._sift = cv2.SIFT_create(nfeatures=1200)
        self._matcher = cv2.BFMatcher(cv2.NORM_L2)
        self._references: dict[str, tuple[tuple[int, int], object, np.ndarray]] = {}

    def detect(self, frame: np.ndarray, name: str) -> LandmarkMatch | None:
        reference = self._references.get(name)
        if reference is None:
            path = self.asset_dir / f"{name}.png"
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise FileNotFoundError(path)
            points, descriptors = self._sift.detectAndCompute(image, None)
            if descriptors is None or len(points) < 8:
                raise ValueError(f"Недостаточно деталей на ориентире: {name}")
            reference = ((image.shape[1], image.shape[0]), points, descriptors)
            self._references[name] = reference

        ref_size, ref_points, ref_descriptors = reference
        source_height, source_width = frame.shape[:2]
        if name == "container_far":
            # The distant building is small: remove the HUD, sky and ground so
            # their thousands of features do not crowd it out of the SIFT budget.
            origin_x, origin_y = int(source_width * 0.12), int(source_height * 0.18)
            view_rgb = frame[origin_y:int(source_height * 0.68), origin_x:int(source_width * 0.80)]
        else:
            origin_x, origin_y = 0, 0
            view_rgb = frame
        scale = min(1.0, 1000.0 / view_rgb.shape[1])
        view = cv2.cvtColor(view_rgb, cv2.COLOR_RGB2GRAY)
        if scale < 1.0:
            view = cv2.resize(view, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        points, descriptors = self._sift.detectAndCompute(view, None)
        if descriptors is None or len(points) < 8:
            return None
        pairs = self._matcher.knnMatch(ref_descriptors, descriptors, k=2)
        good = [first for first, second in pairs if first.distance < 0.72 * second.distance]
        if len(good) < 8:
            return None
        source = np.float32([ref_points[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
        destination = np.float32([points[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
        matrix, mask = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
        minimum_inliers = 24 if name == "container_door" else 8
        if matrix is None or mask is None or int(mask.sum()) < minimum_inliers:
            return None
        corners = np.float32([
            [0, 0], [ref_size[0], 0], [ref_size[0], ref_size[1]], [0, ref_size[1]],
        ]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2) / scale
        projected += np.float32([origin_x, origin_y])
        if not np.isfinite(projected).all():
            return None
        left, top = projected.min(axis=0)
        right, bottom = projected.max(axis=0)
        width, height = float(right - left), float(bottom - top)
        if width < 12 or height < 12 or width > source_width * 1.5 or height > source_height * 1.5:
            return None
        if right < 0 or bottom < 0 or left > source_width or top > source_height:
            return None
        return LandmarkMatch(float((left + right) / 2), float((top + bottom) / 2), width, height, int(mask.sum()))


def horizontal_shift(before: np.ndarray, after: np.ndarray) -> float | None:
    """How far (in screen px) the scene slid sideways between two frames; None if it cannot be told.

    Only the right-middle of the screen is compared: no HUD (minimap, chat, hotbar) and no player model.
    """
    def prepare(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        band = frame[int(height * 0.25):int(height * 0.70), int(width * 0.52):int(width * 0.97)]
        gray = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA).astype(np.float32)
        return gray * cv2.createHanningWindow((gray.shape[1], gray.shape[0]), cv2.CV_32F)

    (shift_x, _shift_y), response = cv2.phaseCorrelate(prepare(before), prepare(after))
    if response < 0.05 or not np.isfinite(shift_x):
        return None
    return float(shift_x) * 2.0  # back to full-resolution pixels


def choose_movement(center_x: float, frame_width: int) -> str:
    """Steer the camera before taking another forward step."""
    delta = center_x / frame_width - 0.5
    if delta < -0.10:
        return "left"
    if delta > 0.10:
        return "right"
    return "forward"


def normalize_prompt(text: str) -> str | None:
    """Accept common Russian OCR distortions of Open/Close without guessing other actions."""
    words = ["".join(char for char in word.casefold() if char.isalpha()) for word in text.split()]
    for word in words:
        if SequenceMatcher(None, word, "закрыть").ratio() >= 0.72:
            return "close"
        if SequenceMatcher(None, word, "открыть").ratio() >= 0.68:
            return "open"
    return None


def is_window_text(text: str) -> bool:
    """The title «ОКНО» of a window prompt. A window has the same «Открыть / Закрыть» line as a door, but is not one."""
    words = ["".join(char for char in word.casefold() if char.isalpha()) for word in text.split()]
    return any(len(word) >= 3 and SequenceMatcher(None, word, "окно").ratio() >= 0.74 for word in words)


def is_door_title(text: str) -> bool:
    """The title line «ДВЕРЬ» (or «ВОРОТА») above the action: only then is the prompt about a door."""
    words = ["".join(char for char in word.casefold() if char.isalpha()) for word in text.split()]
    return any(len(word) >= 4 and (SequenceMatcher(None, word, "дверь").ratio() >= 0.7
                                   or SequenceMatcher(None, word, "ворота").ratio() >= 0.7) for word in words)


def is_harvest_text(text: str) -> bool:
    """The «Сбор урожая» line of a table menu, with room for OCR distortions."""
    words = ["".join(char for char in word.casefold() if char.isalpha()) for word in text.split()]
    for word in words:
        if len(word) >= 4 and SequenceMatcher(None, word, "урожая").ratio() >= 0.7:
            return True
        if len(word) >= 3 and SequenceMatcher(None, word, "сбор").ratio() >= 0.74:
            return True
    return False


class PromptReader:
    """Read only the interaction prompt area; OCR runs on demand, not continuously."""

    def __init__(self, require_door: bool = False) -> None:
        self._ocr = LootReader()
        # True: «Открыть / Закрыть» counts only under the title «ДВЕРЬ». The recorder keeps False: it must not lose a
        # door of the demonstration because of one badly read title.
        self.require_door = require_door

    def read(self, frame: np.ndarray) -> str | None:
        height, width = frame.shape[:2]
        crop = frame[int(height * 0.49):int(height * 0.69), int(width * 0.47):int(width * 0.74)]
        return self.read_crop(crop)

    def harvest_visible(self, frame: np.ndarray) -> bool:
        """True while the menu of a table with its «Сбор урожая» line is on screen (we are at a table)."""
        height, width = frame.shape[:2]
        crop = frame[int(height * 0.49):int(height * 0.72), int(width * 0.47):int(width * 0.74)]
        if crop.size == 0:
            return False
        try:
            lines = asyncio.run(self._ocr._ocr_lines(np.ascontiguousarray(crop)))
        except Exception as exc:
            logger.warning("Harvest prompt OCR failed: %s", exc)
            return False
        return any(is_harvest_text(text) for text, _box in lines)

    def _lines(self, crop: np.ndarray) -> list[str]:
        """The text lines of the prompt area. Lines of our own overlay are dropped: its label «Дверь · открыть · вижу»
        names the door and the action in ONE line with dots between, the game writes the title and the action in two."""
        if crop.size == 0:
            return []
        try:
            lines = asyncio.run(self._ocr._ocr_lines(np.ascontiguousarray(crop)))
        except Exception as exc:
            logger.warning("Visual route prompt OCR failed: %s", exc)
            return []
        return [text for text, _box in lines
                if "·" not in text and not (is_door_title(text) and normalize_prompt(text) is not None)]

    def read_detail(self, frame: np.ndarray) -> tuple[str | None, bool]:
        """(action, titled): «open»/«close» if a door action is offered, and whether the title «ДВЕРЬ» was read with it.
        A window («ОКНО») is never an action. The caller decides whether an untitled action is enough where it stands."""
        height, width = frame.shape[:2]
        lines = self._lines(frame[int(height * 0.49):int(height * 0.69), int(width * 0.47):int(width * 0.74)])
        if any(is_window_text(text) for text in lines):
            logger.info("prompt: a window («ОКНО»), not a door: ignored")
            return None, False
        for text in lines:
            result = normalize_prompt(text)
            if result is not None:
                return result, any(is_door_title(other) for other in lines)
        return None, False

    def read_crop(self, crop: np.ndarray) -> str | None:
        lines = self._lines(crop)
        if any(is_window_text(text) for text in lines):
            logger.info("prompt: a window («ОКНО»), not a door: ignored")
            return None
        for text in lines:
            result = normalize_prompt(text)
            if result is not None:
                if self.require_door and not any(is_door_title(other) for other in lines):
                    logger.info("prompt: «%s» without the title «ДВЕРЬ»: not a door, ignored (%s)", text, " | ".join(lines))
                    return None
                return result
        return None


class VisualRouteTestRunner(MapPilot):
    """Bounded pilot route: re-observe after every small camera or walking pulse."""

    def __init__(
        self,
        screen: object,
        driver: object,
        assets_dir: Path,
        on_state_change: Callable[[RunSnapshot], None],
        *,
        matcher: object | None = None,
        prompt_reader: object | None = None,
        failure_dir: Path | None = None,
        recorded_route: object | None = None,
        taught_runs: list | None = None,
    ) -> None:
        self.screen = screen
        self.driver = driver
        self.recorded_route = recorded_route
        self.taught_runs = taught_runs or []
        self.matcher = matcher or LandmarkMatcher(assets_dir)
        self.prompt_reader = prompt_reader or PromptReader(require_door=True)
        self.on_state_change = on_state_change
        self.failure_dir = failure_dir
        self.trace_dir = failure_dir / "visual_trace" if failure_dir is not None else None
        # Mouse pixels per degree of camera turn. The route measures the real value from the picture at the
        # start (calibrate_turn); 49 is what the first live trial implied.
        self.px_per_degree = 49.0
        self.vision_sink: Callable[[dict], None] | None = None  # the overlay draws what the pilot sees
        self.overlay_busy = True  # False while the pilot only waits: the plantation overlay may then draw timers
        self._came_down = False   # the stairs were just walked down: the platform is to the LEFT of the landing
        self.stairs_taken = False # the stairs were walked by `_take_stairs`: we stand at their end facing north
        self.route_map = None     # the map of all taught runs (routemap.RouteMap), built by `ensure_map`
        self.hand_over_at_platform = False  # True: after the last gate the stairs and the left turn are done here, then the plan
        self.at_platform_entry = False      # set when it did: we stand at the end of the stairs facing north
        self.metres_per_second = 2.58       # metres covered in one second of holding W (set from the stairs of the map)
        self._radar_extractor: RadarExtractor | None = None
        self.use_map_route = True           # walk by the poses of the mini-map (map_route.py) where they can be read
        self.stop_event = getattr(driver, "stop_event", threading.Event())
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._state = RunState.STOPPED
        self._reason = "Зрительный тест не запущен."
        self._stage = ""

    @property
    def worker_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return RunSnapshot(state=self._state, reason=self._reason)

    def _set_state(self, state: RunState, reason: str) -> None:
        with self._lock:
            self._state = state
            self._reason = reason
            snapshot = RunSnapshot(state=state, reason=reason)
        self.on_state_change(snapshot)

    def start(self) -> None:
        if self.worker_alive:
            return
        self._thread = threading.Thread(target=self._run, name="visual-route-test", daemon=True)
        self._thread.start()

    def pause(self, reason: str = "Зрительный маршрут на паузе.") -> None:
        if hasattr(self.driver, "pause"):
            self.driver.pause()
        self._set_state(RunState.PAUSED, reason)

    def resume(self) -> None:
        if self.stop_event.is_set():
            raise VisualRouteError("Тест уже остановлен; запусти его заново из дома.")
        if hasattr(self.driver, "resume"):
            self.driver.resume()
        self._set_state(RunState.ROUTING, f"Продолжаю: {self._stage}.")

    def stop(self) -> bool:
        if hasattr(self.driver, "stop"):
            self.driver.stop()
        else:
            self.stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread and thread.is_alive():
            return False
        self._set_state(RunState.STOPPED, "Зрительный тест остановлен.")
        return True

    def _execute(self) -> None:
        """What this runner does once started (a subclass runs a whole harvest cycle here)."""
        if self.taught_runs:
            self.run_taught(self.taught_runs)
        elif self.recorded_route is not None:
            self.run_recorded(self.recorded_route)
        else:
            self.run_route()
        self._set_state(RunState.STOPPED, "Путь пройден. Сбор не запускался.")

    def _run(self) -> None:
        try:
            self._execute()
        except Exception as exc:
            if self.stop_event.is_set():
                self._set_state(RunState.STOPPED, "Зрительный тест остановлен.")
            else:
                logger.exception("Visual route stopped at %s", self._stage)
                self._save_failure()
                self._set_state(RunState.ERROR, f"Зрительный тест: {self._stage}: {exc}")
        finally:
            self._show_off()
            if hasattr(self.driver, "stop"):
                self.driver.stop()

    def _save_failure(self) -> None:
        if self.failure_dir is None:
            return
        try:
            self.failure_dir.mkdir(parents=True, exist_ok=True)
            frame = self.screen.capture()
            Image.fromarray(frame).save(self.failure_dir / "visual_route_failure.png")
        except Exception:
            logger.exception("Could not save the visual route failure screenshot")

    def _mark(self, name: str) -> None:
        self._stage = name
        logger.info("Visual route stage: %s", name)
        self._save_trace(name)
        self._set_state(RunState.ROUTING, f"Зрительный тест: {name}.")

    def _save_trace(self, name: str) -> None:
        """One small frame per stage, so a live trial can be reviewed afterwards."""
        if self.trace_dir is None:
            return
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            frame = self.screen.capture()
            scale = 640 / frame.shape[1]
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
            Image.fromarray(small).save(self.trace_dir / f"{time.strftime('%H%M%S')}_{safe}.jpg", quality=72)
            for old in sorted(self.trace_dir.glob("*.jpg"))[:-30]:
                old.unlink(missing_ok=True)
        except Exception:
            logger.exception("Could not save a visual route trace frame")

    HORIZONTAL_FOV = 88.0  # degrees; fitted on the user's recordings (a full turn closes at 360 deg)

    def calibrate_turn(self) -> None:
        """Turn the camera a little, see how far the picture moved, derive mouse px per degree."""
        probe = 300
        before = self.screen.capture()
        self.driver.move_relative(probe, 0)
        self.driver.wait(0.60)  # the camera eases into the move; measuring early under-counts the turn
        after = self.screen.capture()
        self.driver.move_relative(-probe, 0)
        self.driver.wait(0.50)
        shift = horizontal_shift(before, after)
        if shift is None or abs(shift) < 12:
            logger.warning("Camera calibration failed (shift=%s); using the default turn rate", shift)
            return
        width = before.shape[1]
        focal = (width / 2) / math.tan(math.radians(self.HORIZONTAL_FOV / 2))
        screen_px_per_degree = focal * math.pi / 180
        mouse_per_screen = probe / abs(shift)
        measured = mouse_per_screen * screen_px_per_degree
        if not 8.0 < measured < 200.0:
            logger.warning("Camera calibration gave an odd value %.2f px/deg; using the default", measured)
            return
        self.px_per_degree = measured
        logger.info("Camera turn rate: %.2f mouse px per degree (picture moved %.0f px for %d)", measured, shift, probe)

    def degrees(self, value: float) -> int:
        return int(round(value * self.px_per_degree))

    def turn(self, degrees: float) -> None:
        """Turn the camera by about `degrees` (positive = right) in short, guarded movements."""
        remaining = self.degrees(degrees)
        while remaining:
            part = max(-300, min(300, remaining))
            self.driver.move_relative(part, 0)
            self.driver.wait(0.08)
            remaining -= part

    def _detect(self, frame: np.ndarray, landmarks: str | tuple[str, ...]) -> tuple[str, LandmarkMatch] | None:
        names = (landmarks,) if isinstance(landmarks, str) else landmarks
        best: tuple[str, LandmarkMatch] | None = None
        for name in names:
            match = self.matcher.detect(frame, name)
            if match is not None and (best is None or match.inliers > best[1].inliers):
                best = (name, match)
        return best

    def seek(
        self,
        landmark: str | tuple[str, ...],
        expected_prompt: str | None,
        *,
        min_height_ratio: float = 0.0,
        max_steps: int = 70,
        scan_direction: int = 1,
    ) -> None:
        """Scan, steer, and walk in small steps until a landmark or prompt is reached.

        `landmark` may list several pictures of the same thing (far / near view); the best one is used.
        When nothing is visible the camera sweeps: 24 steps one way, then back and beyond, so the
        target is found even if it was behind the starting direction.
        """
        scan_step = self.degrees(8)
        steer_step = self.degrees(6.7)
        lost = 0
        for step in range(max_steps):
            frame = self.screen.capture()
            found = self._detect(frame, landmark)
            match = found[1] if found is not None else None
            logger.info(
                "seek %s step %d: %s", landmark, step,
                "not found" if found is None else
                f"{found[0]} at ({match.center_x:.0f},{match.center_y:.0f}) height {match.height:.0f} inliers {match.inliers}",
            )
            if match is not None and expected_prompt is not None:
                observed = self.prompt_reader.read(frame)
                if observed == expected_prompt:
                    return
                if observed is not None:
                    raise VisualRouteError(f"У точки {landmark} действие {observed}, ожидалось {expected_prompt}.")
            elif match is not None and min_height_ratio > 0:
                if match.height >= frame.shape[0] * min_height_ratio:
                    return
            if match is None:
                lost += 1
                # A long first sweep (54 steps of 8 degrees = more than a full turn), then the other
                # way, then again: a lost bot gets plenty of room to look around.
                sweep = (lost - 1) // 54
                direction = scan_direction if sweep % 2 == 0 else -scan_direction
                self.driver.move_relative(scan_step * direction, 0)
                self.driver.wait(0.12)
                # Very close to a door the landmark picture no longer matches, but the game's own
                # prompt still appears when the crosshair crosses the door: accept that too.
                if expected_prompt is not None and lost % 2 == 0 and self.prompt_reader.read(self.screen.capture()) == expected_prompt:
                    logger.info("seek %s: prompt %s seen without a landmark match", landmark, expected_prompt)
                    return
            else:
                lost = 0
                movement = choose_movement(match.center_x, frame.shape[1])
                if movement == "left":
                    self.driver.move_relative(-steer_step, 0)
                    self.driver.wait(0.12)
                elif movement == "right":
                    self.driver.move_relative(steer_step, 0)
                    self.driver.wait(0.12)
                elif expected_prompt is not None and abs(match.center_y / frame.shape[0] - 0.5) > 0.14:
                    self.driver.move_relative(0, -22 if match.center_y < frame.shape[0] * 0.5 else 22)
                    self.driver.wait(0.12)
                else:
                    self.driver.play_key("w", 0.18)
            if step % 3 == 2:
                self.driver.wait(0.08)
        raise VisualRouteError(f"Не нашёл точку {landmark} за {max_steps} шагов; движение остановлено.")

    def interact(self, expected_prompt: str) -> None:
        """Press E only while the expected door action is visibly available."""
        frame = self.screen.capture()
        observed = self.prompt_reader.read(frame)
        if observed != expected_prompt:
            raise VisualRouteError(f"Ожидал на двери {expected_prompt}, на экране {observed or 'нет подсказки'}.")
        self.driver.play_key("e", 0.12)
        self.driver.wait(0.20)

    def _walk_until(self, target: str, *, steps: int, turn_first: int = 0) -> None:
        if turn_first:
            self.driver.move_relative(turn_first, 0)
        for step in range(steps):
            frame = self.screen.capture()
            match = self.matcher.detect(frame, target)
            logger.info("walk to %s step %d: %s", target, step, "not found" if match is None else f"inliers {match.inliers}")
            if match is not None:
                return
            self.driver.play_key("w", 0.18)
        raise VisualRouteError(f"Не достигнута точка {target}; остановился после {steps} шагов.")

    def _turn_until_prompt(self, expected: str, *, steps: int = 32) -> None:
        for _ in range(steps):
            if self.prompt_reader.read(self.screen.capture()) == expected:
                return
            self.driver.move_relative(38, 0)
        raise VisualRouteError(f"Не вижу подсказку двери {expected}; остановился.")

    def screen_to_mouse(self, screen_px: float, frame_width: int) -> int:
        """Mouse pixels that turn the camera by about `screen_px` pixels of picture movement."""
        focal = (frame_width / 2) / math.tan(math.radians(self.HORIZONTAL_FOV / 2))
        return int(round(screen_px * self.px_per_degree / (focal * math.pi / 180)))

    def _replay(self, events: list) -> None:
        started = time.monotonic()
        lag = 0.0
        for event in events:
            while time.monotonic() < started + event.elapsed_offset + lag:
                self.driver.wait(min(0.05, max(0.0, started + event.elapsed_offset + lag - time.monotonic())))
            if event.kind == "interact":
                t0 = time.monotonic()
                self._recorded_interact(str(event.value or ""))
                lag += time.monotonic() - t0
            else:
                self.driver.play_route_event(event)

    ALIGN_YAW = 3.0     # degrees of heading error that are worth correcting
    ALIGN_PITCH = 5.0
    TURN_GAIN = 0.85    # under-shoot a little: the next look corrects the rest
    CHUNK = 0.6         # longest key press between two looks, seconds

    LOOK_SPEED = 80.0   # degrees per second, average: slow enough to look like a hand, not a twitch
    LOOK_TICK = 0.016

    def _look(self, yaw: float, pitch: float) -> None:
        """Turn the camera by `yaw` degrees (right = +) and `pitch` degrees (down = +), smoothly: the speed
        rises and falls like a hand moving the mouse instead of one jump."""
        yaw = max(-180.0, min(180.0, yaw))
        pitch = max(-12.0, min(12.0, pitch))
        total_x = int(round(yaw * self.TURN_GAIN * self.px_per_degree))
        total_y = int(round(pitch * self.TURN_GAIN * self.px_per_degree))
        if not total_x and not total_y:
            return
        size = max(abs(yaw), abs(pitch)) * self.TURN_GAIN
        duration = max(0.14, min(1.2, size / self.LOOK_SPEED))
        steps = max(3, int(duration / self.LOOK_TICK))
        sent_x = sent_y = 0
        for step in range(1, steps + 1):
            t = step / steps
            eased = t * t * (3.0 - 2.0 * t)  # smoothstep: slow start, slow stop
            target_x, target_y = int(round(total_x * eased)), int(round(total_y * eased))
            if target_x != sent_x or target_y != sent_y:
                self.driver.move_relative(target_x - sent_x, target_y - sent_y)
                sent_x, sent_y = target_x, target_y
            self.driver.wait(duration / steps)

    def _hold(self, key: str, seconds: float, shift: bool = False) -> None:
        keys = (["shift"] if shift else []) + [key]
        try:
            for name in keys:
                self.driver.play_route_event(RouteEvent(0.0, "key_down", name))
            self.driver.wait(seconds)
        finally:
            for name in reversed(keys):
                self.driver.play_route_event(RouteEvent(0.0, "key_up", name))

    def _show(self, **data) -> None:
        """Tell the overlay what the autopilot sees and intends (drawn as boxes over the game)."""
        sink = getattr(self, "vision_sink", None)
        if sink is None:
            return
        try:
            sink({"active": True, "ts": time.time(), **data})
        except Exception:
            logger.debug("vision sink failed", exc_info=True)

    def _show_off(self) -> None:
        sink = getattr(self, "vision_sink", None)
        if sink is None:
            return
        try:
            sink({"active": False, "ts": time.time()})
        except Exception:
            logger.debug("vision sink failed", exc_info=True)

    def _try_interact(self, expected: str) -> bool:
        """One look at the prompt: press E if the game offers what was recorded. False = no prompt yet."""
        observed = self.prompt_reader.read(self.screen.capture())
        logger.info("door check: expected %s, screen %s", expected, observed)
        if observed == expected or (expected not in ("open", "close") and observed is not None):
            self.driver.play_key("e", 0.12)
            self.driver.wait(0.25)
            return True
        if observed is not None:
            logger.info("door already %s, E skipped", "open" if observed == "close" else "closed")
            return True
        return False

    MAX_CREEPS = 12     # small steps towards a door whose prompt is not shown yet

    def _door_in_sight(self, frame: np.ndarray, kind: str):
        """(centre x, centre y, size) of the door on the picture, each as a share of the picture; None if none is seen.
        The house door is found by its colour and shape (see door_vision), the container by its pictures."""
        if kind in ("house", "porch"):
            # on the porch the door is in the shade of the roof (a darker colour), the light profile is tried too
            sight = (find_door(frame, "porch") or find_door(frame, "house")) if kind == "porch" else find_door(frame, "house")
            return None if sight is None else (sight.center_x, sight.center_y, sight.height)
        for name in ("container_door", "container_near", "container_far"):
            try:
                found = self.matcher.detect(frame, name)
            except Exception:
                logger.debug("landmark %s failed", name, exc_info=True)
                continue
            if found is not None:
                height, width = frame.shape[:2]
                return found.center_x / width, found.center_y / height, found.height / height
        return None

    def _approach_door(self, expected: str, kind: str, steps: int = 26) -> bool:
        """Go to the door by looking at it: turn until it is in the middle of the picture, walk up to it, and press E
        only when the game itself calls it «ДВЕРЬ». Nothing else is pressed: not a window, not a table, not a wall."""
        label = "контейнер" if kind == "container" else "дом"
        looked = 0.0
        for index in range(steps):
            if self._try_interact(expected):
                return True
            frame = self.screen.capture()
            sight = self._door_in_sight(frame, kind)
            if sight is None:
                # one calm circle to the right, 30 degrees at a time, looking at every stop (never swinging back and forth)
                logger.info("door: %s not in sight, turning on (%.0f deg so far)", label, looked)
                if looked >= 360.0:
                    break
                self._look(30.0, 0.0)
                looked += 30.0
                self.driver.wait(0.3)
                continue
            center_x, center_y, size = sight
            error = (center_x - 0.5) * self.HORIZONTAL_FOV
            logger.info("door: %s in sight, %.0f%% to the %s, %.0f%% of the picture high", label, abs(error) / self.HORIZONTAL_FOV * 100,
                        "right" if error > 0 else "left", size * 100)
            if abs(error) > 4.0:
                self._look(error, 0.0)
                self.driver.wait(0.3)
                continue
            self._hold("w", 0.15 if size >= 0.8 else 0.3)
            self.driver.wait(0.1)
        return self._try_interact(expected)

    CRUISE_MAX = 4.0        # longest single walk without stopping to think, seconds
    CRUISE_MIN_HOLD = 0.45  # shorter recorded steps are simply pressed
    def ensure_map(self, runs: list, plans: list, width: int):
        """The map of the taught runs laid over each other (a few seconds of picture matching the first time, then
        cached next to the runs). None when there is nothing to build it from."""
        if self.route_map is not None or not runs:
            return self.route_map
        from .routemap import align_by_pictures, build

        self._show(status="Строю карту по пройденным маршрутам", mode="search", stage="карта", frame=[width, int(width * 0.8)])
        cache = runs[0].folder.parent / "routemap.json"
        try:
            self.route_map = build(runs, plans, width, self.HORIZONTAL_FOV,
                                   align_by_pictures(runs, plans, width, self.HORIZONTAL_FOV), cache)
        except Exception:
            logger.exception("Could not build the route map")
            self.route_map = None
        return self.route_map

    def _radar(self, pose: tuple[float, float, float] | None = None, goal: tuple[float, float] | None = None,
               zoom: bool = False) -> dict | None:
        return self.route_map.view(pose, goal, zoom) if self.route_map is not None else None

    DESCENT_MAX = 14.0      # seconds allowed for the stairs
    DESCENT_EXTRA = 0.6     # keep walking this long after the purple light of the platform first shows

    def _descend_stairs(self) -> None:
        """The stairs are one straight way down, so no picture is needed (inside the container it is dark and every
        step looks like the next). Walk on, without touching the camera, until the purple light of the grow room has
        shown up and a little further, onto the landing."""
        from .zones import is_platform

        started = time.monotonic()
        seen_at: float | None = None
        previous = None
        still = 0
        try:
            self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
            while time.monotonic() - started < self.DESCENT_MAX:
                frame = self.screen.capture()
                if seen_at is None and is_platform(frame):
                    seen_at = time.monotonic()
                if seen_at is not None and time.monotonic() - seen_at >= self.DESCENT_EXTRA:
                    break
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (64, 51)).astype(np.float32)
                if previous is not None and time.monotonic() - started > 1.0:
                    still = still + 1 if float(np.abs(small - previous).mean()) < 1.2 else 0
                    if still >= 6:
                        raise VisualRouteError("Упёрся на лестнице: картинка не меняется.")
                previous = small
                self.driver.wait(0.05)
            else:
                raise VisualRouteError("Лестница не кончилась за отведённое время.")
        finally:
            self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))
        self._came_down = True

    MAX_CARRY_ACTIONS = 8   # steps taken from a remembered pose without recognising the place

    def _gyro_turn(self, degrees: float, tolerance: float = 3.0) -> float:
        """Turn the camera by `degrees` (right = +) and say how far it REALLY turned.

        The mini-map in the corner turns with the camera whatever the scene looks like, so two mini-maps taken before and
        after a turn give the turn itself (to about a degree, also in the dark of the container where pictures fail). The
        turn is repeated until what is left is within `tolerance`."""
        if self._radar_extractor is None:
            self._radar_extractor = RadarExtractor()
        turned = 0.0
        for _ in range(5):
            left = degrees - turned
            if abs(left) <= tolerance:
                break
            part = max(-90.0, min(90.0, left))
            before = self._radar_extractor.extract(self.screen.capture())
            self._look(part, 0.0)
            self.driver.wait(0.4)
            after = self._radar_extractor.extract(self.screen.capture())
            result = radar_register(before, after) if before is not None and after is not None else None
            if result is None:
                turned += part * self.TURN_GAIN  # no reading: trust the command, a later look corrects it
                logger.info("gyro: no mini-map reading, assumed %.0f deg", part * self.TURN_GAIN)
            else:
                turned += -result[0]  # the map turns the opposite way to the camera
                logger.info("gyro: asked %.0f deg, the mini-map says %.0f deg", part, -result[0])
        return turned

    STAIRS_METRES = 6.4  # from the closed gates to the end of the stairs, on the plan

    def _take_stairs(self, plan, door_step) -> None:
        """The last gate is closed. By the rule of the place, not by pictures (the container is dark, every step of the
        stairs looks like the next): turn round to the stairs as the author did, walk the length of the stairs in one go,
        and turn LEFT at the end: the room with the tables lies to the left of the end of the stairs."""
        first = plan.next_step(door_step.t1 + 0.3)
        yaw_gate = plan.yaw_at(door_step.t0)
        yaw_stairs = plan.yaw_at(first.t0) if first is not None and first.kind == "move" else yaw_gate + 180.0
        turn = (yaw_stairs - yaw_gate + 180.0) % 360.0 - 180.0
        self._show(status="Разворачиваюсь к лестнице", mode="ok", stage="лестница", frame=[1280, 1024])
        self._gyro_turn(turn)
        self._show(status="Спускаюсь по лестнице", mode="ok", stage="лестница", frame=[1280, 1024])
        self._walk_straight(self.STAIRS_METRES / self.metres_per_second)
        self._show(status="Лестница кончилась: поворачиваю налево", mode="ok", stage="лестница", frame=[1280, 1024])
        self._gyro_turn(-90.0)
        self.at_platform_entry = True
        self.stairs_taken = True

    def _walk_straight(self, seconds: float) -> None:
        """Hold W for `seconds` as one movement; if the picture stops changing early, something is in the way."""
        started = time.monotonic()
        previous = None
        still = 0
        try:
            self.driver.play_route_event(RouteEvent(0.0, "key_down", "w"))
            while time.monotonic() - started < seconds:
                frame = self.screen.capture()
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), (64, 51)).astype(np.float32)
                if previous is not None and time.monotonic() - started > 0.8:
                    still = still + 1 if float(np.abs(small - previous).mean()) < 1.0 else 0
                    if still >= 6:
                        raise VisualRouteError("Упёрся на лестнице: картинка не меняется.")
                previous = small
                self.driver.wait(0.05)
        finally:
            self.driver.play_route_event(RouteEvent(0.0, "key_up", "w"))

    def _pass_through_door(self) -> None:
        """A door was just opened: walk straight until its prompt is gone, so we never stop in the frame.

        Decided by what is on screen, not by a recorded number of seconds: the door prompt stays while the
        door is right in front of us and vanishes once we are through."""
        self.driver.wait(0.6)  # the door swings open
        clear = 0
        for chunk in range(12):
            self._hold("w", 0.3)
            self.driver.wait(0.1)
            clear = clear + 1 if self.prompt_reader.read(self.screen.capture()) is None else 0
            logger.info("door: step %d through, prompt %s", chunk + 1, "gone" if clear else "still there")
            if clear >= 1 and chunk >= 1:  # through: not further than the author walked, the porch is short
                return
        raise VisualRouteError("Застрял в дверном проёме: подсказка двери не пропала.")

    def _scan_for_prompt(self, expected: str, reads: int = 14) -> bool:
        """The door is somewhere around us: turn on the spot, a little wider each time, reading the prompt.
        The prompt appears only when the crosshair is on the door, so it is a landmark by itself."""
        for index in range(reads):
            if self._try_interact(expected):
                return True
            self._look(14.0 * (index + 1) * (1 if index % 2 == 0 else -1), 0.0)
            self.driver.wait(0.3)
        return self._try_interact(expected)

    def run_taught(self, runs: list, stage: int | None = None) -> None:
        """Teach and repeat. Every look: where am I (key frames), where is the next target on the screen, turn to
        it, do the step. The demonstrator's camera movements are never replayed.

        Right after a door the place is known without any picture: the door prompt was just seen. From that pose
        the plan continues (turn to the next heading, walk) until the key frames are recognised again."""
        from .taught import Localizer, TaughtPlan, pixels_to_degrees, recorded_degrees_per_count, wrap_degrees

        self._mark("обучение: подготовка")
        self._show(status="Готовлюсь", mode="search", stage="", frame=[1280, 1024])
        height, width = self.screen.capture().shape[:2]
        # No calibration jerk of the camera: the mouse counts per degree are known from the demonstration, and
        # every look corrects what is left (the heading error shrinks 5-10x per look).
        self.px_per_degree = 1.0 / recorded_degrees_per_count(width, self.HORIZONTAL_FOV)
        localizer = Localizer(runs)
        radar = RadarIndex(runs)  # the game's mini-map as a second way to tell where we are (works in the dark)
        plans = [TaughtPlan(run, width, self.HORIZONTAL_FOV) for run in runs]
        if stage is None and self.use_map_route:
            # The way by coordinates: the mini-map says where we stand and where we look on every frame, the taught
            # runs say where the doors, the yard and the stairs are. Only where the mini-map cannot be used does the
            # older way by recognised pictures (below) take over.
            self._mark("путь по мини-карте")
            if self.run_by_map(runs, plans):
                return
            logger.info("taught: the mini-map way is not available, falling back to the taught pictures")
        self.ensure_map(runs, plans, width)
        last_position: tuple[float, float, float] | None = None  # (x, y, time) of the last accepted fix on the map
        suspect: tuple[float, float] | None = None
        self._mark("обученный путь")
        if stage is None:
            # Started in the middle of the way (after a failure, or already outside): continue from where we are.
            first = localizer.locate(self.screen.capture())
            stage = plans[first.run].stage_of[first.index] if first is not None else 0
            logger.info("taught: starting at stage %d%s", stage, "" if first is not None else " (place not recognised)")
        lost = stalls = aligns = creeps = 0
        pending: tuple[int, float] | None = None  # (run, time in the demo) after the last walk
        carry: dict | None = None                  # remembered pose: {"run", "t", "yaw", "pitch", "actions"}
        last_interact: tuple | None = None         # (run, step) while the next thing to do is a door
        after_open = False                         # a door was just opened and walked through
        stairs_done = False
        settle = 0.3

        def allowed(r: int, i: int) -> bool:
            return plans[r].stage_of[i] == stage and (not after_open or plans[r].after_open_ok(i, stage))

        def finish_interact(run_index: int, step) -> bool:
            """A door action was done. Remember the pose, walk through an opened door. True = the path is over."""
            nonlocal stage, stalls, creeps, carry, last_interact, after_open
            plan = plans[run_index]
            after_open = step.expected == "open"
            stage += 1
            stalls = creeps = 0
            last_interact = None
            self._save_trace(f"дверь_{stage}")
            following = plan.next_step(step.t1 + 0.3)
            if self.hand_over_at_platform and step.expected == "close" and stage >= plan.stages:
                self._take_stairs(plan, step)  # past the last gate: stairs, left turn, and the plan of the room takes over
                return True
            if following is None:  # that was the last thing the demonstration did
                return True
            yaw, pitch, after = plan.yaw_at(step.t0), plan.pitch_at(step.t0), step.t1 + 0.3
            if step.expected == "open":
                self._pass_through_door()
                door = next((s for s in plan.steps if s.kind == "interact" and s.t0 > step.t0), None)
                if door is not None:
                    after = door.t0 - 0.01  # the walk through is done: what is left is turning back to close it
            carry = {"run": run_index, "t": after, "yaw": yaw, "pitch": pitch, "actions": 0}
            logger.info("door done: continuing from the door at demo time %.1f", after)
            return False

        def cruise(run_index: int, step, start_t: float) -> float:
            """Walk one recorded step as ONE continuous movement: the key stays down while the picture is watched
            and the heading is eased towards the recorded one with small turns. Returns the demo time reached."""
            plan = plans[run_index]
            keys = (["shift"] if step.shift else []) + [step.key]
            started = time.monotonic()
            end = started + min(self.CRUISE_MAX, max(0.2, plan.remaining_hold(step, start_t)))
            best_t, progress_at, blind = start_t, started, 0
            previous = None
            still = 0
            self._cruise_blocked = False
            try:
                for name in keys:
                    self.driver.play_route_event(RouteEvent(0.0, "key_down", name))
                while time.monotonic() < end:
                    live = self.screen.capture()
                    small = cv2.resize(cv2.cvtColor(live, cv2.COLOR_RGB2GRAY), (64, 51)).astype(np.float32)
                    if previous is not None and time.monotonic() - started > 0.6:
                        still = still + 1 if float(np.abs(small - previous).mean()) < 1.2 else 0
                        if still >= 4:
                            self._cruise_blocked = True  # W is down but the picture does not move: a fence, a wall
                            break
                    previous = small
                    hit = localizer.locate(live, allowed=allowed)
                    now = time.monotonic()
                    turn = None  # how far the camera is turned from the recorded heading of the key frame, degrees
                    if hit is not None and hit.run == run_index:
                        key_index, turn = hit.index, pixels_to_degrees(hit.shift_x, width, self.HORIZONTAL_FOV)
                    elif radar.usable:
                        near = radar.locate(live, allowed=allowed)
                        if near is not None and near.run == run_index:
                            key_index, turn = near.index, near.rotation
                    if turn is None:
                        blind += 1
                        if blind >= 6:
                            break
                        self.driver.wait(0.03)
                        continue
                    blind = 0
                    t_hit = plan.key_times[key_index]
                    if t_hit > best_t + 0.05:
                        best_t, progress_at = t_hit, now
                    elif now - progress_at > 1.5:
                        break  # not getting anywhere: the main loop knows how to deal with that
                    if t_hit >= step.t1 - 0.15:
                        break  # reached the place where this step ended in the demonstration
                    end = min(started + self.CRUISE_MAX, now + plan.remaining_hold(step, t_hit) + 0.05)
                    heading = plan.yaw_at(min(max(t_hit + 0.5, step.t0), step.t1)) - (plan.yaw_at(t_hit) - turn)
                    heading = wrap_degrees(heading)
                    if abs(heading) > 2.0:
                        self._look(max(-8.0, min(8.0, heading * 0.7)), 0.0)  # keep walking while turning
                    else:
                        self.driver.wait(0.02)
            finally:
                for name in reversed(keys):
                    self.driver.play_route_event(RouteEvent(0.0, "key_up", name))
            return best_t

        for loop in range(500):
            self.driver.wait(settle)  # let the picture settle after the last move
            settle = 0.3
            frame = self.screen.capture()
            if self.hand_over_at_platform and stage >= plans[0].stages and zone_for(stage, frame) == "platform":
                # the way ends here: the plan of the room takes over from the end of the stairs
                self.at_platform_entry = True
                self._set_stage("конец лестницы: дальше по плану")
                self._show(status="Лестница пройдена", mode="ok", frame=[width, height], stage="платформа со столами")
                return
            if self.hand_over_at_platform and stage >= plans[0].stages and not stairs_done                     and zone_for(stage, frame) == "stairs":
                # Started on the stairs (not coming from the gate, where `_take_stairs` does it): facing down them. Walk to
                # the end, turn left, and the plan of the room takes over.
                stairs_done = True
                self._show(status="Спускаюсь по лестнице", mode="ok", frame=[width, height], stage="лестница")
                self._walk_straight(5.6 / self.metres_per_second)
                self._gyro_turn(-90.0)
                self.at_platform_entry = self.stairs_taken = True
                self._set_stage("конец лестницы: дальше по плану")
                return
            found = localizer.locate(frame, allowed=allowed)
            fix = None
            if found is None and radar.usable:
                fix = radar.locate(frame, allowed=allowed)
            here = None
            if self.route_map is not None and (found is not None or fix is not None):
                here = self.route_map.position(found.run if found is not None else fix.run,
                                               found.index if found is not None else fix.index)
            if here is not None:
                # The map knows how far we can have got since the last fix: a "place" far from that is a wrong match.
                moment = time.monotonic()
                far = last_position is not None and \
                    math.hypot(here[0] - last_position[0], here[1] - last_position[1]) > 3.0 + 3.0 * (moment - last_position[2])
                if far:
                    if suspect is not None and math.hypot(here[0] - suspect[0], here[1] - suspect[1]) < 1.5:
                        last_position, suspect = (here[0], here[1], moment), None  # twice the same new place: believe it
                    else:
                        suspect = here
                        logger.info("taught loop %d: a fix far from where we were (map says %.1f,%.1f), not trusted yet", loop, *here)
                        found = fix = None
                else:
                    last_position, suspect = (here[0], here[1], moment), None
            if found is None and fix is None and (carry is None or carry["actions"] >= self.MAX_CARRY_ACTIONS):
                carry = None
                lost += 1
                logger.info("taught loop %d: place not recognised (%d), stage %d", loop, lost, stage)
                if lost > 36:
                    raise VisualRouteError("Потерялся: не узнаю место ни на одном кадре обучения.")
                self._show(status="Ищу, где я · осматриваюсь", mode="lost", frame=[width, height],
                           stage=f"{stage + 1}/{plans[0].stages + 1}")
                if lost >= 3 and last_interact is not None:
                    # the next thing is a door: its prompt only shows when we look at it, so look around for it
                    run_index, door = last_interact
                    self._show(status="Ищу подсказку двери", mode="search", frame=[width, height],
                               stage=f"{stage + 1}/{plans[0].stages + 1}")
                    if self._scan_for_prompt(door.expected, reads=10):
                        lost = 0
                        if finish_interact(run_index, door):
                            self._set_stage("конец записанного пути")
                            return
                        continue
                if lost % 12 == 0:
                    self._hold("s", 0.4)  # a different spot may be recognisable
                # look a little to one side and the other, wider each time: the heading stays known, it never
                # drifts round like a sweep in one direction would
                def offset(k: int) -> float:
                    return 0.0 if k <= 0 else min(120.0, 20.0 * ((k + 1) // 2)) * (1 if k % 2 else -1)
                self._look(offset(lost) - offset(lost - 1), 0.0)
                continue
            lost = 0
            if found is not None:
                carry = None  # the picture knows better than the remembered pose
                run_index, inliers, points, kidx = found.run, found.inliers, found.points, found.index
                plan = plans[run_index]
                t_now = plan.key_times[found.index]
                yaw_now = plan.yaw_at(t_now) - pixels_to_degrees(found.shift_x, width, self.HORIZONTAL_FOV)
                pitch_now = plan.pitch_at(t_now) - pixels_to_degrees(found.shift_y, width, self.HORIZONTAL_FOV)
            elif fix is not None:
                carry = None
                run_index, inliers, points, kidx = fix.run, fix.inliers, (), fix.index
                plan = plans[run_index]
                t_now = plan.key_times[fix.index]
                yaw_now = plan.yaw_at(t_now) - fix.rotation  # the map turns the opposite way to the camera
                pitch_now = plan.pitch_at(t_now)
            else:
                run_index, inliers, points, kidx = carry["run"], 0, (), None
                plan = plans[run_index]
                t_now, yaw_now, pitch_now = carry["t"], carry["yaw"], carry["pitch"]
            step = plan.next_step(t_now)
            if step is None:
                self._set_stage("конец записанного пути")
                self._show(status="Путь пройден", mode="ok", frame=[width, height], stage=f"{stage + 1}/{plan.stages + 1}")
                return
            last_interact = (run_index, step) if step.kind == "interact" else None
            ahead = min(max(t_now + 0.5, step.t0), step.t1) if step.kind == "move" else step.t0
            err_yaw = wrap_degrees(plan.yaw_at(ahead) - yaw_now)
            err_pitch = plan.pitch_at(ahead) - pitch_now

            # where the target of this step is on the screen right now (the door; the end of a walk)
            last_step = plan.next_step(step.t1 + 0.3) is None
            if step.kind == "interact":
                name = "Дверь · открыть" if step.expected == "open" else "Дверь · закрыть" if step.expected == "close" else "Дверь"
            else:
                name = "Столы" if last_step else "Следующая точка"
            target_index = plan.target_index(step, stage)
            proj = localizer.project(run_index, target_index) if found is not None and target_index is not None else None
            if proj is not None and step.kind == "interact" and 0.55 <= proj.scale <= 1.8:
                # the door itself is in view: aim straight at it, it is more exact than the recorded heading
                err_yaw = pixels_to_degrees(proj.center[0] - 0.5 * width, width, self.HORIZONTAL_FOV)
                err_pitch = pixels_to_degrees(proj.center[1] - 0.46 * height, width, self.HORIZONTAL_FOV)
            note = "вижу"
            if proj is not None:
                note = "вижу · далеко" if proj.scale < 0.7 else "вижу · ближе" if proj.scale < 0.88 else "вижу · рядом"
            view = {
                "frame": [width, height], "stage": f"{ZONE_TITLES[zone_for(stage, frame)]} · {stage + 1}/{plan.stages + 1}",
                "turn": round(err_yaw, 1),
                "mode": "ok" if proj is not None else "search",
                "place": {"label": f"Узнаю место · {inliers}" if found is not None
                          else f"Радар · {inliers}" if fix is not None else "Иду от двери по памяти",
                          "points": [[round(x), round(y)] for x, y in points]},
                "target": {"label": name, "note": note, "quad": [[round(x), round(y)] for x, y in proj.quad] if proj else None},
            }
            if self.route_map is not None and kidx is not None:
                # past the last door (the red gates closed behind us) the radar zooms in on the plantation
                view["radar"] = self._radar(self.route_map.pose(run_index, kidx, yaw_now), None, stage >= plan.stages)
            logger.info("taught loop %d: zone %s, %s t=%.1f stage %d, next %s %s, turn %.0f deg, tilt %.0f deg, target %s",
                        loop, zone_for(stage, frame), f"run {run_index} frame {found.index} ({inliers} matches)" if found is not None
                        else f"radar run {run_index} frame {fix.index} ({inliers} inliers, {fix.rotation:.0f} deg)" if fix is not None
                        else f"from the door ({carry['actions']} steps)", t_now, stage, step.kind, step.key or step.expected,
                        err_yaw, err_pitch, "scale %.2f" % proj.scale if proj else "not in view")
            if pending is not None and (found is not None or fix is not None) and pending[0] == run_index:
                stalls = 0 if t_now > pending[1] + 0.05 else stalls + 1
            pending = None
            if abs(err_yaw) > self.ALIGN_YAW or abs(err_pitch) > self.ALIGN_PITCH:
                if aligns < 6:
                    aligns += 1
                    side = "вправо" if err_yaw > 0 else "влево"
                    turn_yaw = err_yaw if abs(err_yaw) > self.ALIGN_YAW else 0.0
                    turn_pitch = err_pitch if abs(err_pitch) > self.ALIGN_PITCH else 0.0
                    self._show(status=f"Поворачиваю {abs(err_yaw):.0f}° {side}", **view)
                    self._look(turn_yaw, turn_pitch)
                    if carry is not None:  # no picture to check against: count the turn as made
                        carry["yaw"] += max(-90.0, min(90.0, turn_yaw)) * self.TURN_GAIN
                        carry["pitch"] += max(-12.0, min(12.0, turn_pitch)) * self.TURN_GAIN
                    continue
                if abs(err_yaw) > 20:
                    raise VisualRouteError("Не получается развернуться в нужную сторону. Остановился.")
            aligns = 0
            if stalls >= 12:
                raise VisualRouteError("Застрял: путь не продвигается. Остановился.")
            if stalls >= 5:
                logger.info("taught loop %d: stalled %d, stepping back", loop, stalls)
                self._show(status="Застрял · отхожу назад", **view)
                self._hold("s", 0.35)
                self._look(6.0 if stalls % 2 else -6.0, 0.0)
            if step.kind == "interact":
                near = proj is not None and proj.scale >= 0.88
                self._show(status="Проверяю подсказку двери" if near or proj is None else "Подхожу к двери", **view)
                done = self._try_interact(step.expected)
                if not done and stage < 4:
                    self._show(status="Подхожу к двери: ищу её глазами", **view)
                    done = self._approach_door(step.expected, ("house", "porch")[stage] if stage < 2 else "container")
                if not done and carry is not None:
                    # we stand right at this door but the aim is a little off: look around for its prompt
                    self._show(status="Ищу подсказку двери", **view)
                    done = self._scan_for_prompt(step.expected)
                if not done:
                    if creeps < self.MAX_CREEPS and not (near and creeps >= 4):
                        creeps += 1  # the door is farther than where it was recorded: a short step closer
                        logger.info("taught loop %d: no prompt yet, stepping closer (%d)", loop, creeps)
                        self._show(status="Подхожу к двери", **view)
                        self._hold("w", 0.3)
                        continue
                    self._recorded_interact(step.expected)  # close enough: look around a little more, raises if still nothing
                if finish_interact(run_index, step):
                    self._set_stage("конец записанного пути")
                    self._show(status="Путь пройден", mode="ok", frame=[width, height], stage=f"{stage + 1}/{plan.stages + 1}")
                    return
                continue
            if found is None and fix is None and stage >= plan.stages and zone_for(stage, frame) == "stairs":
                # past the last door, in the dark of the container and the key frames are not recognised: fall back to
                # the straight-line rule (recorded runs that reach the platform are followed instead, see above)
                self._show(status="Спускаюсь по лестнице", **view)
                self._descend_stairs()
                self._set_stage("конец записанного пути")
                return
            self._show(status="Иду к столам" if last_step else "Иду вперёд", **view)
            if (found is not None or fix is not None) and plan.remaining_hold(step, t_now) >= self.CRUISE_MIN_HOLD:
                reached = cruise(run_index, step, t_now)
                settle = 0.1
                if self._cruise_blocked:
                    logger.info("taught loop %d: walked into something, stepping back", loop)
                    self._hold("s", 0.3)
                    stalls += 3
                pending = (run_index, max(t_now, reached) if reached > t_now else t_now)
            else:
                seconds = max(0.12, min(self.CHUNK, plan.remaining_hold(step, t_now)))
                self._hold(step.key, seconds, step.shift)
                settle = 0.12  # walking does not blur the picture like a camera turn
                if carry is not None:
                    carry["actions"] += 1
                    carry["t"] = step.t1  # that step is behind us now
                else:
                    pending = (run_index, t_now)
        raise VisualRouteError("Слишком много шагов: путь не завершён.")

    def _set_stage(self, name: str) -> None:
        self._mark(name)

    def run_recorded(self, route) -> None:
        """Replay a recorded path. E is pressed only while the screen shows the prompt that was
        visible when it was recorded; everything else is the recording."""
        events = sorted(route.events, key=lambda event: event.elapsed_offset)
        started = time.monotonic()
        lag = 0.0
        self._mark("записанный путь")
        for event in events:
            while time.monotonic() < started + event.elapsed_offset + lag:
                self.driver.wait(min(0.1, max(0.0, started + event.elapsed_offset + lag - time.monotonic())))
            if event.kind == "interact":
                t0 = time.monotonic()
                self._recorded_interact(str(event.value or ""))
                lag += time.monotonic() - t0
            else:
                self.driver.play_route_event(event)
        end = started + route.duration_seconds + lag
        while time.monotonic() < end:
            self.driver.wait(min(0.1, max(0.0, end - time.monotonic())))
        if hasattr(self.driver, "release_all"):
            self.driver.release_all()

    def _recorded_interact(self, expected: str) -> None:
        """Press E for a recorded door action, but only when the game offers that action."""
        if expected not in ("open", "close"):
            self.driver.play_key("e", 0.12)  # nothing was recognised while recording: trust the recording
            self.driver.wait(0.2)
            return
        wobble = [0, 3, -6, 9, -12, 15, -18, 21]  # small camera searches around the recorded view, degrees
        for index, step in enumerate(wobble):
            if step:
                self.driver.move_relative(self.degrees(step), 0)
                self.driver.wait(0.15)
            observed = self.prompt_reader.read(self.screen.capture())
            logger.info("recorded E #%d: expected %s, screen %s", index, expected, observed)
            if observed == expected:
                self.driver.play_key("e", 0.12)
                self.driver.wait(0.25)
                return
            if observed is not None:
                # The opposite action is offered: the door is already in the wanted state.
                logger.info("door already %s, E skipped", "open" if observed == "close" else "closed")
                return
        raise VisualRouteError(f"Не вижу подсказку двери «{expected}»: остановился, чтобы не нажать E вслепую.")

    def _walk(self, pulses: int) -> None:
        for _ in range(pulses):
            self.driver.play_key("w", 0.18)

    def run_route(self) -> None:
        """Spawn in the house -> door -> porch -> container -> down the stairs to the grow room.

        Order and wording follow the user's nine screenshots: from the spawn the door is NOT visible,
        so first walk a couple of metres past the whiteboard and turn right.
        """
        self._mark("калибровка камеры")
        self.calibrate_turn()

        self._mark("выход из спавна")
        self._walk(self.SPAWN_FORWARD_PULSES)  # screenshot 1 -> 2: a couple of metres forward
        self.turn(90)                          # then right, the door is on that wall

        self._mark("дверь дома")
        self.seek(("home_door", "home_door_close"), "open", max_steps=170)
        self.interact("open")

        self._mark("выход из дома")
        self._walk(5)  # across the threshold; the outside view of the door confirms the arrival
        self.seek("home_exit_door", "close", max_steps=60)
        self.interact("close")

        self._mark("ступеньки крыльца")
        # screenshot 5: small steps straight ahead from the porch, then turn right
        self.seek("porch_stairs", None, min_height_ratio=0.40, max_steps=50, scan_direction=-1)
        self._walk(6)
        self.turn(90)

        self._mark("контейнер")
        # far view (screenshot 4) turns into the near view (screenshot 5) on the way: use both
        self.seek(("container_far", "container_near"), None, min_height_ratio=0.16, max_steps=120)
        self._mark("дверь контейнера")
        self.seek("container_door", "open", max_steps=95)
        self.interact("open")
        self._walk(5)
        self._turn_until_prompt("close")
        self.interact("close")

        self._mark("спуск к столам")
        # screenshot 8: the stairs are behind you once the door is closed. Turn around and go down.
        self.turn(180)
        self._walk_until("grow_room", steps=60)

    SPAWN_FORWARD_PULSES = 7
