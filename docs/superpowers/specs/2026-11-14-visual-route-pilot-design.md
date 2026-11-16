# Visual route pilot

The user wants an immediate test of screen-guided travel from the standard house spawn, through the house and container doors, down to the grow room. Structural layouts are assumed repeatable. This is a separate test mode; existing timed production and recorded routes remain available.

## Approaches

1. Replay one recorded input stream with extra screenshots: easy but still fails when the character drifts.
2. Follow a small graph of visible landmarks with short movement pulses, confirm doors via their interaction prompt, and stop on uncertainty. **Chosen for the pilot.**
3. Free-form mapping and obstacle avoidance: substantially larger and not needed in a fixed base.

## Pilot behavior

- Use clean crops from the user's screenshots as initial landmarks: house door, porch stairs, container, container door, grow-room ceiling. Scanning and short movement pulses move toward the current landmark. The app captures a new frame after each pulse.
- The pilot does not harvest, alter production timers, or use the normal route. It opens and closes the two doors only after recognizing the game prompt. It stops when the grow room is recognized.
- A separate button starts it. F9 and an adjacent Stop button cancel immediately through the existing input guard. All stages have bounds and an error screenshot.
- Fixed camera sensitivity, resolution and placement still require a live trial; image assets are a starting point, not a guarantee. If the next landmark cannot be found or the route stalls, stop rather than continue blindly.
- Keep full-screen OCR infrequent; localize using cropped/downsized frames and feature matching. Do not run a continuous overlay.

## Open choices made for the pilot

- The route begins at the house spawn shown in screenshot 1 and ends on recognition of the grow room shown in screenshot 9.
- Outdoor movement uses container and stairs as targets. A short door-threshold crossing can be open-loop but is verified immediately afterward; regular travel uses feedback.
- No autonomous recovery from an unknown location in this pilot.
