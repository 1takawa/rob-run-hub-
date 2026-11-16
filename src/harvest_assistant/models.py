"""Data models and timer rules for the harvest assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import ceil
from typing import Literal

ClockMode = Literal["wall_clock", "game_open"]
EventKind = Literal["key_down", "key_up", "mouse_delta", "delay", "interact"]
InteractionMode = Literal["tap", "hold"]
AutomationMode = Literal["plantation", "laboratory", "both"]
AUTOMATION_MODES: tuple[tuple[AutomationMode, str], ...] = (
    ("plantation", "Только плантация"),
    ("laboratory", "Только лаборатория"),
    ("both", "Плантация и лаборатория"),
)
ScreenRegion = tuple[int, int, int, int]

DEFAULT_INTERVAL_SECONDS = 60 * 60  # measured: a table without upgrades takes exactly one hour

# Table upgrades: key -> (name, quality, share of growth time they cut). Bonuses add up.
MODIFIERS: dict[str, tuple[str, str, float]] = {
    "fan": ("Вентилятор", "green", 0.12),
    "pump": ("Водяной насос", "purple", 0.14),
    "lamp": ("Ультрафиолетовая лампа", "legendary", 0.24),
}


def modifier_factor(modifiers: list[str]) -> float:
    """Share of the base growth time that is left with these upgrades installed."""
    return max(0.1, 1.0 - sum(MODIFIERS[key][2] for key in modifiers if key in MODIFIERS))


def effective_interval(table: "TableProfile") -> int:
    """Growth time of one cycle: the 40-minute base reduced by the installed upgrades."""
    return max(60, round(table.interval_seconds * modifier_factor(table.modifiers)))


class RunState(str, Enum):
    STOPPED = "stopped"
    WAITING = "waiting"
    WAIT_ROUTE = "wait_route"
    ROUTING = "routing"
    COLLECTING = "collecting"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(slots=True)
class TableProfile:
    table_id: str
    name: str
    route_index: int
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    due_at_utc: float | None = None
    remaining_game_seconds: float | None = None
    templates: dict[str, str] = field(default_factory=dict)
    screen_regions: dict[str, ScreenRegion] = field(default_factory=dict)
    modifiers: list[str] = field(default_factory=list)  # installed upgrades, keys of MODIFIERS

    def __post_init__(self) -> None:
        if not self.table_id.strip():
            raise ValueError("table_id must not be empty")
        if not self.name.strip():
            self.name = self.table_id
        if self.route_index < 0:
            raise ValueError("route_index must be zero or greater")
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        if self.remaining_game_seconds is not None and self.remaining_game_seconds < 0:
            self.remaining_game_seconds = 0.0


MAX_TABLES = 6  # the room has six places for tables; fewer are planted at first, more are planted later


@dataclass(slots=True)
class RouteEvent:
    elapsed_offset: float
    kind: EventKind
    value: str | tuple[int, int] | float | None = None


@dataclass(slots=True)
class Waypoint:
    table_id: str
    event_offset: float
    anchor_template: str
    ready_icon_template: str
    growing_icon_template: str
    screen_region: ScreenRegion
    capture_template: str = ""


@dataclass(slots=True)
class RecordedRoute:
    events: list[RouteEvent] = field(default_factory=list)
    waypoints: list[Waypoint] = field(default_factory=list)
    start_anchor_template: str = ""
    end_anchor_template: str = ""
    duration_seconds: float = 0.0
    start_screen_region: ScreenRegion | None = None
    end_screen_region: ScreenRegion | None = None
    gameplay_guard_template: str = ""
    gameplay_guard_region: ScreenRegion | None = None
    start_capture_template: str = ""
    end_capture_template: str = ""
    lab_offset: float | None = None


@dataclass(slots=True)
class ScreenMarker:
    template: str = ""
    region: ScreenRegion | None = None


@dataclass(slots=True)
class LaboratoryProfile:
    timer: TableProfile = field(default_factory=lambda: TableProfile("lab", "Лаборатория", 0))
    prepare: str = ""
    actions: str = ""
    finish: str = ""
    station: ScreenMarker = field(default_factory=ScreenMarker)
    ready: ScreenMarker = field(default_factory=ScreenMarker)
    running: ScreenMarker = field(default_factory=ScreenMarker)


@dataclass(slots=True)
class WaypointCalibration:
    table_id: str
    anchor_template: str = ""
    ready_icon_template: str = ""
    growing_icon_template: str = ""
    harvest_prompt_template: str = ""
    progress_template: str = ""
    anchor_region: ScreenRegion | None = None
    icon_region: ScreenRegion | None = None
    prompt_region: ScreenRegion | None = None
    progress_region: ScreenRegion | None = None


@dataclass(slots=True)
class InteractionCalibration:
    key: str = "e"
    mode: InteractionMode = "tap"
    hold_seconds: float = 0.12
    progress_timeout_seconds: float = 45.0
    harvest_seconds: float = 6.0  # max wait for the pick-up list after pressing the key
    prompt_threshold: float = 0.88
    icon_threshold: float = 0.88
    anchor_threshold: float = 0.82
    progress_threshold: float = 0.82


@dataclass(slots=True)
class TableStatus:
    table_id: str
    name: str
    remaining_seconds: int | None
    state: str
    calibrated: bool
    route_index: int


@dataclass(slots=True)
class RunSnapshot:
    state: RunState = RunState.STOPPED
    current_table_id: str | None = None
    table_statuses: list[TableStatus] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class MatchResult:
    found: bool
    score: float
    rect: ScreenRegion | None = None


@dataclass(slots=True)
class Profile:
    name: str = "My room"
    target_window_title_match: str = ""
    background_control: bool = False
    clock_mode: ClockMode = "wall_clock"
    manual_timer_paused: bool = False
    manual_timer_pause_started_utc: float | None = None
    last_clock_update_utc: float | None = None
    tables: list[TableProfile] = field(default_factory=list)
    route: RecordedRoute = field(default_factory=RecordedRoute)
    automation_mode: AutomationMode = "plantation"
    waiting_route: RecordedRoute = field(default_factory=RecordedRoute)
    start_marker: ScreenMarker = field(default_factory=ScreenMarker)
    laboratory: LaboratoryProfile = field(default_factory=LaboratoryProfile)
    waypoint_calibrations: dict[str, WaypointCalibration] = field(default_factory=dict)
    interaction: InteractionCalibration = field(default_factory=InteractionCalibration)
    record_route_hotkey: str = "f5"
    mark_waypoint_hotkey: str = "f6"
    pause_hotkey: str = "f8"
    stop_hotkey: str = "f9"
    loot_hotkey: str = "f7"
    auto_hotkey: str = "f4"
    visual_hotkey: str = "f3"  # start / stop the way to the grow room
    approach_hotkey: str = "f2"  # record the way to the grow room
    autocop_hotkey: str = "f10"  # start / stop police-service signup
    autocop_class: str = "Патрульный"
    approach_route: RecordedRoute = field(default_factory=RecordedRoute)  # spawn -> doors -> tables, doors verified by prompt
    interval_migrated: bool = False  # one-time switch of the old 40-minute default to the real hour
    auto_reconnect: bool = False  # after being thrown into the lobby, click back into the game
    table_vision: bool = True  # overlay marks the tables of the plantation that are still growing
    vision_harvest: bool = True  # harvest by sight (taught way + table badges); False = the old recorded macro
    auto_collect: bool = False  # True: every expired timer starts a new cycle by itself
    loot_totals: dict[str, int] = field(default_factory=dict)  # "цвет|название" -> штук
    last_loot: list[dict] = field(default_factory=list)  # last harvest rows: name, count, color, table
    harvest_count: int = 0
    match_threshold: float = 0.84

    def __post_init__(self) -> None:
        if self.clock_mode not in ("wall_clock", "game_open"):
            raise ValueError(f"Unsupported clock mode: {self.clock_mode}")
        if not self.name.strip():
            self.name = "My room"

    @property
    def runs_plantation(self) -> bool:
        return self.automation_mode in ("plantation", "both")

    @property
    def runs_laboratory(self) -> bool:
        return self.automation_mode in ("laboratory", "both")

    def production_timers(self) -> list[TableProfile]:
        return [*self.tables, self.laboratory.timer]


def remaining_seconds(table: TableProfile, profile: Profile, now_utc: float) -> int | None:
    """Return the displayed time remaining, frozen when the user paused timers."""
    if profile.clock_mode == "wall_clock":
        if table.due_at_utc is None:
            return None
        effective_now = now_utc
        if profile.manual_timer_paused and profile.manual_timer_pause_started_utc is not None:
            effective_now = profile.manual_timer_pause_started_utc
        return max(0, ceil(table.due_at_utc - effective_now))
    if table.remaining_game_seconds is None:
        return None
    return max(0, ceil(table.remaining_game_seconds))


def advance_game_clock(profile: Profile, game_running: bool, now_utc: float) -> None:
    """Advance game-open timers only while the client is open and timers are unpaused."""
    previous = profile.last_clock_update_utc
    if previous is None:
        profile.last_clock_update_utc = now_utc
        return

    elapsed = max(0.0, now_utc - previous)
    if profile.clock_mode == "game_open" and game_running and not profile.manual_timer_paused:
        for table in profile.production_timers():
            if table.remaining_game_seconds is not None:
                table.remaining_game_seconds = max(0.0, table.remaining_game_seconds - elapsed)
    profile.last_clock_update_utc = now_utc


def set_timer_paused(profile: Profile, paused: bool, now_utc: float) -> None:
    """Pause/resume growth countdowns without stopping route automation."""
    if paused == profile.manual_timer_paused:
        return
    if paused:
        profile.manual_timer_paused = True
        profile.manual_timer_pause_started_utc = now_utc
    else:
        pause_started = profile.manual_timer_pause_started_utc
        if profile.clock_mode == "wall_clock" and pause_started is not None:
            paused_for = max(0.0, now_utc - pause_started)
            for table in profile.production_timers():
                if table.due_at_utc is not None:
                    table.due_at_utc += paused_for
        profile.manual_timer_paused = False
        profile.manual_timer_pause_started_utc = None
    profile.last_clock_update_utc = now_utc


def start_table_timer(table: TableProfile, profile: Profile, seconds: int, now_utc: float) -> None:
    """Set the initial remaining growth time for one table."""
    seconds = max(0, int(seconds))
    if profile.clock_mode == "wall_clock":
        effective_now = now_utc
        if profile.manual_timer_paused and profile.manual_timer_pause_started_utc is not None:
            effective_now = profile.manual_timer_pause_started_utc
        table.due_at_utc = effective_now + seconds
        table.remaining_game_seconds = None
    else:
        table.due_at_utc = None
        table.remaining_game_seconds = float(seconds)
    profile.last_clock_update_utc = now_utc


def reset_table_timer(table: TableProfile, profile: Profile, now_utc: float) -> None:
    """Start the next automatic growth cycle after a visually confirmed harvest."""
    start_table_timer(table, profile, effective_interval(table), now_utc)


def set_table_modifiers(table: TableProfile, profile: Profile, modifiers: list[str], now_utc: float) -> None:
    """Install/remove upgrades. A running timer speeds up or slows down in the same proportion."""
    modifiers = [key for key in MODIFIERS if key in modifiers]
    remaining = remaining_seconds(table, profile, now_utc)
    old_factor = modifier_factor(table.modifiers)
    table.modifiers = modifiers
    if remaining:
        start_table_timer(table, profile, round(remaining * modifier_factor(modifiers) / old_factor), now_utc)
