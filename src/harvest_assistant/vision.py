"""Calibrated screen-template capture and matching."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .models import MatchResult, ScreenRegion


class VisionService:
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self.template_dir.mkdir(parents=True, exist_ok=True)

    def match_template(
        self,
        frame: np.ndarray,
        template_name: str,
        region: ScreenRegion,
        threshold: float,
    ) -> MatchResult:
        template_path = self._template_path(template_name)
        template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            return MatchResult(found=False, score=0.0)

        left, top, width, height = self._validated_region(region, frame.shape[1], frame.shape[0])
        search_area = frame[top : top + height, left : left + width]
        gray_area = cv2.cvtColor(search_area, cv2.COLOR_RGB2GRAY)
        template_h, template_w = template.shape[:2]
        if template_h > gray_area.shape[0] or template_w > gray_area.shape[1]:
            return MatchResult(found=False, score=0.0)

        matches = cv2.matchTemplate(gray_area, template, cv2.TM_CCOEFF_NORMED)
        _, max_score, _, max_location = cv2.minMaxLoc(matches)
        score = float(max_score) if np.isfinite(max_score) else 0.0
        match_rect = (
            left + int(max_location[0]),
            top + int(max_location[1]),
            int(template_w),
            int(template_h),
        )
        return MatchResult(found=score >= threshold, score=score, rect=match_rect)

    def save_template(
        self,
        frame: np.ndarray,
        region: ScreenRegion,
        template_name: str,
    ) -> Path:
        left, top, width, height = self._validated_region(region, frame.shape[1], frame.shape[0])
        if width < 2 or height < 2:
            raise ValueError("Template region must be at least 2 by 2 pixels")
        crop = frame[top : top + height, left : left + width]
        path = self._template_path(template_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(crop.astype(np.uint8)).save(path)
        return path

    def has_template(self, template_name: str) -> bool:
        return self._template_path(template_name).is_file()

    def load_rgb_image(self, image_name: str) -> np.ndarray:
        path = self._template_path(image_name)
        image = Image.open(path).convert("RGB")
        return np.asarray(image)

    def _template_path(self, template_name: str) -> Path:
        name = Path(template_name).name
        if not name:
            raise ValueError("template_name must not be empty")
        if not name.lower().endswith(".png"):
            name += ".png"
        return self.template_dir / name

    @staticmethod
    def _validated_region(region: ScreenRegion, frame_width: int, frame_height: int) -> ScreenRegion:
        left, top, width, height = (int(part) for part in region)
        if left < 0 or top < 0 or width <= 0 or height <= 0:
            raise ValueError("Screen region must have a non-negative origin and positive size")
        if left + width > frame_width or top + height > frame_height:
            raise ValueError("Screen region extends beyond the captured frame")
        return left, top, width, height
