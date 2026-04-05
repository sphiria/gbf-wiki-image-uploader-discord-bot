# AGENTS.md

This file captures repo-specific context that should stay consistent across AI sessions.

## Purpose

This repo is a Discord bot frontend for GBF Wiki image upload and Main Page promotion maintenance tasks.
Most changes should preserve existing command contracts, wiki filename conventions, and deployment behavior.

## Source Of Truth

- This repository is the canonical active repo for the Discord bot.
- The legacy `AdlaiT/gbf-wiki-image-uploader-discord-bot` checkout is closed and must not be treated as canonical.
- When multiple local copies diverge, prefer this repo and its `origin` remote (`sphiria/gbf-wiki-image-uploader-discord-bot`).

## Important Files

- `main.py`: Discord slash commands, validation, progress reporting, MainPageDraw page editing helpers.
- `images.py`: CDN download logic, wiki upload logic, duplicate handling, redirect creation.
- `docs/discord-slash-command-reference.md`: user-facing slash command reference. Update when command contracts change.
- `README.md`: short command overview. Keep it aligned with actual code.

## Canonical Duplicate Handling

- `images.py` now uses a centralized duplicate-family registry inside `check_image()` for supported ID-based canonical filenames.
- For supported families, duplicate binaries should not be re-uploaded and should not trigger repeated canonical file moves on reruns.
- Supported duplicate resolution should prefer a stable canonical file and redirect later duplicate canonical titles to it.
- Canonical winner rule:
  - numeric lowest ID token when comparable numerically
  - lexical lowest normalized ID token otherwise
- Duplicate canonicalization is only safe when both files match the same family and identical family signature.
- Family signatures must keep meaningfully different variants separate, such as:
  - section or subgroup
  - style suffix like `_st2`
  - melee sprite slot
  - `m` vs `s`
  - event difficulty or index
  - gender or presentation variant
  - extension
- NPC-style duplicate handling exception:
  - for `Npc ...`, `Npc_my_...`, `Npc_result_lvup_...`, `npc_f_skin_*`, and `npc_s_skin_*` canonical families, omitted gender and `_0` are treated as the same duplicate signature when the binaries match
  - the non-gendered canonical title is the stable winner over the `_0` form
  - `_1` variants remain meaningfully distinct and must not collapse into the non-gendered or `_0` canonical
- Skin tall-element CDN note:
  - `npc/f/skin` asset probes currently work against both `prd-game-a-granbluefantasy.akamaized.net` and `prd-game-a2-granbluefantasy.akamaized.net`
  - for future `npc/f/skin` probe additions, prefer `prd-game-a` as the default host unless there is a specific reason not to
- Skin square-element CDN note:
  - `npc/s/skin` asset probes should use `prd-game-a-granbluefantasy.akamaized.net` as the default host
  - canonical naming for these assets uses `npc_s_skin_*`
- Do not broaden duplicate-family matching casually. A shared bitmap alone is not enough reason to collapse two canonical files together.
- Unsupported or ambiguous canonical filename patterns should keep the older generic duplicate behavior until an explicit safe family rule is added.
- When changing canonical filename generators in upload flows, review the duplicate-family registry in `images.py` so canonicalization behavior stays aligned.

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
- `/help` exists as an in-bot slash-command reference.
- Do not assume a new command will appear without either:
  - bot restart/redeploy
  - manual `/synccommands`

## Help Command Contract

- `/help` is informational and should not use upload cooldown or the global `upload_lock`.
- Params:
  - `command` (optional)
- Behavior:
  - no `command`: show a concise overview of supported slash commands
  - valid `command`: show detailed help for that command
  - invalid `command`: return an error with suggested valid command names when possible
- Autocomplete:
  - the `command` field should autocomplete slash command names
  - filtering should be case-insensitive
  - suggestions should stay within Discord's 25-choice limit
- Help text should stay aligned with:
  - `README.md`
  - `docs/discord-slash-command-reference.md`

## Rising Rotation Contract

- `/risingrotation` updates the fixed page:
  - `Granblue Fantasy Versus: Rising/Rotation`
- Params:
  - `start_date`
  - `c2`
  - optional `c1`, `c3`, `c4`, `c5`
  - optional `notes`
  - optional `week_override`
  - optional `start_time_override`
  - optional `end_date_override`
  - optional `end_time_override`
- Auto-filled defaults:
  - `week` defaults to the current top row week plus `1`
  - `start_time` defaults to `11:00` JST
  - `end` defaults to `start_date + 7 days` at `10:59` JST
- Override rules:
  - `week_override` is for backfills/corrections and must not jump ahead of the next auto week
  - `end_date_override` and `end_time_override` must be provided together
  - explicit end overrides fully replace the default calculated end
- Insert behavior:
  - generate one `{{RisingRotation/Row}}`
  - prepend it immediately under the `{{RisingRotation|` wrapper
  - preserve existing older rows and wrapper text
  - abort if the resolved week already exists anywhere on the page
- Wikitext output:
  - `start` and `end` are stored as `YYYY-MM-DD HH:MM JST`
  - blank character fields are omitted instead of emitted as empty params
  - `c1` should only be emitted when explicitly supplied
- Character suggestions:
  - `c1`-`c5` should autocomplete from a local roster list
  - suggestions should be case-insensitive
  - suggestions should stay within Discord's 25-choice limit
  - do not suggest `All Characters` or `38 Characters`
  - fields must remain free-form so newly added characters can still be typed manually
- Summary output should include:
  - resolved week/start/end values
  - the updated page link
  - a copyable `wikitext` block for the inserted row

## Environment / Deployment

- `DRY_RUN` is a supported runtime flag.
- `ALLOWED_ROLES` is runtime-configurable.
- `IMAGE_PROBE_DELAY` is a supported runtime flag for slowing image probe/upload pacing in `images.py` across all environments.
- `LOCAL_IMAGE_PROBE_DELAY` is a supported runtime flag for slowing image probe/upload pacing only when `PROXY_URL` is unset; this is preferred for local runs so the deployed bot keeps its normal pacing.
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
  - `guide`
  - `trailer mp3`
  - `voice banner`
  - `top`
  - `raid_thumb`
- Default `max_index` is `20` for `notice`, `start`, and `guide`.
- `trailer mp3` uses the fixed single audio file and defaults to `1`.
- `voice banner` also defaults to `20`.
- `top` uses the fixed single teaser file and defaults to `1`.
- `raid_thumb` currently processes the fixed `vhard`, `vhard_1`, `vhard_2`, `ex`, `ex_1`, `ex_2`, `high`, `high_1`, `high_2`, `hell`, `free_proud`, `free_proud_1`, and `free_proud_2` files and defaults to `13`.
- `raid_thumb` should attempt every configured fixed variant even when some URLs are missing.
- `notice`, `start`, and `voice banner` stop on the first missing base index.
- `guide` stops on the first missing base index.
- `guide` probes the base suffix plus `_0` and `_1` for each base index.
- `guide` should continue to later base indices when a subindex like `_0` or `_1` is missing.
- `guide` tries `.jpg` first and `.png` second for each suffix, and uploaded filenames keep the actual source extension.
- `trailer mp3` uses the fixed `assets_en/sound/voice/{event_id}.mp3` URL and uploads the canonical file without redirects.
- `voice banner` tries `.png` first and `.jpg` second for each index, and uploaded filenames keep the actual source extension.

### Event Upload Naming

- `notice`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_notice_{index}.png`
  - Canonical: `{event_id}_banner_event_notice_{index}.png`
  - Redirect: `banner_{event_name}_notice_{index}.png`
- `start`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_start_{index}.png`
  - Canonical: `{event_id}_banner_event_start_{index}.png`
  - Redirect: `banner_{event_name}_{index}.png`
- `guide`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/event/{event_id}/assets/tips/description_event_{suffix}.jpg`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/event/{event_id}/assets/tips/description_event_{suffix}.png`
  - Suffixes: `{index}`, `{index}_0`, `{index}_1`
  - Canonical: `{event_id}_description_event_{suffix}.{ext}`
  - Redirect: `description_{event_name}_{suffix}.{ext}`
- `trailer mp3`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/sound/voice/{event_id}.mp3`
  - Canonical: `{event_id}.mp3`
  - No redirect
  - `event_name` remains required for command consistency but is ignored for naming
- `voice banner`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_trailer_{index}.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/banner/events/{event_id}/banner_event_trailer_{index}.jpg`
  - Canonical: `{event_id}_banner_event_trailer_{index}.{ext}`
  - Redirect: `banner_{event_name}_trailer_{index}.{ext}`
- `top`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/event/{event_id}/assets/teaser/event_teaser_top.jpg`
  - Canonical: `{event_id}_top.jpg`
  - Redirect: `{event_name}_top.jpg`
- `raid_thumb`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_vhard.png`
  - Canonical: `summon_qm_{event_id}_vhard.png`
  - Redirect: `BattleRaid_{event_name}_Very_Hard.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_vhard_1.png`
  - Canonical: `summon_qm_{event_id}_vhard_1.png`
  - Redirect: `BattleRaid_{event_name}_Very_Hard2.png`
  - Redirect: `BattleRaid_{event_name}_Very_Hard_2.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_vhard_2.png`
  - Canonical: `summon_qm_{event_id}_vhard_2.png`
  - Redirect: `BattleRaid_{event_name}_Very_Hard3.png`
  - Redirect: `BattleRaid_{event_name}_Very_Hard_3.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_ex.png`
  - Canonical: `summon_qm_{event_id}_ex.png`
  - Redirect: `BattleRaid_{event_name}_Extreme.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_ex_1.png`
  - Canonical: `summon_qm_{event_id}_ex_1.png`
  - Redirect: `BattleRaid_{event_name}_Extreme2.png`
  - Redirect: `BattleRaid_{event_name}_Extreme_2.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_ex_2.png`
  - Canonical: `summon_qm_{event_id}_ex_2.png`
  - Redirect: `BattleRaid_{event_name}_Extreme3.png`
  - Redirect: `BattleRaid_{event_name}_Extreme_3.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_high.png`
  - Canonical: `summon_{event_id}_high.png`
  - Redirect: `BattleRaid_{event_name}_Impossible.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_high_1.png`
  - Canonical: `summon_qm_{event_id}_high_1.png`
  - Redirect: `BattleRaid_{event_name}_Impossible2.png`
  - Redirect: `BattleRaid_{event_name}_Impossible 2.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_high_2.png`
  - Canonical: `summon_qm_{event_id}_high_2.png`
  - Redirect: `BattleRaid_{event_name}_Impossible3.png`
  - Redirect: `BattleRaid_{event_name}_Impossible 3.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/summon/qm/{event_id}_hell.png`
  - Canonical: `qm_{event_id}_hell.png`
  - Redirect: `BattleRaid_{event_name}_Nightmare.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/quest/assets/free/{event_id}_free_proud.png`
  - Canonical: `quest_assets_{event_id}_free_proud.png`
  - Redirect: `BattleRaid_{event_name}_Proud.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/quest/assets/{event_id}_free_proud_1.png`
  - Canonical: `quest_assets_{event_id}_free_proud_1.png`
  - Redirect: `BattleRaid_{event_name}_Proud2.png`
  - Redirect: `BattleRaid_{event_name}_Proud_2.png`
  - URL: `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/quest/assets/{event_id}_free_proud_2.png`
  - Canonical: `quest_assets_{event_id}_free_proud_2.png`
  - Redirect: `BattleRaid_{event_name}_Proud3.png`
  - Redirect: `BattleRaid_{event_name}_Proud_3.png`

### Event Upload Summary Output

- Successful `/eventupload` runs should include:
  - counts for processed/uploaded/duplicates/failed
  - wiki links for canonical and redirect files
  - a copyable code block labeled `Paste into EventHistory template` for `notice` and `start`
  - a copyable code block labeled `Paste into guide gallery` for `guide`
- The EventHistory code block is semicolon-separated redirect filenames with underscores, for example:
  - `banner_PS_the_Astrals_1.png;banner_PS_the_Astrals_2.png`
- This block should still appear on reruns that resolve to duplicates, as long as files were processed.
- The `guide` gallery block should list redirect filenames in probe order.
- `trailer mp3`, `voice banner`, `top`, and `raid_thumb` should not include the EventHistory copy box.

### Event Upload UI Notes

- `/eventupload` asset type dropdown labels should stay lowercase:
  - `notice`
  - `start`
  - `guide`
  - `trailer mp3`
  - `voice banner`
  - `top`
  - `raid_thumb`
- The slash-command help text for `event_id` should describe it as a folder identifier, not a numeric-only id.

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
- variant-suffixed forms such as `{name}_myA2.png`

## Character Style Image Contract

- `/imgupload page_type:character` recognizes `style_id` from the target `{{Character}}` template.
- `style_id` behavior:
  - missing or `1`: treat as default style (no style suffix in canonical names)
  - explicit `2` or higher: treat as style-specific assets
- For explicit styles (`style_id >= 2`), character CDN filename probes and canonical wiki filenames append `_st{style_id}` after the existing index suffix:
  - example: `{id}_01_st2.png`
- This style suffix applies across character image families handled by the character upload flow, including:
  - standard `npc/<section>` character assets
  - `npc/my`
  - `npc/result_lvup`
  - Sky Compass `characters/1138x1138`
- Redirect naming is unchanged for style pages; no `_st{style_id}` is appended to redirect filenames.

## Character FS Skin Contract

- `/imgupload page_type:character_fs_skin` scans only the character `f_skin` and `s_skin` asset families from the target `{{Character}}` id.
- `page_type:character` no longer owns `f_skin` or `s_skin`; those heavy skin subsets belong exclusively to `character_fs_skin`.
- `/imgupload page_type:character_full` runs `character` and `character_fs_skin` sequentially for the same page, so it covers both the lighter standard character families and the heavier skin-only families in one command.
- `f_skin` canonical naming:
  - `npc_f_skin_{id}{suffix}.jpg`
- `s_skin` canonical naming:
  - `npc_s_skin_{id}{suffix}.jpg`
- `f_skin` redirects keep the existing character-style `_tall...` naming pattern.
- `s_skin` redirects follow the existing character-style `_square...` naming pattern.

## Character Result Level Up Image Contract

- `/imgupload page_type:character` also checks `npc/result_lvup` assets from the target `{{Character}}` id.
- CDN pattern:
  - `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/npc/result_lvup/{id}{index}.png`
- Canonical naming:
  - `Npc_result_lvup_{id}{index}.png`
- Redirect naming follows the existing character variant mapping style, but uses `_result_lvup`:
  - `{name}_result_lvup.png`
  - variant-suffixed forms such as `{name}_result_lvupA2.png`
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
- Redirect naming uses `_HD`, following the existing Sky Compass/class-image style:
  - `{name}_HD.png`
  - variant-suffixed forms such as `{name}_HDA2.png`

## Upload Comment Contract

- Upload comments shown in MediaWiki file history for the affected `/imgupload` and `/statusupload` flows should be:
  - `Uploaded by VyrnBot`
- This is intentionally separate from file page categorization.
- File pages should still receive their normal category tags via follow-up category edits.

## MainPageDraw Ownership

- `promoupdate` currently owns these subtemplates:
  - `Template:MainPageDraw/SuptixPromo`
  - `Template:MainPageDraw/SuptixPromoEndDate`
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

### PromoUpdate Contract

- `/promoupdate` is separate from `/drawupdate`; do not overload `/drawupdate` with non-draw promo sections.
- Supported `promo_type` values:
  - `suptix`
- `/promoupdate` currently owns:
  - `Template:MainPageDraw/SuptixPromo`
  - `Template:MainPageDraw/SuptixPromoEndDate`
- `/promoupdate` params currently include:
  - `promo_type`
  - `promo_id`
  - `end_date`
  - `end_time`
  - `link_target`
- `promo_id` accepts the bare id, `banner_<id>`, `banner_<id>.png`, or the full CDN URL and normalizes to the id portion.
- Current `suptix` subtemplate contract:
  - subtemplate page: `Template:MainPageDraw/SuptixPromo`
  - end date page: `Template:MainPageDraw/SuptixPromoEndDate`
  - resolved file title: `banner_{promo_id}.png`
  - default link target: `Surprise Ticket`
  - `SuptixPromo` should use both `{{ScheduledContent|end_time={{MainPageDraw/SuptixPromoEndDate}} JST|...}}` and `{{EventCountdown|{{MainPageDraw/SuptixPromoEndDate}} JST|...}}`
  - `SuptixPromoEndDate` stores the supplied `end_date` + `end_time` in `YYYY-MM-DD HH:MM` JST-local form without the `JST` suffix
- `/promoupdate` does not upload files; it assumes the referenced wiki file already exists.
- `/promoupdate` should halt before saving when the resolved file title does not exist on the wiki or redirect to a real file page.

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
- If a `Template:MainPageDraw` section becomes managed by a slash command, update `Template:MainPageDraw` documentation/noinclude notes so editors can see which command owns that section and which inputs it expects.
- If a behavior is being changed for operator convenience, document the reason here if it affects future maintenance.
- If a session identifies a worthwhile refactoring opportunity, it may suggest it.
- Do not perform refactoring just because an opportunity exists; explain the problem, the proposed direction, and the tradeoff first, then get explicit user permission before doing the refactor.

## Session Checklist

- If a slash command contract changed, update:
  - `README.md`
  - `docs/discord-slash-command-reference.md`
- If a `Template:MainPageDraw` section is newly managed by a slash command or its managed inputs changed, update `Template:MainPageDraw` noinclude documentation.
- If MainPageDraw behavior changed, confirm the affected subtemplate ownership still matches this file.
- If canonical names, redirect names, or CDN URL patterns changed, record the new contract here.
- Run at least:
  - `python3 -m py_compile main.py images.py`
- In the final response, mention any required deploy or `/synccommands` step when command registration may be affected.
