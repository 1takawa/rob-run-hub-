# Harvest Assistant UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the scattered default Tkinter menus with a cohesive, minimal four-page desktop interface for tables, route recording, calibration, and settings.

**Architecture:** Keep the existing `HarvestAssistantApp` as the coordinator for profile state, timers, hotkeys, and `RouteRunner`. Add focused Tkinter page classes and a shared theme/components module; make calibration an embedded page while keeping screenshot and region selectors modal. Preserve current action methods and profile format.

**Tech Stack:** Python 3.12, Tkinter/ttk, current project dependencies only.

**Spec:** `docs/superpowers/specs/2026-11-12-harvest-assistant-ui-design.md`

## Global Constraints

- Keep the current Windows desktop app, `run.bat`, profile format, hotkeys, and harvest safeguards.
- Use the approved graphite, muted-violet, and plant-green palette with concise Russian labels.
- Keep the main window usable around 1020×720 and at narrower Windows window sizes.
- Preserve immediate game-window binding and timer-pause behavior; **Save settings** applies clock mode and hotkeys together.
- Do not change route playback, image matching thresholds, timer semantics, profile schema, or game input behavior.
- Do not add or run tests unless requested; this session permits manual code review only.

## Review Focus

- **A page refresh during an active route:** a one-second timer tick must update countdowns without rebuilding pages, losing selection, or changing focus.
- **A missing game window:** route and calibration pages must explain what's unavailable and keep actions disabled until binding.
- **A partial/cancelled calibration:** the checklist must retain its previous ready/incomplete state and not imply the whole profile is calibrated.
- **A table add/remove operation:** names, route order, selected row, timers, and calibration statuses must stay consistent with the existing profile actions.
- **A narrow window:** navigation, status, and Start/Pause/Stop remain visible and pages can scroll instead of clipping controls.

---

## File Map

- `src/harvest_assistant/ui/__init__.py` — UI package.
- `src/harvest_assistant/ui/theme.py` — palette, ttk styles, and shared card/button/status helpers.
- `src/harvest_assistant/ui/dashboard_page.py` — next-ready summary, table cards/list, and run controls.
- `src/harvest_assistant/ui/route_page.py` — route recording state, waypoint list, and route actions.
- `src/harvest_assistant/ui/settings_page.py` — game window, clock mode, timer pause, and hotkeys.
- `src/harvest_assistant/ui/table_dialogs.py` — styled add/rename/remove/initial-time dialogs.
- `src/harvest_assistant/calibration.py` — embed the calibration checklist into the main page host; retain `RegionSelector` and template behavior.
- `src/harvest_assistant/app.py` — shell navigation, page wiring, refresh orchestration, persistence, hotkeys, and existing action handlers.

## Task 1: Shared Theme and Navigation Shell

**Files:**
- Create: `src/harvest_assistant/ui/__init__.py`
- Create: `src/harvest_assistant/ui/theme.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- `apply_theme(root: tk.Tk) -> None`
- `create_page_shell(parent: ttk.Frame, title: str, eyebrow: str = "") -> tuple[ttk.Frame, ttk.Frame]` returns header and content containers.
- `HarvestAssistantApp._show_page(page_id: str) -> None` activates one registered page and updates navigation states.

- [x] Define shared colors, typography, spacing constants, buttons, panels, status labels, and scrollbar style using `ttk.Style(theme="clam")` or the nearest available cross-version theme.
- [x] Build a persistent left navigation rail for **Столы**, **Маршрут**, **Калибровка**, **Настройки** and a compact top status area for game connection/run state.
- [x] Replace the flat root layout with a page host and registry; implement `_show_page` without creating/destroying page widgets on timer ticks.
- [x] Keep pages within the 1020×720 design target and use a scrollable page host where narrow windows need it.

## Task 2: Dashboard and Table Dialogs

**Files:**
- Create: `src/harvest_assistant/ui/dashboard_page.py`
- Create: `src/harvest_assistant/ui/table_dialogs.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- `DashboardPage(parent, callbacks, on_select_table)` creates the timer home page.
- `DashboardPage.refresh(snapshot: RunSnapshot, tables: list[TableProfile], selected_table_id: str | None) -> None` updates content in place.
- `TableDialogs.add(parent, initial_name: str, initial_minutes: int) -> tuple[str, int] | None`.
- `TableDialogs.rename(parent, current_name: str) -> str | None`.
- `TableDialogs.confirm_remove(parent, table_name: str) -> bool`.
- `TableDialogs.set_initial_time(parent, table_name: str, initial_minutes: int) -> int | None`.

- [x] Show the next-ready countdown and one row/card per table with name, countdown, growing/ready/collecting state, route order, and calibration state.
- [x] Derive the next-ready countdown from the configured timers; if the route reports a visual mismatch, show an attention warning without treating the table as harvested.
- [x] Place **Старт**, **Пауза**, **Стоп**, and manual timer pause in a consistent action area; disable actions only when their prerequisites are absent.
- [x] Preserve table selection when `refresh` runs each second and show clear empty-state instructions when no tables exist.
- [x] Replace native simple dialogs for table operations with shared styled modals while preserving the existing callback logic and remove-route confirmation.

## Task 3: Route Recording Page

**Files:**
- Create: `src/harvest_assistant/ui/route_page.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- `RoutePage(parent, callbacks, profile) -> None`.
- `RoutePage.refresh(profile: Profile, is_recording: bool, camera_trace_usable: bool) -> None`.
- Callbacks map existing methods: `toggle_recording`, `mark_next_waypoint`, `open_calibration`, and `stop_recording` as needed.

- [x] Replace route status-only feedback with a visible recording/idle/completed state and marked-table count.
- [x] Show the recorded waypoint order and clear directions: stand at start, record walking/turns, stop at each table before marking it, return to start, finish.
- [x] Show current global hotkeys and camera-trace guidance; link manual turns and no-input route preview to the calibration page.
- [x] Keep all existing focus checks, route recorder behavior, and profile-saving calls unchanged.

## Task 4: Embedded Calibration Checklist

**Files:**
- Modify: `src/harvest_assistant/calibration.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- `CalibrationWizard(parent, profile, screen, vision, recorder, on_profile_changed) -> None` becomes a page widget with `refresh() -> None` and `capture_waypoint(table_id: str) -> WaypointCalibration`.
- Existing `RegionSelector`, `calibrate_route_landmarks`, `calibrate_gameplay_guard`, `capture_interaction`, `dry_run`, and screenshot-selection methods remain available.

- [x] Convert the calibration `Toplevel` menu into a page with route-level checklist rows and per-table checklist cards.
- [x] Display missing/complete states for start/end landmarks, gameplay HUD marker, each table anchor, both icon states, harvest prompt, and interaction settings; show the progress reference as optional.
- [x] Add a per-table selector/action so the user can calibrate directly from its card without entering an ID in a prompt.
- [x] Keep screen crop selection and image file dialogs modal; render completion only after a successful capture and persist after each completed action.
- [x] Show the no-input route order in a styled modal or subpanel without sending keyboard or mouse input.

## Task 5: Settings Page and Existing Action Wiring

**Files:**
- Create: `src/harvest_assistant/ui/settings_page.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- `SettingsPage(parent, callbacks, profile) -> None`.
- `SettingsPage.read_values() -> dict[str, str]` returns clock mode and hotkey fields for the existing validation path.
- `SettingsPage.refresh(profile: Profile, window_status: str) -> None` updates persisted values and connection state.

- [x] Group game-window title/bind action, clock mode, manual timer pause, and hotkeys into labeled cards.
- [x] Preserve current semantics: bind applies immediately, timer pause applies immediately, Save settings applies mode/hotkeys with existing validation and reserved-key checks.
- [x] Show concise inline guidance for the primary-monitor capture requirement, Ctrl+F10 binding, and reserved movement/harvest keys.
- [x] Move current settings actions from the main screen and ensure hotkey help stays synchronized with saved values.

## Task 6: Status Feedback, Responsive Polish, and Integration Review

**Files:**
- Modify: `src/harvest_assistant/app.py`
- Modify: `src/harvest_assistant/ui/theme.py`
- Modify: `src/harvest_assistant/ui/dashboard_page.py`
- Modify: `src/harvest_assistant/ui/route_page.py`
- Modify: `src/harvest_assistant/ui/settings_page.py`
- Modify: `src/harvest_assistant/calibration.py`
- Modify: `README.md`

- [x] Keep recording, route, focus-loss, calibration, and validation messages visible until replaced by a meaningful state change.
- [x] Ensure page updates do not rebuild layouts or erase selections when `_tick` updates timers.
- [x] Add vertical scrolling for dense pages and confirm controls remain reachable at the minimum window size.
- [x] Update README screenshots/labels and navigation instructions to match the new interface.
- [x] Perform a read-only review of callbacks, refresh ordering, profile locking, and safety behavior; do not add/run tests unless the user asks.

## Acceptance Review (not executed automatically)

- The home page shows all timers and the next-ready table with Start/Pause/Stop visible.
- Recording, marking a table, finishing at the start, camera guidance, and route preview are discoverable on the Route page.
- Every calibration requirement has an explicit checklist row and a direct action.
- Window binding, timer semantics/pause, and editable hotkeys are in Settings with the preserved commit behavior.
- Periodic countdown updates do not reset page state, selection, or focus.
- Missing or cancelled calibration remains visibly incomplete; existing route/harvest safeguards are untouched.
