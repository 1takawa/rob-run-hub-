# Local Harvest Macro — Design

## Intent

Create a standalone Windows companion for the user's authorized local game client. It should track each grow table's 40-minute cycle and automatically walk a recorded room route, turn the camera, and collect harvest when the game's on-screen prompt confirms a table is ready. The game source is unavailable, so the companion works from visible UI and keyboard/mouse input.

## Goals

- Show a configurable list of tables, each with a countdown and a clear state: growing, ready, collecting, or needs attention.
- Let the user set the initial remaining time for each existing crop. After a collection is visually confirmed, restart that table's timer at 40 minutes because the next growth cycle begins automatically.
- Record an ordered route that includes walking between tables and turning the camera, starting and ending at a repeatable room landmark.
- At each recorded table waypoint, verify the expected table using a saved screen anchor and its over-table status icon. The supplied screenshots show a green check when ready and a clock during growth; use that transition as the primary confirmation that a harvest succeeded.
- Calibrate the interaction from the live UI so the profile records whether the displayed harvest key is tapped or held and how long to wait. Reset the timer only when the ready icon changes to the growing clock and the harvest prompt disappears.
- Support adding and naming more tables without changing code.
- Provide clear Start, Pause, Stop, and emergency-stop controls, plus a no-input route-order preview and calibration flow before live input.
- Save table timers and route profiles locally so the UI can recover countdowns after restart.

## Non-goals

- No planting, inventory management, crafting, combat, or other game actions.
- No access to game memory, network traffic, private APIs, or source code.
- No route recovery or map navigation when the recorded route no longer reaches a table.

## Anti-goals

- Never send repeated blind inputs when the prompt or progress state is not recognized.
- Never continue the route after losing game focus, encountering an unexpected screen, or failing a waypoint check; pause and ask the user to correct the state.

## Constraints and assumptions

- Windows desktop game; Python 3.12 is available. The project folder has no existing source or dependencies.
- The user has permission to automate this local project. The game is a standalone full-scale title, not Roblox; its source is unavailable.
- Harvest interval defaults to 40 minutes and can be adjusted per profile.
- The player must walk between tables and turn the camera. A route therefore needs to capture timed WASD/mouse input and mark a waypoint at each table.
- The prompt alone is not enough to identify a station because all tables may show the same text. Calibration must capture a view anchor for each waypoint plus both ready and growing icon states; a missing or mismatched anchor is a route failure.
- The screenshots show the `E` harvest prompt and a visible progress bar. The game window, resolution/UI scale, and movement conditions must stay consistent with calibration for recorded input to remain reliable.
- Initial timer values are user-set because the companion cannot see when a crop was planted. After the first confirmed harvest, the companion can track recurring cycles itself.
- Timer profiles need a setting for whether growth counts while the helper or game is closed. Default to wall-clock time. `game_open` counts only while both the helper and target game are open; time between helper launches is ignored because the helper cannot observe whether the game process remained open. The UI also needs a manual timer pause for an in-game pause menu.

## Approaches considered

1. **Game API or supported mod.** Most reliable and least sensitive to camera or screen layout, but no source or integration is available. Not selected.
2. **Local screen-and-input macro.** Records a room route, watches the visible prompt/progress bar, sends keyboard/mouse input, and provides the requested timer UI. Selected because it works without game source and matches the user's choice. It needs calibration and can drift if movement, collisions, resolution, or game timing changes.
3. **Timer-only companion.** Reliable countdowns and reminders, but leaves the repeated walking and collecting to the user. Not selected because the user wants automatic collection too.

## Proposed design

- Build a separate Python desktop app with a Tkinter interface, OpenCV template matching for visible state checks, PyAutoGUI for screenshots, and PyDirectInput for game keyboard/mouse input. Keep profiles in a local JSON file. These packages are not currently installed; Python itself is available.
- The first-run wizard captures the game's harvest prompt and progress indicator at the configured resolution, records a gameplay-only HUD marker, then records route input and table waypoints. It must end at the same landmark so each pass has a stable start.
- The first-run wizard also captures a screen anchor for the start landmark and each table waypoint, plus both ready and growing icon states. During interaction calibration, the user selects whether the displayed harvest key should be tapped or held and sets the collection timeout.
- The route runner checks that the game window is foreground and the gameplay HUD marker remains visible during route input, and verifies the start/waypoint anchor before interacting. A route pass starts when at least one table is due, follows the recorded order, and collects a due table only when its anchor, ready icon, and harvest prompt are all recognized. If another table becomes due after its waypoint, the app returns to the start and begins another pass. It verifies the ready-to-growing icon change and prompt disappearance before resetting that table's timer.
- If the prompt is missing for a due table, progress does not complete, or the game window loses focus, the app pauses immediately and shows the table and reason. It does not improvise movement or retry input blindly.
- After a safety pause, Resume is allowed only if the current screen matches the saved checkpoint for that waypoint. Otherwise, require the user to return to the start landmark and begin a fresh pass.
- The main window shows controls and one row per table with remaining time, status, route order, and calibration state. Use F8 to pause/resume and F9 as an emergency stop; let the user change these hotkeys if they conflict with the game.
- Include a no-input route-order preview, then per-table visual calibration before enabling the full route.

## Open implementation calls

- Tune image-match regions and thresholds to the actual game window during calibration.
- Keep the game resolution and UI scale stable after calibration. Each visual crop is matched within a larger search area to allow small alignment changes.
- Use conservative movement playback and waypoint checks; exact route reliability cannot be guaranteed without game source or a live calibration session.
- If the game does not expose a stable visible table anchor or ready/growing icon in the supplied UI, pause the automation design and revise recognition before enabling collection.
