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

## Advyrnture Gear Contract

- `/imgupload page_type:advyrnture_gear` scans `{{Advyrnture/Cosmetic/Row}}` templates on the target page.
- Pertinent parameters:
  - `id`
  - `name`
- Uploads always use the `id` for these CDN paths and canonical filenames:
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/item/cosmetic/s/{id}.jpg`
    - Canonical: `cosmetic_s_{id}.jpg`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/item/cosmetic/m/{id}.jpg`
    - Canonical: `cosmetic_m_{id}.jpg`
- When `name` is present, also create:
  - File redirect: `{name} (Advyrnture) square.jpg`
  - File redirect: `{name} (Advyrnture) icon.jpg`
  - Page redirect: `{name} (Advyrnture)` -> `Let's Go, Advyrnturers!#{name}`
- When `name` is blank, skip redirect creation and upload canonicals only.

## Advyrnture Pal Contract

- `/imgupload page_type:advyrnture_pal` scans `{{Advyrnture/Pal}}` templates on the target page.
- Pertinent parameters:
  - `id`
  - `name`
- Uploads use the `id` for these CDN paths and canonical filenames:
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/{id}.jpg`
    - Canonical: `vyrnsampo_character_thumb_{id}.jpg`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/{id}_friendship.jpg`
    - Canonical: `vyrnsampo_character_thumb_{id}_friendship.jpg`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/{id}_fatigue.jpg`
    - Canonical: `vyrnsampo_character_thumb_{id}_fatigue.jpg`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/detail/{id}.png`
    - Canonical: `vyrnsampo_character_detail_{id}.png`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/detail/{id}_friendship.png`
    - Canonical: `vyrnsampo_character_detail_{id}_friendship.png`
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/special_skill_label/{id}.png`
    - Canonical: `Label {name}.png`
    - No redirect
- When `name` is present, also create:
  - File redirect: `{name} (Advyrnture) icon.jpg`
  - File redirect: `{name} (Friendship) icon.jpg`
  - File redirect: `{name} (Fatigue) icon.jpg`
  - File redirect: `{name} (Advyrnture).png`
  - File redirect: `{name} (Friendship).png`
- When `name` is blank, skip all name-based uploads and redirects.

## Character Home Image Contract

- `/imgupload page_type:character` also checks `npc/my` assets from the target `{{Character}}` id.
- CDN pattern:
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/my/{id}{index}.png`
- Canonical naming:
  - `Npc_my_{id}{index}.png`
- Redirect naming follows the existing `m` icon mapping style, but uses `_my`:
  - `{name}_my.png`
  - variant-suffixed forms such as `{name}_my A2.png`

## Character Result Level Up Image Contract

- `/imgupload page_type:character` also checks `npc/result_lvup` assets from the target `{{Character}}` id.
- CDN pattern:
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/result_lvup/{id}{index}.png`
- Canonical naming:
  - `Npc_result_lvup_{id}{index}.png`
- Redirect naming follows the existing character variant mapping style, but uses `_result_lvup`:
  - `{name}_result_lvup.png`
  - variant-suffixed forms such as `{name}_result_lvup A2.png`
- Category:
  - `Result Level Up Character Images`

## Character Sky Compass Zoom Contract

- `/imgupload page_type:character` also checks Sky Compass higher-resolution zoom-style assets using the target `{{Character}}` id and the same index set as the standard `zoom` assets.
- CDN pattern:
  - `https://media.skycompass.io/assets/customizes/characters/1138x1138/{id}{index}.png`
- Canonical naming:
  - `characters_1138x1138_{id}{index}.png`
- Categories:
  - `Sky Compass Images`
  - `Sky Compass Character Images`
- Redirects are intentionally omitted for now until a naming contract is decided.

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
- `/rateup` currently requires both pipe-separated inputs:
  - `rateups`
  - `sparkable`
- Save order matters for draw updates:
  - content pages first
  - `EndDate`
  - `PromoMode` last

### DrawUpdate Contract

- `/drawupdate` params currently include:
  - `mode`
  - `end_date`
  - `end_time`
  - `left_banner_id`
  - `right_banner_id`
  - `left_count`
  - `right_count`
  - `max_probe`
  - `link_target`
  - `element_start`
- `end_date` and `end_time` are intentionally split:
  - `end_date` uses `YYYY-MM-DD`
  - `end_time` uses `HH:MM`
  - common `end_time` suggestions are `18:59`, `11:59`, and `23:59`
- `drawupdate` file detection rules:
  - if count override is provided, validate contiguous `1..count`
  - if count override is omitted, probe from index `1` and stop on first miss
  - if index `1` is missing, abort

### DrawUpdate Modes

- Supported `drawupdate` modes:
  - `single`
  - `double`
  - `element-single`
  - `element-double`
- Mode-specific constraints:
  - `single`
    - `right_banner_id` and `right_count` must be omitted
  - `double`
    - `right_banner_id` is required
  - `element-single`
    - `right_banner_id` and `right_count` must be omitted
  - `element-double`
    - `right_banner_id` is required
    - `right_count` optional
- Both element modes intentionally write `Template:MainPageDraw/PromoMode` as `element` for template compatibility.

### Element Mode Rules

- `element_start` defaults to `fire`.
- Element order is fixed and rotates as:
  - `fire -> water -> earth -> wind -> light -> dark`
- `element-single`
  - uses one slug only
  - daily banner pairs are built from indices `(1,2)`, `(3,4)`, ...
  - if there is an odd final banner, the last day reuses the last banner for the pair
- `element-double`
  - each side uses its own slug and its own index pairs `(1,2)`, `(3,4)`, ...
  - left and right are rendered as separate columns inside one outer `double-promotion` wrapper
  - counts do not need to match
  - at least one side must have `12` banners
  - when one side is shorter, it reuses its last pair for remaining days
- All element banner days are wrapped in `ScheduledContent`; day 1 must not be left unscheduled.
- Element banner `ScheduledContent` blocks are joined with `<!--` / `-->` separators to avoid whitespace rendering gaps.
- The final element banner day extends its end time by `+ 3 days` so banners remain visible after the event ends.

### DrawUpdate Output / Edit Summaries

- Successful `/drawupdate` summaries should:
  - echo the resolved inputs
  - list updated page links as URL-only bullets
  - use suppressed-embed links via `<https://...>`
  - include the Main Page purge reminder as `<https://gbf.wiki/Main_Page/purge>`
- `Updated pages` bullets should contain only links, not repeated page labels.
- `drawupdate` page edit summaries:
  - `Template:MainPageDraw/EndDate` should include the exact datetime
  - `Template:MainPageDraw/PromoMode` should include the resolved mode value
  - content subtemplates can keep the generic draw promotion summary

## Code Health Notes

- The repo has repeated command-runner patterns in `main.py`.
- Similar commands are currently a mix of shared patterns and session-by-session drift.
- There is enough duplication between upload-style commands that future confusion is a real risk if new work keeps copying nearby code without consolidation.
- Prefer extracting shared helpers rather than copying another command block when adding new upload-style commands.
- Prefer reusing existing wiki upload helpers in `images.py` rather than creating near-duplicate loops.
- More uniformity is preferred between similar functions, especially:
  - command flow in `main.py`
  - status payload shape
  - progress message structure
  - summary formatting
- A future refactor to unify these flows is on the table.
- If behavior changes, keep summary formatting and status payloads consistent with existing commands unless there is a reason to diverge.

## Working Norms For Future Sessions

- Do not silently change canonical or redirect naming conventions.
- Do not silently change wiki page targets or MainPageDraw subtemplate ownership.
- Do not leave docs stale after changing slash command parameters or outputs.
- If a behavior is being changed for operator convenience, document the reason here if it affects future maintenance.
- If a session identifies a worthwhile refactoring opportunity, it may suggest it.
- Do not perform refactoring just because an opportunity exists; explain the problem, the proposed direction, and the tradeoff first, then get explicit user permission before doing the refactor.

## Session Checklist

- If a slash command contract changed, update:
  - `README.md`
  - `docs/discord-slash-command-reference.md`
- If MainPageDraw behavior changed, confirm the affected subtemplate ownership still matches this file.
- If canonical names, redirect names, or CDN URL patterns changed, record the new contract here.
- Run at least:
  - `python3 -m py_compile main.py images.py`
- In the final response, mention any required deploy or `/synccommands` step when command registration may be affected.
