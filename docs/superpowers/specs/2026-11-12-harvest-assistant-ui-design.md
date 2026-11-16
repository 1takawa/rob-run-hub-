# Harvest Assistant — UI Redesign

## Intent

Rework the companion's menus into a clear, cohesive desktop interface. The user should be able to read each table's time at a glance and understand how to record a route, calibrate screen recognition, and change settings without navigating scattered dialogs.

## Goals

- Keep the existing Windows desktop app, Python entry point, profile format, hotkeys, and safe harvest behavior.
- Organize the app into four persistent sections: **Столы**, **Маршрут**, **Калибровка**, **Настройки**.
- Make the tables screen the home page, with a clear next-ready summary, per-table countdown/status, and prominent start, pause, and stop controls.
- Make route recording a guided sequence: start at a repeatable landmark, record walking and camera turns, mark each table, return to the start, and finish.
- Make calibration a checklist that shows which route and per-table signals are configured, with a direct action for each missing step.
- Put game-window binding, clock mode, manual timer pause, and hotkeys together in Settings.
- Use a restrained, high-contrast theme: graphite surfaces, muted violet secondary highlights drawn from the game lighting, and a plant-green action accent.
- Use short Russian labels, consistent button hierarchy, clear active/disabled states, and visible feedback after each action.

## Non-goals

- No changes to route playback, image matching thresholds, timer semantics, profile schema, or game input behavior.
- No in-game overlay, web server, new UI dependency, or account/network integration.
- No automatic modification of calibration data or route events.

## Constraints

- The current app is Tkinter/ttk and is launched by `run.bat`; keep that platform and launcher.
- The current UI actions and safeguards in `app.py` and `calibration.py` must remain available.
- The main window must remain usable around 1020×720 and at narrower Windows window sizes.
- Native dialogs remain appropriate for selecting reference screenshots and screen regions.

## Approaches

1. **Rebuild the Tkinter shell and pages (selected by the user).** Keep existing controls and state model, apply one design system, add persistent navigation, and move route/calibration actions into dedicated pages. This preserves the launcher and dependencies while giving the workflow one consistent home.
2. **Adopt CustomTkinter.** Offers more styled widgets, but introduces another dependency and requires replacing many existing ttk controls without improving the core workflow.
3. **Move to a local web interface.** Gives greater visual freedom but requires a local server and a new bridge between browser controls and the Python automation worker.

## Proposed layout

- **App shell:** narrow left navigation with the four sections; a compact top bar shows game-window connection and automation state; page content sits in a consistent card/grid layout.
- **Столы:** next-ready summary; one row/card per table with name, countdown, state badge, calibration state, and per-table actions; a clearly separated control group for Start/Pause/Stop and growth-timer pause.
- **Маршрут:** recording state and hotkey guide; an ordered table checkpoint list; start/finish instructions; buttons for record, inspect the no-input route preview, and add manual camera turns when needed.
- **Калибровка:** route checklist for start/end landmarks and gameplay HUD marker, followed by per-table checklist for room anchor, ready/growing icons, harvest prompt, and progress reference; collection key/tap/hold configuration in a compact action card.
- **Настройки:** game title and bind-current-window action; timer clock mode; manual timer pause; editable hotkeys with conflict guidance and one save action.
- **Dialogs:** use consistent compact modal styling for adding/renaming/removing a table and entering an initial time; keep native file/image pickers for image references.

## Interaction rules

- Page changes never start or stop automation.
- Route recording has an explicit visible recording state and shows how many tables are marked.
- Missing setup items appear as actionable checklist rows, not only in error dialogs.
- The next-ready countdown is derived from the configured table timer. If the on-screen state conflicts with the timer, surface an attention warning and keep the existing fail-closed route behavior.
- Destructive actions such as removing a table stay explicit and clear that the route must be recorded again.
- Status and error messages remain visible long enough to read and do not disappear on the next timer refresh.
- All existing safety checks, emergency hotkey behavior, and profile saves remain intact.

## Settings and calibration decisions

- Preserve current commit behavior: game-window binding takes effect from its own button, growth-timer pause takes effect immediately, and **Save settings** applies the clock mode and hotkeys together.
- The progress-bar reference is optional and informational. Automation remains gated by the table anchor, ready/growing icon pair, harvest prompt, gameplay-screen marker, and confirmed ready-to-growing transition.
- Route and table calibration actions save after each successful capture. Incomplete or cancelled calibration does not replace the active profile data.

## Open implementation calls

- Exact spacing, typography, icons, and small-screen behavior are left to the implementation, using the palette and hierarchy above.
- Use standard Tkinter/ttk styling and built-in drawing/text where practical; avoid external image assets unless they materially improve clarity.
