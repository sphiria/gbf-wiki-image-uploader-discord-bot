**Wiki Image Upload Bot - Discord Slash Command Reference**

__General Rules__
- Commands (except `/synccommands`) require one of the allowed roles (`Wiki Editor`, `Wiki Admin`, `Wiki Discord Moderator`, `Verified Editor` by default) or the server owner; responses are ephemeral when the check fails.
- Every user has a 5s cooldown per upload-style command, and the bot only runs one upload at a time, so kick off the next request after the previous status message completes.
- Progress pings land every ~15s; final summaries include key counts and wiki links. If the bot runs in dry-run mode you will see a `[DRY RUN]` prefix.

__Reference Lists (from `main.py`)__
- `PAGE_TYPES`: `character`, `weapon`, `summon`, `class`, `class_skin`, `skin`, `skill_icons`, `npc`, `artifact`, `item`, `manatura`, `shield`, `bullet`.
- `ITEM_TYPES`: `article`, `normal`, `recycling`, `skillplus`, `evolution`, `lottery`, `npcaugment`, `set`, `ticket`, `campaign`, `npcarousal`, `memorial`.

**/imgupload**
Usage: `/imgupload page_type:<character|weapon|summon|class|class_skin|skin|npc|artifact|item|manatura|shield|skill_icons|bullet> page_name:<Wiki Page Title> filter:<id>`
- Purpose: Pull every image the upload scripts expect for a wiki page and push them to the correct file titles.
- Inputs:
  - `page_type` - pick the asset family; determines which CDN paths are scanned.
    - `class_skin` - **requires** the `filter` input (numeric `id` from the `{{ClassSkin}}` template). The bot uploads the shared skin artwork plus every configured variant (MC icon/square, gendered raid/quest/talk/etc., PM, Sky Compass, skin_name, and more) under canonical `Leader_*` / `jobs_*` filenames and redirect titles such as `{name} (Gran) raid.jpg`.
    - `bullet` - searches for every `{{Bullet}}` template, reads the `id` and `name`, downloads `https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/img/sp/assets/bullet/m/<id>.jpg` and `/s/<id>.jpg`, uploads them as `Bullet_m_<id>.jpg` / `Bullet_s_<id>.jpg`, and builds redirects `<Name>_icon.jpg` / `<Name>_square.jpg`.
    - `skill_icons` - extracts ability icon parameters from the Character template (`a1_icon`, `a2_icon`, `a3_icon`, `a4_icon`, `a1a_icon`, `a2a_icon`, `a3a_icon`, `a4a_icon`, `a1b_icon`, `a2b_icon`, `a3b_icon`, `a4b_icon`) and uploads the corresponding icons from the CDN. Supports comma-separated values in icon parameters (e.g., `Ability_m_2232_3.png,Ability_m_2233_3.png`). Icons are uploaded with canonical names matching the parameter values (e.g., `Ability_m_2731_3.png`). If no icons are found in the parameters, the upload is skipped.
  - `page_name` - target wiki page (1-100 chars; rejects control characters plus #, <, >, [, ], {, }, | so titles with &, !, ?, :, /, etc. are accepted). The bot trims whitespace before running.
  - `filter` - optional everywhere else, but mandatory for `class_skin`. When provided it’s the precise identifier passed to the upload script so only that asset subset runs.
- Checks & Limits: role requirement, cooldown, and single-upload lock. Invalid names are rejected before any scripts run.
- Output: background task reports "Downloading/Processing/Downloaded" states and ends with counts for images downloaded, uploaded, duplicated, failed, plus total URLs scanned. Wiki errors are echoed back in a code block.

**/statusupload**
Usage: `/statusupload status_id:<1438|status_1438|status_1438#> max_index:<1-100 (defaults 10)>`
- Purpose: Upload small/large status effect icons in bulk.
- Inputs:
  - `status_id` - accept raw numeric IDs, prefixed IDs (`status_1438`), or add a trailing `#` to iterate sequential IDs. When `#` is present the command uploads the base identifier first, then walks up to `max_index` consecutive identifiers.
  - `max_index` - only used when `status_id` ends with `#`; choose 1-100 (default 10) to define the upper bound.
- Checks & Limits: same role/cooldown/lock rules; IDs must be alphanumeric/underscore with an optional trailing `#`.
- Output: progress callouts show which icon number is active. The summary lists processed/uploaded/failed counts and wiki links for every file created; embed previews are auto-suppressed to keep the post tidy.

**/bannerupload**
Usage: `/bannerupload banner_id:<campaign id> max_index:<1-50 (defaults 12)>`
- Purpose: Upload rotating gacha banner variants by hitting `banner_<id>_<index>.jpg` on the CDN until an index fails.
- Inputs:
  - `banner_id` - the part between `banner_` and the trailing index (letters/numbers/underscores only). You may paste a full `banner_<id>` string; the command strips `banner_` automatically.
  - `max_index` - highest numeric suffix to try, 1-50 with a default of 12.
- Checks & Limits: role/cooldown/lock apply; invalid IDs are rejected up front.
- Output: shows which banner slug/index it is processing, then reports processed/uploaded/failed counts and wiki links for every successful upload.

**/itemupload**
Usage: `/itemupload item_type:<CDN folder (e.g. Article, Normal)> item_id:<CDN id> item_name:<Display Name>`
- Purpose: Upload the square/icon pair for a single item along with canonical redirects for the supplied display name.
- Inputs:
  - `item_type` - type any CDN subfolder; the UI suggests common values (`article`, `normal`, `recycling`, `skillplus`, `evolution`, `lottery`, `npcaugment`, `set`, `ticket`, `campaign`, `npcarousal`, `memorial`) but free-form entries are supported.
  - `item_id` - path fragment straight from the asset URL (1-48 chars; letters, numbers, `_`, `-` only - IDs such as `teamforce_340` are valid).
  - `item_name` - wiki-facing name used for redirect files (same validation as page names).
- Checks & Limits: role/cooldown/lock plus validation for every field before the upload worker starts.
- Output: progress mentions current variant, then the summary lists variants processed, uploads, duplicates, total URLs checked, and direct wiki links for canonical/redirect targets (`Item_<type>_s/m_<id>`, `<Name> square/icon`).

**/enemyupload**
Usage: `/enemyupload id:<8104243>`
- Purpose: Upload the S/M icons for a single enemy id and wire up the matching redirects.
- Inputs:
  - `id` - numeric CDN identifier (the part that appears in the `/enemy/s/<id>.png` path). Digits only.
- Checks & Limits: same role/cooldown/lock rules as other uploaders; input is validated before contacting the CDN.
- Output: summary lists processed/uploaded/duplicates/failed counts plus wiki links for each canonical/redirect pair (`enemy_s_<id>.png`, `enemy_m_<id>.png`, `enemy_icon_<id>_S.png`, `enemy_icon_<id>_M.png`).

**/eventupload**
-Usage: `/eventupload event_id:<biography042> event_name:<Event Name> image_type:<banner_start|banner_notice> event_run:<default|redux|redux2|side_story>`
- Purpose: Upload the indexed `banner_event_start_<index>.png` (live banners) or `banner_event_notice_<index>.png` (teaser banners) assets for an event and create the matching redirects.
- Inputs:
  - `event_id` - CDN folder slug such as `biography042`; must be lowercase letters/numbers/underscores.
  - `event_name` - display name used when building redirect filenames (spaces allowed; keep it exactly how you want it to appear on the wiki).
  - `image_type` - strict dropdown (`banner_start`, `banner_notice`) that decides which asset pipeline to run and which canonical/redirect names are generated.
  - `event_run` - pick one of the fixed options (`default`, `redux`, `redux2`, `side_story`) for bookkeeping.
- Checks & Limits: same role/cooldown/lock behavior as other uploaders; command stops when a banner index is missing (max 20 attempts).
- Output: summary reports how many banners were processed/uploaded/duplicated along with wiki links for each canonical + redirect pair (start banners create `banner_<EventName>_<index>.png`, notice banners create `banner_<EventName>_notice_<index>.png`).

**/synccommands**
Usage: `/synccommands`
- Purpose: Force-register all slash commands when Discord falls out of sync.
- Requirements: Must be run in a server by an administrator (the bot rejects DMs and non-admin roles). No cooldown/lock applies.
- Output: replies ephemerally with whether the sync happened at the guild or global scope, total commands now registered, and the previous error if it had to fall back to a global sync.
