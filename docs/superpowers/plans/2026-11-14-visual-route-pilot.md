# Visual Route Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate screen-guided pilot from the standard house spawn to the grow room.

**Architecture:** A feature matcher recognizes landmark crops; a small visual state machine scans, centers, advances in short pulses and verifies door prompts. Existing ScreenService and InputDriver provide capture and guarded input. The pilot is launched from Settings and reports through RunSnapshot.

**Tech Stack:** Python 3.12, Tkinter, OpenCV SIFT, Windows OCR, existing input driver.

**Spec:** `docs/superpowers/specs/2026-11-14-visual-route-pilot-design.md`

## Global Constraints

- Never change production timers or harvest in test mode.
- Preserve the existing macro route.
- Stop and release inputs when target focus is lost, a landmark is missing, or a stage times out.
- Keep screenshots/OCR bounded to avoid a heavy FPS cost.

## Review Focus

- A wrong door prompt must never trigger E.
- Losing game focus must release all keys.
- Template matching must reject false/weak matches.
- Stalled movement must time out instead of walking forever.
- Existing profile loading and normal automation must still work.

### Task 1: Landmark recognition and movement decisions

**Files:** `src/harvest_assistant/visual_route.py`, `src/harvest_assistant/visual_assets/*.png`, `tests/test_visual_route.py`

- [ ] Write failing tests for cross-distance landmark detection, prompt normalization, and steering decision.
- [ ] Run them and confirm feature absence is the failure.
- [ ] Implement minimal feature matching and decisions.
- [ ] Run focused tests.

### Task 2: Guarded route runner

**Files:** `src/harvest_assistant/visual_route.py`, `tests/test_visual_route.py`

- [ ] Write failing tests for no E without the expected prompt, timeout, and stop behavior.
- [ ] Run them and confirm failure.
- [ ] Implement stage sequence, state updates and diagnostic screenshot.
- [ ] Run focused tests.

### Task 3: Hub entry point and verification

**Files:** `src/harvest_assistant/app.py`, `src/harvest_assistant/ui/settings_page.py`, `README.md`

- [ ] Add separate start/stop controls without disturbing the normal route.
- [ ] Run compilation, focused tests, and hidden Tk smoke check.
- [ ] Document test scope, F9, and game-side validation needed.
