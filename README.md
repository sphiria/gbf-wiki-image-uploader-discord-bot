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

Use the `/imgupload` slash command in Discord with:
- `page_type`: character, weapon, summon, class, skin, npc, artifact, or item
- `page_name`: The wiki page name to process

Use the `/statusupload` slash command to upload status icons:
- `status_id`: Base status identifier (e.g. `1438`, `status_1438`, `1438#`)
- `max_iterations`: Optional when using `#`; defaults to 10 and accepts values from 1-100

Use the `/itemupload` slash command to upload square/icon variants for a single item:
- `item_type`: One of `article`, `normal`, `recycling`, `skillplus`, `evolution`, or `npcaugment`
- `item_id`: ID from the item image URL path (e.g. `20500`)
- `item_name`: Display name used when creating redirects
