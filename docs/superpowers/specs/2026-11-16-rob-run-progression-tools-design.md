# ROB RUN Raid Calculator and Season Planner

## Intent

Add a `ROB RUN Info` helper tab to the hub with a decoder calculator for base raids and a saved, cost-aware plan for season progression.

## Goals

- Calculate base defense from structure protection and the three electronic-fuse strengths.
- Show decoder quantities for each decoder strength, total installation time at 12 seconds per decoder, and reject fuse counts above the selected structure's capacity.
- Let the user maintain goals for a season, quantity still needed, research cost, purchase cost, vendor tier and sentence requirement; summarize remaining research and purchase cash separately and together.
- Save plans locally by season ID so switching/ending a server season does not overwrite another server's plan.
- Keep prices editable because server prices can vary; don't clone the ROB RUN Info site's full item catalog or art.

## Non-goals

- Triggering or assisting an in-game raid.
- Scraping/copying the website's proprietary catalog, images or planner code.
- Treating prices/requirements as universal; all plan amounts are user-editable.

## Constraints

- Python/Tkinter, matching the existing theme and local JSON persistence.
- Decoder rules and structure capacities are preloaded from the public raid guide and surfaced with a note that game data can change.
- A plan is data only; it never alters game settings or sends game input.

## Approaches considered

1. **Embed the site's whole catalog.** Rejected because the user asked for a helper, site data/prices can change, and the site identifies its catalog and planner as its intellectual property.
2. **Free-form costs only.** Rejected because it omits useful structure in a progression checklist.
3. **Preloaded raid rules plus editable season goals (chosen).** It gives a ready-to-use decoder calculator and a flexible tracker without baking in server-specific prices or copying the site's catalog.

## Data and calculation rules

- Structures: Trailer (5 base/9 slots), Small House (15/27), Car Garage (4/6), Large Car Garage (6/12), Warehouse (10/15), Workshop (5/9), Grow Room (5/9), Chemical Laboratory (3/6), Mesh Fence (2/3), Metal Fence (5/6), Concrete Fence (10/9).
- Fuse points: uncommon 1, epic 3, legendary 5. Defense is base protection plus fuse count times points. Each decoder removes 1, 3 or 5 points; required count is `ceil(defense / decoder_value)` and each install takes 12 seconds.
- Plan row fields: title, desired amount, owned/unlocked amount, one-time research cost, purchase cost per remaining unit, vendor tier, minimum sentence, completion/research state and optional note.
- Research is counted once while the row is not researched; purchase cost is `unit_price × max(0, desired - owned)`. Totals are separated and combined.

## Sources

- [ROB RUN Info: Base hacking and defense](https://robrun.info/en/guides/raid/)
- [ROB RUN Info: Shipment Planner](https://robrun.info/en/planner/)
- [ROB RUN Info: Vendor tree mechanic](https://robrun.info/en/mechanics/vendor-tree/)
