"""Parse the input steps configured for a laboratory visit."""

from __future__ import annotations

from math import isfinite

from .models import LaboratoryProfile, ScreenMarker


def parse_steps(source: str) -> list[tuple[str, tuple]]:
    steps: list[tuple[str, tuple]] = []
    total_seconds = 0.0
    for number, line in enumerate(source.splitlines(), 1):
        parts = line.partition("#")[0].lower().split()
        if not parts:
            continue
        command, *args = parts
        try:
            if command == "key" and len(args) in (1, 2):
                key = args[0]
                if key not in "abcdefghijklmnopqrstuvwxyz0123456789" or len(key) != 1:
                    if key not in ("esc", "tab", "enter", "space", "shift", "ctrl", "alt", "left", "right", "up", "down"):
                        raise ValueError
                duration = float(args[1]) if len(args) == 2 else 0.12
                if not isfinite(duration) or not 0.05 <= duration <= 10:
                    raise ValueError
                step = (key, duration)
                total_seconds += duration
            elif command in ("click", "rightclick") and len(args) == 2:
                step = tuple(int(value) for value in args)
                if min(step) < 0:
                    raise ValueError
            elif command == "drag" and len(args) == 4:
                step = tuple(int(value) for value in args)
                if min(step) < 0:
                    raise ValueError
                total_seconds += 0.45
            elif command == "wait" and len(args) == 1:
                duration = float(args[0])
                if not isfinite(duration) or not 0 <= duration <= 60:
                    raise ValueError
                step = (duration,)
                total_seconds += duration
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"Строка {number}: проверь команду «{line.strip()}».") from exc
        steps.append((command, step))
    if len(steps) > 100 or total_seconds > 180:
        raise ValueError("На один этап допускается до 100 действий и 180 секунд.")
    return steps


def marker_available(marker: ScreenMarker, vision) -> bool:
    return bool(marker.template and marker.region and vision.has_template(marker.template))


def laboratory_errors(lab: LaboratoryProfile, vision) -> list[str]:
    errors: list[str] = []
    for title, source in (("Открыть", lab.prepare), ("Сбор и запуск", lab.actions), ("Закрыть", lab.finish)):
        try:
            steps = parse_steps(source)
            if title == "Сбор и запуск" and not any(command != "wait" for command, _ in steps):
                errors.append("добавь действия «Сбор и запуск» на вкладке «Лаборатория»")
        except ValueError as exc:
            errors.append(f"лаборатория, «{title}»: {exc}")
    if not marker_available(lab.ready, vision) or not marker_available(lab.running, vision):
        errors.append("сохрани признаки «Готово» и «Партия запущена» в лаборатории")
    if lab.prepare.strip() and not marker_available(lab.station, vision):
        errors.append("сохрани ориентир у закрытой лаборатории для шага «Открыть»")
    return errors
