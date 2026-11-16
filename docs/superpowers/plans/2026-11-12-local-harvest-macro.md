# Local Harvest Macro Implementation Plan

> **For agentic workers:** Implement this plan task by task. Keep each component within its listed files and preserve the fail-closed behavior in the spec.

**Goal:** Build a Windows companion that tracks multiple 40-minute grow-table cycles and safely runs a calibrated keyboard/mouse route to collect ready harvests.

**Architecture:** A Tkinter desktop UI edits a local profile and displays persisted table deadlines. A route runner composes screen checks, recorded movement/camera input, and harvest interaction; it may send input only while the configured game window is foreground and a calibrated screen checkpoint matches.

**Tech Stack:** Python 3.12, Tkinter, PyAutoGUI, OpenCV, PyDirectInput, Pynput, JSON profiles.

**Spec:** `docs/superpowers/specs/2026-11-12-local-harvest-macro-design.md`

## Global Constraints

- Target a Windows desktop game client; Python 3.12 is available.
- The user has permission to automate this local project. The game is a standalone full-scale title, not Roblox; its source is unavailable.
- Harvest interval defaults to 40 minutes and can be adjusted per profile.
- Initial timer values are user-set because the companion cannot see when a crop was planted.
- At each recorded table waypoint, verify a saved screen anchor and the over-table status icon before interacting.
- Require a gameplay-only HUD marker and recheck it during movement so menus/loading screens stop playback.
- Reset a table timer only after verifying its ready-to-growing icon transition.
- Pause when the game loses focus, a screen anchor or prompt is missing, or collection does not complete; do not retry blindly.
- Route recording includes walking and camera turns and begins and ends at the same calibrated room landmark.

## Review Focus

- **Focus loss during a route:** input must stop before another key or mouse event; belongs to the input driver and route runner.
- **Mouse input cannot be recorded in captured-camera mode:** calibration must detect an empty/invalid mouse trace and offer manual relative-turn controls; belongs to route recorder and calibration UI.
- **A repeated table prompt appears at the wrong waypoint after route drift:** require per-waypoint anchor and ready/growing icon match before collection; belongs to vision and route runner.
- **Harvest progress disappears without a successful harvest:** reset the timer only after the ready icon changes to the growing clock; belongs to harvest interaction state machine.
- **App restarts during a cycle:** persist due-at timestamps and the selected clock mode so countdowns recover correctly; belongs to persistence and timer model.

---

## File Map

- `requirements.txt` — runtime packages.
- `run.bat` — install/run entry point for Windows.
- `README.md` — setup, calibration, route recording, and safety controls.
- `src/harvest_assistant/models.py` — profile, table, route event, and run-state data types.
- `src/harvest_assistant/persistence.py` — JSON profile and due-time persistence.
- `src/harvest_assistant/screen.py` — screenshot capture and foreground-window identification.
- `src/harvest_assistant/vision.py` — calibrated template capture and screen matching.
- `src/harvest_assistant/input_driver.py` — guarded game key/mouse input and emergency stop.
- `src/harvest_assistant/route_recorder.py` — timed route event capture, waypoint markers, and manual-turn fallback.
- `src/harvest_assistant/calibration.py` — first-run screen, icon, and interaction calibration flow.
- `src/harvest_assistant/runner.py` — due-table scheduler, route playback, and harvest confirmation state machine.
- `src/harvest_assistant/app.py` — Tkinter window, table list, controls, and status updates.
- `src/harvest_assistant/__main__.py` — application entry point.

## Task 1: Profile and Timer Persistence

**Files:**
- Create: `requirements.txt` (`numpy`, `opencv-python`, `pyautogui`, `pillow`, `pydirectinput`, `pynput`)
- Create: `run.bat` (create/use `.venv`, install requirements, set `PYTHONPATH=src`, launch `harvest_assistant`)
- Create: `src/harvest_assistant/__init__.py`
- Create: `src/harvest_assistant/models.py`
- Create: `src/harvest_assistant/persistence.py`

**Interfaces:**
- `ClockMode`: `wall_clock` or `game_open`.
- `TableProfile`: table ID, display name, route index, interval seconds, optional wall-clock deadline, remaining game-open seconds, and template paths.
- `RouteEvent`: elapsed offset, kind (`key_down`, `key_up`, `mouse_delta`, or `delay`), and a typed key/delta payload.
- `MatchResult`: `found: bool`, `score: float`, and optional matched screen rectangle.
- `TableStatus`: table ID, remaining seconds, current state, and calibration status.
- `Waypoint`: table ID, event offset, screen anchor path, ready/growing icon paths, and screen region.
- `RecordedRoute`: ordered events and waypoints, start/end anchor paths, a gameplay HUD guard, and route duration.
- `WaypointCalibration`: table ID, anchor/icon/prompt/progress template paths, and their screen regions.
- `InteractionCalibration`: key, tap/hold mode, hold duration, progress region, and completion timeout.
- `RunState`: stopped, waiting, routing, collecting, paused, or error.
- `RunSnapshot`: current run state, optional current table ID, `list[TableStatus]`, and user-facing reason.
- `Profile`: profile name, target window title match, clock mode, manual timer-pause state, last clock update timestamp, ordered tables, route events, waypoint metadata, and interaction settings.
- `save_profile(profile: Profile, path: Path) -> None`
- `load_profile(path: Path) -> Profile`
- `remaining_seconds(table: TableProfile, profile: Profile, now_utc: float) -> int | None`
- `advance_game_clock(profile: Profile, game_running: bool, now_utc: float) -> None`
- `reset_table_timer(table: TableProfile, profile: Profile, now_utc: float) -> None`

**Deliverable:** Profiles survive restart with table order, initial due times, route metadata, and the wall-clock/game-open timing choice intact.

- [x] Define the profile, table, route, calibration, and run snapshot dataclasses with the fields listed above.
- [x] Implement wall-clock and game-open countdown updates, the manual timer pause state, and the 40-minute reset operation.
- [x] Implement JSON save/load for profiles, converting paths and timestamps to portable string/number values.
- [x] Add the dependency list and Windows launcher that creates `.venv`, installs requirements, and starts the `src` package.

## Task 2: Screen Capture, Window Focus, and Visual Matching

**Files:**
- Create: `src/harvest_assistant/screen.py`
- Create: `src/harvest_assistant/vision.py`

**Interfaces:**
- `ScreenService(target_title_match: str)`
- `ScreenService.capture() -> numpy.ndarray`
- `ScreenService.foreground_window() -> tuple[int, str]`
- `ScreenService.is_target_foreground() -> bool`
- `VisionService(template_dir: Path)`
- `VisionService.match_template(frame: numpy.ndarray, template_name: str, region: tuple[int, int, int, int], threshold: float) -> MatchResult`
- `VisionService.save_template(frame: numpy.ndarray, region: tuple[int, int, int, int], template_name: str) -> Path`

**Deliverable:** Calibration can save the gameplay HUD marker, route/table anchors, and ready/growing/prompt/progress templates; matching returns an explicit score and never treats a low-confidence match as success.

- [x] Capture the current screen with PyAutoGUI and expose the foreground window handle/title through `ScreenService`.
- [x] Save user-selected screen rectangles as named templates under the profile's template directory.
- [x] Implement region-scoped OpenCV matching that returns `found=False` below the configured threshold.

## Task 3: Guarded Input and Route Recorder

**Files:**
- Create: `src/harvest_assistant/input_driver.py`
- Create: `src/harvest_assistant/route_recorder.py`

**Interfaces:**
- `InputDriver(screen: ScreenService, stop_event: threading.Event)`
- `InputDriver.play_key(key: str, duration: float = 0.0) -> None`
- `InputDriver.move_relative(dx: int, dy: int) -> None`
- `InputDriver.play_route_event(event: RouteEvent) -> None`
- `InputDriver.stop() -> None`
- `RouteRecorder(screen: ScreenService, vision: VisionService, profile: Profile)`
- `RouteRecorder.start(self) -> None` (starts Pynput listeners for timed key and mouse events)
- `RouteRecorder.mark_waypoint(self, table_id: str) -> Waypoint`
- `RouteRecorder.finish(self) -> RecordedRoute`

**Deliverable:** The recorder captures timed WASD and camera input, lets the user mark tables and the return landmark, and detects when captured mouse movement is unusable. In that case, calibration offers manual relative-turn segments. The driver refuses each event unless the game window is foreground and the emergency-stop event is clear.

- [x] Implement the input driver with focus checks before every event and guaranteed key release on pause/stop.
- [x] Capture timed movement-key and mouse-delta events; let the user mark the start, each table, and the return landmark.
- [x] Detect an unusable camera trace and provide editable relative-turn events for manual calibration.

## Task 4: Calibration and Route Profile Setup

**Files:**
- Create: `src/harvest_assistant/calibration.py`

**Interfaces:**
- `CalibrationWizard(root: tkinter.Tk, profile: Profile, screen: ScreenService, vision: VisionService, recorder: RouteRecorder)`
- `CalibrationWizard.capture_waypoint(self, table_id: str) -> WaypointCalibration`
- `CalibrationWizard.capture_interaction(self) -> InteractionCalibration`
- `CalibrationWizard.dry_run(self, route: RecordedRoute) -> None`

**Deliverable:** The wizard captures start/table anchors, a gameplay-only HUD marker, and both ready/growing icon crops, asks whether the harvest key is tapped or held, and provides a no-input route-order preview before playback.

- [x] Build the guided setup flow for game window, timer mode, and initial remaining time per table.
- [x] Capture the gameplay HUD marker, each waypoint, and icon/prompt/progress templates; save the user's tap/hold selection and timeout.
- [x] Add a no-input route-order preview that shows expected table checkpoints.

## Task 5: Scheduler and Harvest Route Runner

**Files:**
- Create: `src/harvest_assistant/runner.py`

**Interfaces:**
- `RouteRunner(profile: Profile, screen: ScreenService, vision: VisionService, driver: InputDriver, on_state_change: Callable[[RunSnapshot], None], profile_lock: Any | None = None)`
- `RouteRunner.start() -> None` (starts its worker thread and verifies the start landmark before moving)
- `RouteRunner.pause(reason: str) -> None`
- `RouteRunner.resume() -> None` (continues only from a verified waypoint checkpoint)
- `RouteRunner.stop() -> None`
- `RouteRunner.snapshot() -> RunSnapshot`

**Deliverable:** Start schedules a pass when a table is due. The runner verifies each waypoint, skips growing tables, collects only when the due time, table anchor, ready icon, and harvest prompt agree, waits for completion, then resets that table to 40 minutes. A table that becomes due after its waypoint is handled on a new pass. Any missing confirmation pauses the route.

- [x] Implement the background scheduler that waits for a due table and emits status snapshots for the UI.
- [x] Implement ordered route playback with gameplay-screen and start/waypoint anchor checks and skip behavior for growing tables.
- [x] Implement tap/hold, progress monitoring, icon-transition confirmation, timer reset, and fail-closed pause behavior.
- [x] Resume only from a verified checkpoint; otherwise require a fresh start-landmark calibration.

## Task 6: Main UI, Controls, and User Setup Guide

**Files:**
- Create: `src/harvest_assistant/app.py`
- Create: `src/harvest_assistant/__main__.py`
- Create: `README.md`
- Modify: `run.bat`

**Interfaces:**
- `HarvestAssistantApp(root: tkinter.Tk, profile: Profile, profile_path: Path, runner_factory: Callable[..., RouteRunner])`
- `refresh_table_rows(snapshot: RunSnapshot) -> None`
- `handle_hotkey(key: str) -> None`

**Deliverable:** The window lists each table, remaining time, state, route order, and calibration state. The user can add, rename, remove, or recalibrate tables and set each initial remaining time. Start/Pause/Stop are visible; F8 pauses/resumes and F9 stops immediately by default, with editable hotkeys. A separate control pauses growth countdowns when the game is paused in a menu. The README explains installation, window requirements, route calibration, timer modes, and recovery from a safety pause.

- [x] Build the table list and add/rename/remove/recalibrate actions, showing each countdown and state.
- [x] Wire Start/Pause/Stop, configurable hotkeys, manual timer pause, and profile persistence to the runner and model.
- [x] Document setup, target window requirements, route recording, calibration, timer modes, and safety-pause recovery.

## Acceptance Walkthrough

- Add one table and then several tables; confirm each row has its own countdown and route order.
- Use the no-input dry run to confirm waypoint order and safety pause behavior before enabling input.
- Confirm focus loss or a mismatched anchor stops input.
- Confirm an in-game menu or loading screen that hides the calibrated HUD marker pauses route input.
- Confirm an incomplete harvest never resets the timer and a verified ready-to-growing transition does.
- Restart the app and confirm saved countdowns reflect the chosen clock mode.

Live calibration against the actual game remains necessary before trusting a recorded walking route; the game source and live client are not present in this workspace.
