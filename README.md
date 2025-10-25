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

- `/imgupload page_type:<type> page_name:<title>` — bulk-upload all images for a wiki page (types: character, weapon, summon, class, skin, npc, artifact, item).
- `/statusupload status_id:<id or id#> max_iterations:<1-100>` — upload status effect icons (use `#` to iterate sequential IDs, defaults to 10 iterations).
- `/bannerupload banner_id:<campaign id> max_index:<1-50>` — iterate `banner_<id>_<index>.jpg` assets to upload gacha banner variants (default max index 12).
- `/itemupload item_type:<article|normal|recycling|skillplus|evolution|npcaugment> item_id:<cdn id> item_name:<display name>` — upload square/icon variants for a single item and create redirects.
- `/synccommands` — admin-only utility to force a guild/global slash-command sync if Discord stops showing new commands.
