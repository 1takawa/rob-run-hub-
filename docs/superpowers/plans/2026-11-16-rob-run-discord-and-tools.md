# ROB RUN Discord Presence and Progression Tools Implementation Plan

> **For agentic workers:** implement task-by-task in this worktree. The user asked to proceed after finishing their requirements; use the hybrid defaults recorded in the specs without another approval round.

**Goal:** Add fixed Discord Rich Presence with compact visibility controls and a `ROB RUN Info` tab with raid-decoder and season-progression tools.

**Architecture:** Keep Discord IPC in a background worker that consumes a small immutable presence payload from the Tk app. Keep game-independent raid and progression calculations in focused modules; a Tk page edits and persists season goals separately from the game profile.

**Tech Stack:** Python 3.11–3.13, Tkinter, existing profile/season stores, `pypresence` for local Discord RPC.

**Spec:** `docs/superpowers/specs/2026-11-16-rob-run-discord-presence-design.md`; `docs/superpowers/specs/2026-11-16-rob-run-progression-tools-design.md`.

## Global Constraints

- Never access or modify the ROB RUN graphics/settings configuration.
- Never send coordinates, exact base/home location, or free-text location to Discord.
- Discord presence starts automatically with fixed Application ID `1571948762367262760`; unavailable Discord must not prevent the hub from opening.
- Game status and safe location are always shown; unknown residential locations fail closed to the hidden placeholder.
- Server costs and progression requirements are editable and stored locally.
- Do not copy the ROB RUN Info site's catalog, text or images wholesale.
- Do not add or run tests unless the user asks for testing or verification.

## Review Focus

- Discord RPC missing or Discord not running: normal hub startup and other controls remain available; presence gives a useful status.
- User selects hidden location or automatic area: payload contains no precise location; automatic area is only menu/in-game.
- Season plan row with zero/negative or incomplete numbers: totals remain non-negative and invalid edits are rejected.
- More fuse entries than the structure's slots: reject them and keep calculated defense bounded by structure capacity.
- Existing profile JSON with legacy Discord settings: those keys are ignored; other profile values remain intact.

---

### Task 1: Add pure domain models for raid calculations and season plans

**Files:**
- Create: `src/harvest_assistant/rob_run_tools.py`
- Create: `src/harvest_assistant/progression_store.py`

**Interfaces:**
- Produces `raid_defense(structure_key, fuses) -> dict` with defense, capacity, decoder counts and install seconds.
- Produces `plan_totals(rows) -> dict` with research, purchase and grand totals.
- Produces `ProgressionStore(path)` with `rows_for_season(season_id)`, `add`, `update`, `remove`, and atomic JSON save.

- [x] Define structure constants and validate fuse counts, then implement decoder counts using ceiling division and 12 seconds per decoder.
- [x] Define season goal row normalization and non-negative total calculations.
- [x] Persist rows keyed by season ID in a separate local `progression.json` file.
- [x] Review formulas against the cited guide and the design spec; no automated tests are run per the task instruction.

### Task 2: Add the ROB RUN Info tab

**Files:**
- Create: `src/harvest_assistant/ui/rob_run_info_page.py`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- Consumes Task 1 domain functions and `ProgressionStore`.
- Page constructor receives the current-season provider and a `ProgressionStore`.

- [x] Add two page sections: Decoder calculator and Season plan.
- [x] Add structure selection and three fuse-count controls; show defense, decoders by quality and total install time; show validation for excess fuse slots.
- [x] Add goal create/edit/delete controls; show goal status, tier/sentence notes and the three cost totals.
- [x] Bind page refresh to current season changes and preserve each plan by season ID; include history selection.
- [x] Use only existing theme helpers and manual editable prices; no website catalog scrape.

### Task 3: Add fixed Discord Rich Presence

**Files:**
- Modify: `src/harvest_assistant/models.py`
- Modify: `src/harvest_assistant/persistence.py`
- Create: `src/harvest_assistant/discord_presence.py`
- Modify: `src/harvest_assistant/ui/settings_page.py`
- Modify: `requirements.txt`
- Modify: `src/harvest_assistant/app.py`

**Interfaces:**
- Presence controller exposes `publish(payload)`, `status`, and `close()`; it owns its RPC worker thread.
- Presence payload accepts only allow-listed text/image fields and never location coordinates.

- [x] Keep Discord Application ID and image keys as fixed constants outside the user profile.
- [x] Build a worker that lazy-imports `pypresence`, reconnects safely, updates only changed presence no faster than once per 15 seconds, and clears/closes on disable/shutdown.
- [x] Keep App ID fixed; put visibility toggles and connection status in the existing Settings page, with status/location always on.
- [x] Build payload values from current season summary, session time and log-backed menu/battle state; area choices are a closed safe list with no base/home option.
- [x] Add the `Get ROB RUN tracker` button using fixed location art keys and a small `hidden_pool` overlay asset.
- [x] Add `pypresence` to requirements; existing launch script installs it when requirements change.

### Task 4: Navigation, documentation and static review

**Files:**
- Modify: `src/harvest_assistant/app.py`
- Modify: `README.md`

- [x] Add `ROB RUN Info` to navigation; ensure close shuts down the RPC worker.
- [x] Document fixed Discord behavior, privacy limits, plan save location, and editable cost assumptions.
- [x] Inspect all touched functions and call sites; do not run test suites or alter game configuration.

