# ROB RUN Discord Rich Presence

## Intent

Show a ROB RUN activity card in Discord from the existing Hidden Pool hub. The Application ID stays fixed in code. A persisted master switch fully disables or enables the RPC connection. While enabled, status and safe location are always shown; the user can toggle the full server name, role, party state, wipe earnings and playtime. Location artwork changes among the map screen, Motel Lenny, Looty Bay, prison and a hidden-location placeholder.

## Goals

- Connect automatically to the local Discord desktop client using the fixed Application ID `1571948762367262760`; never expose it as an in-app setting.
- Reuse the current season summary, `GameWatcher` menu/battle state and the saved active server.
- Detect role and party state from the game; let the user hide each field without manually selecting its value.
- Show status and location unconditionally. Put optional visibility toggles and read-only connection status inside the existing Settings page; do not add a separate Discord page.
- Persist a master enabled switch; when disabled, clear activity and disconnect from Discord, then reconnect when enabled again.
- When hiding the server, publish only its region mask (`EU-??????` or `NA-??????`) and omit the state name. Earnings and wipe hours have independent toggles.
- Keep a fixed safe location vocabulary; never send coordinates, a custom address, or a base/home location.
- Use fixed Discord asset keys for large location artwork, a small Hidden Pool overlay icon, and a single GitHub tracker button.

## Non-goals

- Publishing residential/private locations, exact coordinates, or home location.
- Uploading assets or registering a Discord application on the user's behalf.
- Making a Discord activity joinable or controlling the game from Discord.

## Constraints

- App is a Windows Tkinter/Python hub. Settings persist through `Profile` JSON.
- Discord Rich Presence starts automatically and requires the Discord desktop client and fixed assets uploaded in the configured application.
- The built-in CTA target is the current project repository `https://github.com/1takawa/rob-run-hub-`.
- If Discord is unavailable, the hub and the other features keep working; the read-only status reports the connection state.
- Home/base location is not an option and is never sent.

## Approaches considered

1. **Editable Discord page.** Rejected because the user asked for a fixed integration and no in-app configuration.
2. **No integration status in the app.** Rejected because the user still needs to see whether Discord presence connected.
3. **Fixed integration with compact display preferences (chosen).** Keep status and coarse location on, let the user hide other data, and fail closed to the hidden-location image outside recognized public places. Keep the connection indicator and visibility toggles in Settings.

## Activity layout

- Name: `ROB RUN`.
- Details: game state and location always, plus role/party only when automatically recognized and enabled.
- State: full server or a masked region prefix, plus independently enabled wipe earnings and hours.
- Timestamp: current game-session elapsed time, based on the game's menu/battle log signal.
- Large image: the detected map screen, public location or hidden-location placeholder. Small image: the Hidden Pool emblem overlaid at the lower-right corner. The map asset is generic and has no server name, markers or coordinates. Asset keys and Application ID are fixed constants that match assets uploaded to the configured Discord application.
- One `Get ROB RUN tracker` URL button.
- Party information comes from game recognition; no party count is invented.

## Builder decisions

- Use `pypresence` as an optional runtime dependency around Discord's local IPC protocol; keep import and connection failures isolated to the presence worker.
- Keep the Application ID and asset keys in code; persist only visibility preferences in the user profile. Discord client secret is not needed.
- Refresh presence from the hub's existing UI tick, send only changed payloads, and stop/clear cleanly when the hub closes.
- Rate-limit payload updates to the documented 15-second minimum.
- Detect the open map from its bottom location-card strip and recognize public places only from explicit visible labels. Do not infer location from chat-channel names; unknown views use the hidden-location placeholder.
- Never include precise game coordinates or a free-text location in the IPC payload.

## Sources

- [Discord Rich Presence overview](https://discord.com/developers/docs/rich-presence/overview)
- [Discord Rich Presence best practices](https://discord.com/developers/docs/rich-presence/best-practices)
- [Discord Activity reference](https://discord.com/developers/docs/social-sdk/classdiscordpp_1_1Activity.html)
- [pypresence button example](https://github.com/qwertyquerty/pypresence/blob/master/examples/buttons.py)
