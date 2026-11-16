# Background control for standalone ROB RUN

## Goal

Let Harvest Assistant read and control the standalone ROB RUN Windows application while it remains visible in a window on a secondary monitor and another application is active on the primary monitor. No virtual machine, remote server, process injection, or game-memory reading.

## Chosen approach

- Capture the target window's client rectangle from the virtual desktop, including secondary monitors.
- Route keyboard and mouse messages to the selected game window instead of sending global input that would affect the user's active application.
- Keep the existing foreground mode as the default; background mode is an explicit profile setting.
- Keep the game window visible and unminimized. The user should avoid covering it on its monitor.

## Current constraints

The current capture and input paths require foreground focus. Background window messages may be ignored by the game client or its input layer; this implementation can prevent the helper from stealing focus, but cannot guarantee that the standalone application accepts messages while unfocused. If the client ignores them, the helper must report that limitation and stop safely.

## Out of scope

- Capturing a minimized or fully covered game window.
- Virtual machines, remote desktops, or a second computer.
- Injecting into the game process or reading/modifying game memory.
- Changing the learned route, harvest recognition, or timer rules.

## Source areas

- `src/harvest_assistant/screen.py` — target-window selection, capture, focus readiness.
- `src/harvest_assistant/window_input.py` — background window messages.
- `src/harvest_assistant/input_driver.py` — route keyboard and mouse backend.
- `src/harvest_assistant/app.py` and `ui/settings_page.py` — persisted opt-in setting and startup behavior.
