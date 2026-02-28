# AGENTS.md

This file captures repo-specific context that should stay consistent across AI sessions.

## Purpose

This repo is a Discord bot frontend for GBF Wiki image upload and Main Page promotion maintenance tasks.
Most changes should preserve existing command contracts, wiki filename conventions, and deployment behavior.

## Important Files

- `main.py`: Discord slash commands, validation, progress reporting, MainPageDraw page editing helpers.
- `images.py`: CDN download logic, wiki upload logic, duplicate handling, redirect creation.
- `docs/discord-slash-command-reference.md`: user-facing slash command reference. Update when command contracts change.
- `README.md`: short command overview. Keep it aligned with actual code.

## Command Conventions

- Upload-style commands generally follow the same flow:
  - validate inputs
  - enforce role check
  - enforce cooldown
  - enforce global `upload_lock`
  - send a start message
  - run a thread-backed worker
  - show periodic progress updates
  - post a summary with links or other copy-pasteable output
- When changing an existing command contract, update both:
  - `README.md`
  - `docs/discord-slash-command-reference.md`

## Discord Sync Behavior

- Slash commands are synced automatically on startup in `WikiBot.setup_hook()`.
- `/synccommands` exists as a manual recovery tool if Discord command sync drifts.
- Do not assume a new command will appear without either:
  - bot restart/redeploy
  - manual `/synccommands`

## Environment / Deployment

- `DRY_RUN` is a supported runtime flag.
- `ALLOWED_ROLES` is runtime-configurable.
- `ENABLE_EVENTUPLOAD` was temporary and has been removed. Do not reintroduce a feature gate for `/eventupload` unless explicitly requested.

## Event Upload Contract

- `/eventupload` is always registered.
- Params:
  - `event_id`
  - `event_name`
  - `asset_type`
  - `max_index`
- Supported `asset_type` values:
  - `notice`
  - `start`
- Default `max_index` is `20` for both asset types.

### Event Upload Naming

- `notice`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_notice_{index}.png`
  - Canonical: `{event_id}_banner_event_notice_{index}.png`
  - Redirect: `banner_{event_name}_notice_{index}.png`
- `start`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_start_{index}.png`
  - Canonical: `{event_id}_banner_event_start_{index}.png`
  - Redirect: `banner_{event_name}_{index}.png`

### Event Upload Summary Output

- Successful `/eventupload` runs should include:
  - counts for processed/uploaded/duplicates/failed
  - wiki links for canonical and redirect files
  - a copyable code block labeled `Paste into EventHistory template`
- The EventHistory code block is semicolon-separated redirect filenames with underscores, for example:
  - `banner_PS_the_Astrals_1.png;banner_PS_the_Astrals_2.png`
- This block should still appear on reruns that resolve to duplicates, as long as files were processed.

## Validation Rules Worth Preserving

- `event_name` uses file-name validation, not page-name validation.
- Invalid characters for `event_name`:
  - `# < > [ ] { } | :`
  - ASCII control characters
- `event_id` currently allows only lowercase letters, numbers, and underscores.

## MainPageDraw Ownership

- `drawupdate` currently owns these subtemplates:
  - `Template:MainPageDraw/PromoMode`
  - `Template:MainPageDraw/EndDate`
  - `Template:MainPageDraw/SinglePromo`
  - `Template:MainPageDraw/DoublePromoLeft`
  - `Template:MainPageDraw/DoublePromoRight`
  - `Template:MainPageDraw/ElementPromoBanners`
  - `Template:MainPageDraw/ElementPromoIcons`
- `rateup` owns these subtemplates:
  - `Template:MainPageDraw/RateUps`
  - `Template:MainPageDraw/RateUpsEndDate`
- `rateup` must not overwrite `Template:MainPageDraw/EndDate`; its end date is intentionally separate from the banner rotation end date.
- Save order matters for draw updates:
  - content pages first
  - `EndDate`
  - `PromoMode` last

## Code Health Notes

- The repo has repeated command-runner patterns in `main.py`.
- Prefer extracting shared helpers rather than copying another command block when adding new upload-style commands.
- Prefer reusing existing wiki upload helpers in `images.py` rather than creating near-duplicate loops.
- If behavior changes, keep summary formatting and status payloads consistent with existing commands unless there is a reason to diverge.

## Working Norms For Future Sessions

- Do not silently change canonical or redirect naming conventions.
- Do not silently change wiki page targets or MainPageDraw subtemplate ownership.
- Do not leave docs stale after changing slash command parameters or outputs.
- If a behavior is being changed for operator convenience, document the reason here if it affects future maintenance.
