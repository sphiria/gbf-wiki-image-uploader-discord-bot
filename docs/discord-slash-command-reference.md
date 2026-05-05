**Wiki Image Upload Bot - Discord Slash Command Reference**

__General Rules__
- Commands (except `/synccommands`) require one of the allowed roles (`Wiki Editor`, `Wiki Admin`, `Wiki Discord Moderator`, `Verified Editor` by default) or the server owner; responses are ephemeral when the check fails.
- Every user has a 5s cooldown per upload-style command, and the bot only runs one upload at a time, so kick off the next request after the previous status message completes.
- Progress pings land every ~15s; final summaries include key counts and wiki links. If the bot runs in dry-run mode you will see a `[DRY RUN]` prefix.

__Reference Lists (from `main.py`)__
- `PAGE_TYPES`: `character`, `character_fs_skin`, `weapon`, `summon`, `class`, `class_skin`, `skin`, `npc`, `story_location`, `profile_stickers`, `profile_backgrounds`, `profile_other_characters`, `profile_favorite_art`, `profile_trophies`, `profile_trinkets`, `profile_frames`, `profile_designs`, `artifact`, `item`, `manatura`, `shield`, `skill_icons`, `bullet`, `advyrnture_gear`, `advyrnture_pal`.
- `ITEM_TYPES`: `article`, `normal`, `recycling`, `skillplus`, `evolution`, `lottery`, `npcaugment`, `set`, `ticket`, `campaign`, `npcarousal`, `memorial`.

**/help**
Usage: `/help command:<optional>`
- Purpose: Show a concise overview of all supported slash commands, or detailed help for one command directly in Discord.
- Inputs:
  - `command` - optional slash command name. Autocomplete suggests valid command names and filters as you type.
- Checks & Limits: no upload cooldown or upload lock; this is informational only.
- Output:
  - No `command`: a short overview list of slash commands plus a prompt to rerun with `command`.
  - Valid `command`: detailed help for that command, automatically split into Discord-safe chunks if needed.
  - Invalid `command`: ephemeral error with matching command suggestions when available.

**/imgupload**
Usage: `/imgupload page_type:<character|character_fs_skin|weapon|summon|class|class_skin|skin|npc|story_location|profile_stickers|profile_backgrounds|profile_other_characters|profile_favorite_art|profile_trophies|profile_trinkets|profile_frames|profile_designs|artifact|item|manatura|shield|skill_icons|bullet|advyrnture_gear|advyrnture_pal> page_name:<Wiki Page Title> filter:<id>`
- Purpose: Pull every image the upload scripts expect for a wiki page and push them to the correct file titles.
- Inputs:
  - `page_type` - pick the asset family; determines which CDN paths are scanned.
    - `character` - if `{{Character}}` includes explicit `style_id` and it is `2` or higher, character asset downloads use CDN/canonical filenames with `_st<style_id>` appended after the variant suffix (for example `3040088000_01_st2.png`) to avoid overwriting default-style canonical files. Redirect naming remains unchanged. This lighter mode excludes character `f_skin` and `s_skin`.
    - `character_fs_skin` - uploads only the character `f_skin` and `s_skin` asset families from the target `{{Character}}` id. `f_skin` uses canonical `npc_f_skin_*` filenames, `s_skin` uses canonical `npc_s_skin_*` filenames, and both probe the broader character-style element-skin suffix families.
    - `profile_stickers` (shown in Discord as `profile (stickers)`) - uploads Profile Room sticker images, square thumbnails, and icons from `{{ProfileRoom/Sticker/Row}}` rows and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Sticker Images]]`.
    - `profile_backgrounds` (shown in Discord as `profile (backgrounds)`) - uploads Profile Room background images, square thumbnails, and icons from `{{ProfileRoom/Background/Row}}` rows, creates EN redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Background Images]]`.
    - `profile_other_characters` (shown in Discord as `profile (other characters)`) - uploads Profile Room other-character images, icons, and squares from `{{ProfileRoom/OtherCharacter/Row}}` rows, creates EN redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Other Character Images]]`.
    - `profile_favorite_art` (shown in Discord as `profile (favorite art)`) - uploads Profile Room favorite-art images, square thumbnails, and icons from `{{ProfileRoom/FavoriteArt/Row}}` rows, creates EN redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Favorite Art Images]]`.
    - `profile_trophies` (shown in Discord as `profile (trophies)`) - uploads Profile Room trophy images, square thumbnails, and icons from `{{ProfileRoom/RoomTrophy/Row}}` rows, creates EN redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Trophy Images]]`.
    - `profile_trinkets` (shown in Discord as `profile (trinkets)`) - uploads Profile Room trinket images, square thumbnails, and icons from `{{ProfileRoom/Trinket/Row}}` rows, creates EN redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Trinket Images]]`.
    - `profile_frames` (shown in Discord as `profile (frames)`) - uploads Profile Room color-specific frame images and icons plus shared square thumbnails from `{{ProfileRoom/Frame/Row}}` rows, creates color-disambiguated EN square/icon redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Frame Images]]`.
    - `profile_designs` (shown in Discord as `profile (designs)`) - uploads Profile Room color-specific design images and icons plus shared square thumbnails from `{{ProfileRoom/Design/Row}}` rows, creates color-disambiguated EN square/icon redirects, and tags files with `[[Category:Profile Room Images]]` and `[[Category:Profile Room Design Images]]`.
    - `story_location` (shown in Discord as `story location`) - searches `{{MainQuestTabs}}` and `{{EventTabs}}`, reads `location_id` and `header_image`, downloads `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/archive/assets/island_l/<location_id>.jpg`, uploads it as `island_l_<location_id>.jpg`, and redirects the template's `header_image` title when it differs. If duplicate binaries are found, the lowest base id before any underscore is the stable canonical title.
    - `class_skin` - **requires** the `filter` input (numeric `id` from the `{{ClassSkin}}` template). The bot uploads the shared skin artwork plus every configured variant (MC icon/square, gendered raid/quest/talk/etc., PM, Sky Compass, skin_name, and more) under canonical `Leader_*` / `jobs_*` filenames and redirect titles such as `{name} (Gran) raid.jpg`.
    - `bullet` - searches for every `{{Bullet}}` template, reads the `id` and `name`, downloads `https://prd-game-a1-granbluefantasy.akamaized.net/assets_en/img/sp/assets/bullet/m/<id>.jpg` and `/s/<id>.jpg`, uploads them as `Bullet_m_<id>.jpg` / `Bullet_s_<id>.jpg`, and builds redirects `<Name>_icon.jpg` / `<Name>_square.jpg`.
    - `advyrnture_gear` - searches for every `{{Advyrnture/Cosmetic/Row}}` template, reads `id` and `name`, downloads `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/assets/item/cosmetic/m/<id>.jpg` and `/s/<id>.jpg`, uploads them as `cosmetic_m_<id>.jpg` / `cosmetic_s_<id>.jpg`, creates redirects `<Name> (Advyrnture) icon.jpg` / `<Name> (Advyrnture) square.jpg` when `name` is present, and also ensures the page redirect `<Name> (Advyrnture)` -> `Let's Go, Advyrnturers!#<Name>`. If `name` is blank, the canonical uploads still run but redirect creation is skipped.
    - `advyrnture_pal` - searches for every `{{Advyrnture/Pal}}` template, reads `id` and `name`, downloads `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/<id>.jpg`, `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/<id>_friendship.jpg`, `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/thumb/<id>_fatigue.jpg`, `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/detail/<id>.png`, `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/detail/<id>_friendship.png`, and `https://prd-game-a-granbluefantasy.akamaized.net/assets_en/img/sp/vyrnsampo/assets/character/special_skill_label/<id>.png`, uploads them as `vyrnsampo_character_thumb_<id>.jpg`, `vyrnsampo_character_thumb_<id>_friendship.jpg`, `vyrnsampo_character_thumb_<id>_fatigue.jpg`, `vyrnsampo_character_detail_<id>.png`, `vyrnsampo_character_detail_<id>_friendship.png`, and `Label <Name>.png`, and creates the redirects `<Name> (Advyrnture) icon.jpg`, `<Name> (Friendship) icon.jpg`, `<Name> (Fatigue) icon.jpg`, `<Name> (Advyrnture).png`, and `<Name> (Friendship).png` when `name` is present. If `name` is blank, the name-based uploads and redirects are skipped.
    - `skill_icons` - extracts ability icon parameters from the Character template (`a1_icon`, `a2_icon`, `a3_icon`, `a4_icon`, `a1a_icon`, `a2a_icon`, `a3a_icon`, `a4a_icon`, `a1b_icon`, `a2b_icon`, `a3b_icon`, `a4b_icon`) and uploads the corresponding icons from the CDN. Supports comma-separated values in icon parameters (e.g., `Ability_m_2232_3.png,Ability_m_2233_3.png`). Icons are uploaded with canonical names matching the parameter values (e.g., `Ability_m_2731_3.png`). If no icons are found in the parameters, the upload is skipped.
  - `page_name` - target wiki page (1-100 chars; rejects control characters plus #, <, >, [, ], {, }, | so titles with &, !, ?, :, /, etc. are accepted). The bot trims whitespace before running.
  - `filter` - mandatory for `class_skin`; optional for Profile Room page types. For Profile Room uploads, it exact-matches the row `id` and uploads only that row; `all` processes the full table. Other page types ignore it.
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
  - `banner_id` - the part between `banner_` and the trailing index (letters/numbers/underscores only). You may also paste `banner_<id>`, `banner_<id>.png`, or the full CDN URL; the command normalizes those automatically.
  - `max_index` - highest numeric suffix to try, 1-50 with a default of 12.
- Checks & Limits: role/cooldown/lock apply; invalid IDs are rejected up front.
- Output: shows which banner slug/index it is processing, then reports processed/uploaded/failed counts and wiki links for every successful upload.

**/promoupdate**
Usage: `/promoupdate promo_type:<suptix> promo_id:<id> end_date:<YYYY-MM-DD> end_time:<HH:MM> link_target:<wiki page (defaults Surprise Ticket)>`
- Purpose: Update a supported non-draw MainPageDraw promo subtemplate without using `/drawupdate`.
- Inputs:
  - `promo_type` - currently only `suptix`.
  - `promo_id` - accepts the bare id, `banner_<id>`, `banner_<id>.png`, or the full CDN URL. For the current Suptix banner filename `banner_special_217.png`, use `special_217` or paste the full filename/URL.
  - `end_date` - required JST date in `YYYY-MM-DD`.
  - `end_time` - required JST time in `HH:MM`; common values are `18:59`, `11:59`, and `23:59`.
  - `link_target` - wiki page target for the promo image; defaults to `Surprise Ticket`.
- Checks & Limits: same role/cooldown/lock rules as upload commands.
- Notes: `/promoupdate` does not upload the asset; it assumes the resolved wiki file already exists.
- Validation: the command halts before saving if the resolved `File:banner_<promo_id>.png` title does not exist on the wiki or redirect to a real file page.
- Output:
  - Updated page links.
  - The resolved wiki file name.
  - Main Page purge reminder.

**/drawupdate**
Usage: `/drawupdate mode:<single|double|element-single|element-double> end_date:<YYYY-MM-DD> end_time:<HH:MM> left_banner_id:<id> right_banner_id:<id?> left_count:<1-50?> right_count:<1-50?> max_probe:<1-50 (defaults 12)> link_target:<wiki page (defaults Draw)> element_start:<fire|water|earth|wind|light|dark>`
- Purpose: Update MainPageDraw draw promotion subtemplates (single/double/element modes) without editing `Template:MainPageDraw` directly.
- Inputs:
  - `mode` - `single`, `double`, `element-single`, or `element-double`.
  - `end_date` - required, strict JST date string in `YYYY-MM-DD`.
  - `end_time` - required, strict JST time string in `HH:MM` (24-hour). The command suggests common values (`18:59`, `11:59`, `23:59`) via autocomplete and still allows custom input.
  - `left_banner_id` - required banner id for the single banner set or left side (same format as `/bannerupload`).
  - `right_banner_id` - required when `mode=double` or `mode=element-double`; must be omitted for `mode=single` and `mode=element-single`.
  - `element_start` - optional starting element for element modes; defaults to `fire`.
  - `left_count` - optional explicit count override for left/only side.
  - `right_count` - optional explicit count override for right side (`mode=double` and `mode=element-double`).
  - `max_probe` - optional probe cap for auto-detection, default `12`.
  - `link_target` - optional wiki page target for banner clicks, default `Draw`.
- File existence detection:
  - If `*_count` is provided, the bot validates a contiguous range from index `1..count`.
  - If `*_count` is omitted, the bot probes from index `1` and stops at the first miss.
  - Redirects are accepted if they resolve to a real file page.
  - If index `1` is missing, the command aborts for that side.
- Element mode behavior:
  - `mode=element-single`: uses `left_banner_id` only; each day uses banner index pairs from that slug (`1,2` then `3,4`, ...).
  - When `element-single` has an odd number of banners, the final day's pair reuses the last banner.
  - `mode=element-double`: uses both `left_banner_id` and `right_banner_id`; each side builds its own daily index pairs (`1,2` then `3,4`, ...) and renders as separate left/right blocks.
  - `mode=element-double` does not require matching counts, but at least one side must have 12 banners.
  - When one side has fewer banners, the last banner on that side is reused for remaining days.
  - The final day's banner `ScheduledContent` end time is extended by `+ 3 days` so banners do not disappear immediately after the event ends.
  - Builds the daily swap schedule automatically and rotates elements from `element_start` in the order:
    `fire -> water -> earth -> wind -> light -> dark`.
- Wiki pages updated:
  - Always: `Template:MainPageDraw/EndDate`, `Template:MainPageDraw/PromoMode`.
  - `mode=single`: `Template:MainPageDraw/SinglePromo`.
  - `mode=double`: `Template:MainPageDraw/DoublePromoLeft`, `Template:MainPageDraw/DoublePromoRight`.
  - `mode=element-single` and `mode=element-double`: `Template:MainPageDraw/ElementPromoBanners`, `Template:MainPageDraw/ElementPromoIcons`.
  - Both element modes set `Template:MainPageDraw/PromoMode` to `element` for template compatibility.
  - Save order is content pages -> `EndDate` -> `PromoMode` (mode switch happens last).
- Checks & Limits: same role/cooldown/lock behavior as other upload-style commands.
- Output: progress updates while resolving/saving, then a summary that echoes command inputs, shows updated page links (URL-only bullets), banner files used, and includes a purge reminder link: `<https://gbf.wiki/Main_Page/purge>` (embed suppressed).

**/rateup**
Usage: `/rateup end_date:<YYYY-MM-DD> end_time:<HH:MM> rateups:<Name A|Name B> sparkable:<Name C|Name D>`
- Purpose: Update the MainPageDraw rate-up subtemplates without touching the shared draw banner end date.
- Inputs:
  - `end_date` - required, strict JST date string in `YYYY-MM-DD`.
  - `end_time` - required, strict JST time string in `HH:MM` (24-hour). The command suggests common values (`18:59`, `11:59`, `23:59`) via autocomplete and still allows custom input.
  - `rateups` - required pipe-separated character list for the `{{CharacterIcons}}` rate-up group, e.g. `Gawain (Valentine)|Wamdus (Valentine)|Yatima`.
  - `sparkable` - required pipe-separated character list for the sparkable group, e.g. `Catura|Sandira`.
- Wiki pages updated:
  - `Template:MainPageDraw/RateUps`
  - `Template:MainPageDraw/RateUpsEndDate`
- Notes:
  - `/rateup` keeps its end date separate from `Template:MainPageDraw/EndDate`, because rate-ups do not always end with the banner rotation.
  - When both groups are present, the rate-up icons render on the left with `{{icon|drawrateup|size=100}}` and sparkable icons render on the right with `{{label|sparkable|size=73|link=Spark}}`.
- Checks & Limits: same role/cooldown/lock behavior as other upload-style commands; names use the same wiki page-name validation as other wiki-facing inputs.
- Output: progress updates while saving, then a summary that echoes inputs, shows updated page links, includes the exact rendered subtemplate in a copyable `wikitext` code block, and includes a purge reminder link: `<https://gbf.wiki/Main_Page/purge>` (embed suppressed).

**/risingrotation**
Usage: `/risingrotation start_date:<YYYY-MM-DD> c2:<Name> c3:<Name?> c4:<Name?> c5:<Name?> c1:<Name?> notes:<text?> week_override:<int?> start_time_override:<HH:MM?> end_date_override:<YYYY-MM-DD?> end_time_override:<HH:MM?>`
- Purpose: Insert one new `{{RisingRotation/Row}}` at the top of `Granblue Fantasy Versus: Rising/Rotation` without hand-editing the page.
- Inputs:
  - `start_date` - required JST date in `YYYY-MM-DD`.
  - `start_time_override` - optional JST time in `HH:MM`. If omitted, start time defaults to `11:00`.
  - `end_date_override` / `end_time_override` - optional override pair for special cases. If omitted, end resolves to `start_date + 7 days` at `10:59 JST`.
  - `c2` - required character slot.
  - `c1`, `c3`, `c4`, `c5` - optional character slots for exception or extended weeks.
  - `notes` - optional text copied into the row’s `notes` parameter.
  - `week_override` - optional manual week for backfills/corrections.
- Character autocomplete:
  - `c1`-`c5` suggest a local GBVSR roster list as you type.
  - Suggestions are case-insensitive, capped to Discord’s 25-choice limit, and intentionally exclude `All Characters` and `38 Characters`.
  - Fields remain free-form, so you can still type roster entries that are newer than the bot’s local suggestion list.
- Resolution rules:
  - The command reads the current top row on `Granblue Fantasy Versus: Rising/Rotation` and auto-resolves the next week as `top_week + 1`.
  - `week_override` may backfill/correct an older week, but it may not jump ahead of the next auto week.
  - The command aborts if the resolved week already exists anywhere on the page.
- Checks & Limits: same role/cooldown/lock behavior as other upload-style commands.
- Output: progress updates while loading/saving, then a summary with the resolved week/start/end values, the updated page link, and a copyable `wikitext` block containing the inserted row.

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
- Usage: `/eventupload event_id:<biography042> event_name:<Event Name> asset_type:<notice|start|guide|trailer_mp3|voice_banner|top|raid_thumb> max_index:<optional>`
- Purpose: Upload event notice/start banner assets, event guide panels, event trailer audio, event trailer banners, the event teaser top image, and event raid thumbnails, then create matching redirects where applicable.
- Inputs:
  - `event_id` - CDN folder slug such as `biography042`; must be lowercase letters/numbers/underscores.
  - `event_name` - display name used when building redirect filenames (spaces allowed; keep it exactly how you want it to appear on the wiki). `trailer_mp3` keeps this field required for command consistency but ignores it for naming because no redirect is created.
  - `asset_type` - strict dropdown with:
    - `notice`: loads `img/sp/banner/events/<event_id>/banner_event_notice_<index>.png`
    - `start`: loads `img/sp/banner/events/<event_id>/banner_event_start_<index>.png`
    - `guide`: scans `img/sp/event/<event_id>/assets/tips/description_event_<suffix>.jpg` and `.png`, where `<suffix>` is each base index `1..max_index` plus optional `_0` and `_1` variants
    - `trailer_mp3`: loads `assets_en/sound/voice/<event_id>.mp3`
    - `voice_banner`: scans `img/sp/banner/events/<event_id>/banner_event_trailer_<index>.png` first and `.jpg` second
    - `top`: loads `img/sp/event/<event_id>/assets/teaser/event_teaser_top.jpg`
    - `raid_thumb`: loads `img/sp/assets/summon/qm/<event_id>_vhard.png`, `img/sp/assets/summon/qm/<event_id>_vhard_1.png`, `img/sp/assets/summon/qm/<event_id>_vhard_2.png`, `img/sp/assets/summon/qm/<event_id>_ex.png`, `img/sp/assets/summon/qm/<event_id>_ex_1.png`, `img/sp/assets/summon/qm/<event_id>_ex_2.png`, `img/sp/assets/summon/qm/<event_id>_high.png`, `img/sp/assets/summon/qm/<event_id>_high_1.png`, `img/sp/assets/summon/qm/<event_id>_high_2.png`, `img/sp/assets/summon/qm/<event_id>_hell.png`, `img/sp/quest/assets/free/<event_id>_free_proud.png`, `img/sp/quest/assets/<event_id>_free_proud_1.png`, and `img/sp/quest/assets/<event_id>_free_proud_2.png`
  - `max_index` - optional upper bound for index probing (defaults to 20 for `notice`, `start`, `guide`, and `voice_banner`; `top` and `trailer_mp3` use a single fixed file and default to 1; `raid_thumb` currently processes the fixed `vhard`, `vhard_1`, `vhard_2`, `ex`, `ex_1`, `ex_2`, `high`, `high_1`, `high_2`, `hell`, `free_proud`, `free_proud_1`, and `free_proud_2` files and defaults to 13; minimum 1).
- Checks & Limits: same role/cooldown/lock behavior as other uploaders. `notice`, `start`, and `voice_banner` stop on the first missing base index; `guide` probes the base suffix plus `_0` and `_1` for each base index, tries `.jpg` first and `.png` second, skips missing subindices, and stops on the first missing base index; `top` and `trailer_mp3` each check one fixed URL; `raid_thumb` checks every configured fixed variant even if some URLs are missing.
- Output: summary reports how many banners were processed/uploaded/duplicated along with wiki links for each canonical + redirect pair:
  - `notice`: `<event_id>_banner_event_notice_<index>.png` and `banner_<EventName>_notice_<index>.png`
  - `start`: `<event_id>_banner_event_start_<index>.png` and `banner_<EventName>_<index>.png`
  - `guide`: `<event_id>_description_event_<suffix>.<jpg|png>` and `description_<EventName>_<suffix>.<jpg|png>` using the actual source extension, plus a copyable redirect-based `<gallery>` block for MediaWiki galleries
  - `trailer_mp3`: `<event_id>.mp3` only; no redirect is created
  - `voice_banner`: `<event_id>_banner_event_trailer_<index>.<png|jpg>` and `banner_<EventName>_trailer_<index>.<png|jpg>` using the actual source extension
  - `top`: `<event_id>_top.jpg` and `<EventName>_top.jpg`
  - `raid_thumb`: `summon_qm_<event_id>_vhard.png` and `BattleRaid_<EventName>_Very_Hard.png`, `summon_qm_<event_id>_vhard_1.png` and `BattleRaid_<EventName>_Very_Hard2.png` (with extra redirect `BattleRaid_<EventName>_Very_Hard_2.png`), `summon_qm_<event_id>_vhard_2.png` and `BattleRaid_<EventName>_Very_Hard3.png` (with extra redirect `BattleRaid_<EventName>_Very_Hard_3.png`), `summon_qm_<event_id>_ex.png` and `BattleRaid_<EventName>_Extreme.png`, `summon_qm_<event_id>_ex_1.png` and `BattleRaid_<EventName>_Extreme2.png` (with extra redirect `BattleRaid_<EventName>_Extreme_2.png`), `summon_qm_<event_id>_ex_2.png` and `BattleRaid_<EventName>_Extreme3.png` (with extra redirect `BattleRaid_<EventName>_Extreme_3.png`), `summon_<event_id>_high.png` and `BattleRaid_<EventName>_Impossible.png`, `summon_qm_<event_id>_high_1.png` and `BattleRaid_<EventName>_Impossible2.png` (with extra redirect `BattleRaid_<EventName>_Impossible 2.png`), `summon_qm_<event_id>_high_2.png` and `BattleRaid_<EventName>_Impossible3.png` (with extra redirect `BattleRaid_<EventName>_Impossible 3.png`), `qm_<event_id>_hell.png` and `BattleRaid_<EventName>_Nightmare.png`, `quest_assets_<event_id>_free_proud.png` and `BattleRaid_<EventName>_Proud.png`, `quest_assets_<event_id>_free_proud_1.png` and `BattleRaid_<EventName>_Proud2.png` (with extra redirect `BattleRaid_<EventName>_Proud_2.png`), plus `quest_assets_<event_id>_free_proud_2.png` and `BattleRaid_<EventName>_Proud3.png` (with extra redirect `BattleRaid_<EventName>_Proud_3.png`)
  - `notice` and `start` include a copyable code block with semicolon-separated redirect filenames for easy Event template pasting.
  - `guide` includes a copyable `<gallery>` block listing redirect filenames in probe order.
  - `trailer_mp3`, `voice_banner`, `top`, and `raid_thumb` skip the copy box and just list links.

**/synccommands**
Usage: `/synccommands`
- Purpose: Force-register all slash commands when Discord falls out of sync.
- Requirements: Must be run in a server by an administrator (the bot rejects DMs and non-admin roles). No cooldown/lock applies.
- Output: replies ephemerally with whether the sync happened at the guild or global scope, total commands now registered, and the previous error if it had to fall back to a global sync.
