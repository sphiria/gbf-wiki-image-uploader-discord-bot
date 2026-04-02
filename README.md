# gbf-wiki-image-uploader-discord-bot
Bot frontend for the image uploader script

## setup

1. Install dependencies with uv:
   ```bash
   uv sync
   ```

2. Set required environment variables:
   ```bash
   export DISCORD_TOKEN="your_discord_bot_token"
   export GUILD_ID="your_discord_guild_id"
   export WIKI_USERNAME="your_gbf_wiki_username"
   export WIKI_PASSWORD="your_gbf_wiki_password"
   export USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
   ```

3. Optional environment variables:
   ```bash
   export PROXY_URL="http://user:pass@host:port"  # For CDN requests
   export DRY_RUN="true"  # Enable dry-run mode (no actual uploads)
   export ALLOWED_ROLES="Wiki Editor,Wiki Admin"  # Comma-separated list
   ```

## running

Start the bot:
```bash
uv run main.py
```

## usage

See `docs/discord-slash-command-reference.md` for Discord-ready copy you can paste into a server announcement.

Quick overview of the available slash commands:

- `/help command:<optional>` — show a concise overview of all slash commands, or detailed help for one command. The `command` field autocompletes supported slash command names.
- `/imgupload page_type:<type> page_name:<title>` — bulk-upload all images for a wiki page (types: character, character_fs_skin, weapon, summon, class, class_skin, skin, npc, artifact, item, manatura, shield, skill_icons, bullet, advyrnture_gear, advyrnture_pal).
  - `page_type:character` supports `{{Character|style_id=...}}` for explicit styles (`style_id >= 2`). Styled uploads use canonical CDN/wiki filenames with `_st<style_id>` after the variant index (for example `..._01_st2.png`) so they do not overwrite default-style canonicals. Redirect naming is unchanged. Character `f_skin` / `s_skin` uploads are excluded from this lighter mode.
  - `page_type:character_fs_skin` uploads only the character `f_skin` and `s_skin` asset families, including the heavier element-skin suffix coverage.
- `/statusupload status_id:<id or id#> max_iterations:<1-100>` — upload status effect icons (use `#` to iterate sequential IDs, defaults to 10 iterations).
- `/bannerupload banner_id:<campaign id> max_index:<1-50>` — iterate `banner_<id>_<index>.jpg` assets to upload gacha banner variants (default max index 12). `banner_id` may be the bare id, `banner_<id>`, `banner_<id>.png`, or the full CDN URL.
- `/promoupdate promo_type:<suptix> promo_id:<id> end_date:<YYYY-MM-DD> end_time:<HH:MM> link_target:<wiki page>` — update a supported non-draw MainPageDraw promo subtemplate set. `promo_id` may be the bare id, `banner_<id>`, `banner_<id>.png`, or the full CDN URL. This command does not upload the file.
- `/rateup end_date:<YYYY-MM-DD> end_time:<HH:MM> rateups:<Name A|Name B> sparkable:<Name C|Name D>` — update `Template:MainPageDraw/RateUps` and `Template:MainPageDraw/RateUpsEndDate` using required rate-up and sparkable groups for the Main Page draw section.
- `/risingrotation start_date:<YYYY-MM-DD> c2:<Name> c3:<Name?> c4:<Name?> c5:<Name?> c1:<Name?> notes:<text?> week_override:<int?> start_time_override:<HH:MM?> end_date_override:<YYYY-MM-DD?> end_time_override:<HH:MM?>` — insert a new `{{RisingRotation/Row}}` at the top of `Granblue Fantasy Versus: Rising/Rotation`. The bot auto-increments the week from the current top row, defaults start to `11:00 JST`, defaults end to `start_date + 7 days` at `10:59 JST`, and supports free-form character input with autocomplete suggestions.
- `/itemupload item_type:<CDN folder (e.g. article, normal)> item_id:<cdn id> item_name:<display name>` — upload square/icon variants for a single item and create redirects (you can type any folder, but common choices include `article`, `normal`, `recycling`, `skillplus`, `evolution`, `lottery`, `npcaugment`, `set`, `ticket`, `campaign`, `npcarousal`, `memorial`).
- `/enemyupload id:<enemy id>` — upload the S and M variants for an enemy icon (produces canonical `enemy_s_<id>.png`/`enemy_m_<id>.png` plus redirects `enemy_Icon_<id>_S.png` and `enemy_Icon_<id>_M.png`).
- `/eventupload event_id:<event folder id> event_name:<display name> asset_type:<notice|start|guide|trailer_mp3|voice_banner|top|raid_thumb> max_index:<optional>` — upload event banner assets and create redirects:
  - `notice`: `<event_id>_banner_event_notice_<index>.png` + `banner_<EventName>_notice_<index>.png`
  - `start`: `<event_id>_banner_event_start_<index>.png` + `banner_<EventName>_<index>.png`
  - `guide`: checks `description_event_<index>`, `description_event_<index>_0`, and `description_event_<index>_1` under `img/sp/event/<event_id>/assets/tips/`, trying `.jpg` first and `.png` second for each suffix, continuing on subindex misses and stopping on the first missing base index, then uploads `<event_id>_description_event_<suffix>.<ext>` + `description_<EventName>_<suffix>.<ext>` and includes a copyable redirect-based `<gallery>` block in the summary
  - `trailer_mp3`: uploads `assets_en/sound/voice/<event_id>.mp3` as `<event_id>.mp3`; `event_name` remains required for command consistency but is ignored for naming and no redirect is created
  - `voice_banner`: checks `banner_event_trailer_<index>.png` first and `.jpg` second under `img/sp/banner/events/<event_id>/`, stopping on the first missing base index, then uploads `<event_id>_banner_event_trailer_<index>.<ext>` + `banner_<EventName>_trailer_<index>.<ext>`
  - `top`: uploads `img/sp/event/<event_id>/assets/teaser/event_teaser_top.jpg` as `<event_id>_top.jpg` + `<EventName>_top.jpg`
  - `raid_thumb`: `summon_qm_<event_id>_vhard.png` + `BattleRaid_<EventName>_Very_Hard.png`, `summon_qm_<event_id>_vhard_1.png` + `BattleRaid_<EventName>_Very_Hard2.png` (also `BattleRaid_<EventName>_Very_Hard_2.png`), `summon_qm_<event_id>_vhard_2.png` + `BattleRaid_<EventName>_Very_Hard3.png` (also `BattleRaid_<EventName>_Very_Hard_3.png`), `summon_qm_<event_id>_ex.png` + `BattleRaid_<EventName>_Extreme.png`, `summon_qm_<event_id>_ex_1.png` + `BattleRaid_<EventName>_Extreme2.png` (also `BattleRaid_<EventName>_Extreme_2.png`), `summon_qm_<event_id>_ex_2.png` + `BattleRaid_<EventName>_Extreme3.png` (also `BattleRaid_<EventName>_Extreme_3.png`), `summon_<event_id>_high.png` + `BattleRaid_<EventName>_Impossible.png`, `summon_qm_<event_id>_high_1.png` + `BattleRaid_<EventName>_Impossible2.png` (also `BattleRaid_<EventName>_Impossible 2.png`), `summon_qm_<event_id>_high_2.png` + `BattleRaid_<EventName>_Impossible3.png` (also `BattleRaid_<EventName>_Impossible 3.png`), `qm_<event_id>_hell.png` + `BattleRaid_<EventName>_Nightmare.png`, `quest_assets_<event_id>_free_proud.png` + `BattleRaid_<EventName>_Proud.png`, `quest_assets_<event_id>_free_proud_1.png` + `BattleRaid_<EventName>_Proud2.png` (also `BattleRaid_<EventName>_Proud_2.png`), and `quest_assets_<event_id>_free_proud_2.png` + `BattleRaid_<EventName>_Proud3.png` (also `BattleRaid_<EventName>_Proud_3.png`)
- `/synccommands` — admin-only utility to force a guild/global slash-command sync if Discord stops showing new commands.
