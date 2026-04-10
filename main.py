import os
import discord
from discord import app_commands
import io
import asyncio
import time
import re
import sys
from datetime import datetime, timedelta
from contextlib import redirect_stdout, redirect_stderr
from images import WikiImages

class TeeOutput:
    """Write to both original output and string buffer"""
    def __init__(self, original, buffer):
        self.original = original
        self.buffer = buffer
    
    def write(self, text):
        self.original.write(text)
        self.original.flush()
        self.buffer.write(text)
    
    def flush(self):
        self.original.flush()
        self.buffer.flush()

class DryRunWikiImages(WikiImages):
    """wrapper for dryrun"""
    
    def __init__(self):
        super().__init__()
        # Patch all wiki operations for dry-run
        self._original_upload = self.wiki.upload
        self._original_allimages = self.wiki.allimages
        
        self.wiki.upload = self._dry_run_upload
        self.wiki.allimages = self._dry_run_allimages
        
    def _dry_run_upload(self, io, filename=None, **kwargs):
        """Simulate wiki upload without actually uploading"""
        print(f"[DRY RUN] Would upload file: {filename}")
        return {'result': 'Success', 'filename': filename}
    
    def _dry_run_allimages(self, **kwargs):
        """Simulate allimages query without actually querying"""
        print(f"[DRY RUN] Would query allimages with: {list(kwargs.keys())}")
        return []  # Return empty list to simulate no duplicates found
    
    def _patch_page_save(self, page):
        """Patch a page's save method to be dry-run"""
        if not hasattr(page, '_original_save'):
            page._original_save = page.save
            page.save = lambda text, summary='', **kwargs: print(f"[DRY RUN] Would save page '{page.name}' with summary: '{summary}'")

upload_lock = asyncio.Lock() # Global lock so only one upload runs at a time
last_used = {}  # maps user_id -> timestamp of last command

# --- CONFIG ---
COOLDOWN_SECONDS = 5
MAX_PAGE_NAME_LEN = 100
MAX_ITEM_ID_LEN = 48
MAX_ITEM_NAME_LEN = 100
MAX_STATUS_ID_LEN = 64
MAX_BANNER_ID_LEN = 64
MAX_EVENT_ID_LEN = 64
MAX_ENEMY_ID_LEN = 64
MAX_CLASS_SKIN_ID_LEN = 64
MAX_RATEUP_INPUT_LEN = 1000
MAX_RISING_ROTATION_NOTES_LEN = 500
PAGE_NAME_INVALID_PATTERN = re.compile(r"[#<>\[\]\{\}\|\x00-\x1F]")
FILE_NAME_INVALID_PATTERN = re.compile(r"[#<>\[\]\{\}\|:\x00-\x1F]")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1F]")
VALID_ITEM_ID_REGEX = re.compile(r"^[\w\-]+$")
VALID_STATUS_ID_REGEX = re.compile(r"^[A-Za-z0-9_]+#?$")
VALID_BANNER_ID_REGEX = re.compile(r"^[A-Za-z0-9_]+$")
VALID_EVENT_ID_REGEX = re.compile(r"^[a-z0-9_]+$")
VALID_ENEMY_ID_REGEX = re.compile(r"^[0-9]+$")
VALID_CLASS_SKIN_ID_REGEX = re.compile(r"^[0-9]+$")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
# Comma-separated list of allowed roles, e.g. "Wiki Editor,Wiki Admin"
ALLOWED_ROLES = [r.strip() for r in os.getenv("ALLOWED_ROLES", "Wiki Editor,Wiki Admin,Wiki Discord Moderator,Verified Editor").split(",")]
# Enable dry-run mode (no actual uploads, just logging)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

# Valid page types
PAGE_TYPES = [
    "character",
    "character_fs_skin",
    "weapon",
    "summon",
    "class",
    "class_skin",
    "skin",
    "npc",
    "artifact",
    "item",
    "manatura",
    "shield",
    "skill_icons",
    "bullet",
    "advyrnture_gear",
    "advyrnture_pal",
]

# Supported single-item upload types (CDN path segments)
ITEM_TYPES = ["article", "normal", "recycling", "skillplus", "evolution", "lottery", "npcaugment", "set", "ticket", "campaign", "npcarousal", "memorial"]

EVENT_TEASER_ASSET_TYPE_CHOICES = [
    app_commands.Choice(name="notice", value="notice"),
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="guide", value="guide"),
    app_commands.Choice(name="trailer mp3", value="trailer_mp3"),
    app_commands.Choice(name="voice banner", value="voice_banner"),
    app_commands.Choice(name="top", value="top"),
    app_commands.Choice(name="raid_thumb", value="raid_thumb"),
]
EVENT_TEASER_ASSET_TYPE_SET = {choice.value for choice in EVENT_TEASER_ASSET_TYPE_CHOICES}

DRAW_MODE_CHOICES = [
    app_commands.Choice(name="single", value="single"),
    app_commands.Choice(name="double", value="double"),
    app_commands.Choice(name="element-single", value="element-single"),
    app_commands.Choice(name="element-double", value="element-double"),
]
DRAW_MODE_SET = {choice.value for choice in DRAW_MODE_CHOICES}
DRAW_COMMON_END_TIMES = ["18:59", "11:59", "23:59"]
DRAW_ELEMENT_CHOICES = [
    app_commands.Choice(name="fire", value="fire"),
    app_commands.Choice(name="water", value="water"),
    app_commands.Choice(name="earth", value="earth"),
    app_commands.Choice(name="wind", value="wind"),
    app_commands.Choice(name="light", value="light"),
    app_commands.Choice(name="dark", value="dark"),
]
DRAW_ELEMENT_ORDER = [choice.value for choice in DRAW_ELEMENT_CHOICES]
DRAW_MAX_PROBE_DEFAULT = 12
DRAW_PAGE_PROMO_MODE = "Template:MainPageDraw/PromoMode"
DRAW_PAGE_END_DATE = "Template:MainPageDraw/EndDate"
DRAW_PAGE_SUPTIX_PROMO = "Template:MainPageDraw/SuptixPromo"
DRAW_PAGE_SUPTIX_PROMO_END_DATE = "Template:MainPageDraw/SuptixPromoEndDate"
DRAW_PAGE_SINGLE = "Template:MainPageDraw/SinglePromo"
DRAW_PAGE_DOUBLE_LEFT = "Template:MainPageDraw/DoublePromoLeft"
DRAW_PAGE_DOUBLE_RIGHT = "Template:MainPageDraw/DoublePromoRight"
DRAW_PAGE_ELEMENT_BANNERS = "Template:MainPageDraw/ElementPromoBanners"
DRAW_PAGE_ELEMENT_ICONS = "Template:MainPageDraw/ElementPromoIcons"
DRAW_PAGE_RATE_UPS = "Template:MainPageDraw/RateUps"
DRAW_PAGE_RATE_UPS_END_DATE = "Template:MainPageDraw/RateUpsEndDate"
PROMO_TYPE_CHOICES = [
    app_commands.Choice(name="suptix", value="suptix"),
]
PROMO_TYPE_SET = {choice.value for choice in PROMO_TYPE_CHOICES}
MAIN_PAGE_PURGE_URL = "<https://gbf.wiki/Main_Page/purge>"
RISING_ROTATION_PAGE = "Granblue Fantasy Versus: Rising/Rotation"
RISING_ROTATION_DEFAULT_START_TIME = "11:00"
RISING_ROTATION_DEFAULT_END_TIME = "10:59"
RISING_ROTATION_CHARACTER_NAMES = [
    "Anila",
    "Avatar Belial",
    "Beatrix",
    "Belial",
    "Cagliostro",
    "Charlotta",
    "Djeeta",
    "Eustace",
    "Ferry",
    "Galleon",
    "Gran",
    "Grimnir",
    "Ilsa",
    "Katalina",
    "Ladiva",
    "Lancelot",
    "Lowain",
    "Lucilius",
    "Lunalu",
    "Metera",
    "Narmaya",
    "Percival",
    "Sandalphon",
    "Seox",
    "Siegfried",
    "Soriz",
    "Vane",
    "Vaseraga",
    "Versusia",
    "Vikala",
    "Wilnas",
    "Yuel",
    "Zooey",
    "2B",
    "Vira",
    "Avatar Belial",
]
RISING_ROTATION_AUTOCOMPLETE_NAMES = [
    name for name in sorted(set(RISING_ROTATION_CHARACTER_NAMES), key=str.casefold)
    if name not in {"All Characters", "38 Characters"}
]

HELP_COMMAND_DETAILS = {
    "help": {
        "summary": "Show an overview of slash commands or detailed help for one command.",
        "details": "\n".join([
            "**/help**",
            "Usage: `/help command:<optional>`",
            "- Purpose: Show an overview of the bot's slash commands or detailed help for one command.",
            "- Inputs:",
            "  - `command` - optional slash command name. Autocomplete suggests valid command names and filters as you type.",
            "- Output:",
            "  - No `command`: concise overview of all slash commands.",
            "  - With `command`: detailed help for that command, split into Discord-safe chunks when needed.",
        ]),
    },
    "imgupload": {
        "summary": "Bulk-upload images for a wiki page based on its page type.",
        "details": "\n".join([
            "**/imgupload**",
            "Usage: `/imgupload page_type:<type> page_name:<title> filter:<id>`",
            "- Purpose: Pull every image the upload scripts expect for a wiki page and upload them to canonical file titles.",
            "- Inputs:",
            "  - `page_type` - chooses the asset family and CDN scan rules.",
            "  - `page_name` - target wiki page title.",
            "  - `filter` - optional everywhere except `class_skin`, where it is required.",
            "- Notes: `character` supports explicit `style_id >= 2`; `character_fs_skin` handles only the heavy `f_skin` / `s_skin` families.",
            "- Output: progress plus downloaded/uploaded/duplicate/failed counts and wiki links.",
        ]),
    },
    "statusupload": {
        "summary": "Upload small and large status icons, optionally as a numbered range.",
        "details": "\n".join([
            "**/statusupload**",
            "Usage: `/statusupload status_id:<1438|status_1438|status_1438#> max_index:<1-100>`",
            "- Purpose: Upload status effect icons in bulk.",
            "- Inputs:",
            "  - `status_id` - raw id, prefixed id, or a trailing `#` to iterate sequential ids.",
            "  - `max_index` - only used with trailing `#`; default 10.",
            "- Output: processed/uploaded/failed counts and direct wiki links.",
        ]),
    },
    "bannerupload": {
        "summary": "Upload rotating gacha banner variants until the first missing index.",
        "details": "\n".join([
            "**/bannerupload**",
            "Usage: `/bannerupload banner_id:<campaign id> max_index:<1-50>`",
            "- Purpose: Upload `banner_<id>_<index>.jpg` variants from the CDN.",
            "- Inputs:",
            "  - `banner_id` - campaign slug; full `banner_<id>` input is accepted and normalized.",
            "  - `max_index` - highest suffix to try; default 12.",
            "- Output: processed/uploaded/failed counts and wiki links.",
        ]),
    },
    "drawupdate": {
        "summary": "Update MainPageDraw promotion subtemplates for single, double, or element modes.",
        "details": "\n".join([
            "**/drawupdate**",
            "Usage: `/drawupdate mode:<single|double|element-single|element-double> end_date:<YYYY-MM-DD> end_time:<HH:MM> ...`",
            "- Purpose: Update MainPageDraw promotion subtemplates without editing `Template:MainPageDraw` directly.",
            "- Inputs:",
            "  - `mode`, `end_date`, `end_time`, and `left_banner_id` are core inputs.",
            "  - `right_banner_id` is required for `double` and `element-double`.",
            "  - `end_time` and `element_start` have autocomplete suggestions.",
            "- Notes: save order is content pages, then `EndDate`, then `PromoMode`.",
            f"- Output: updated page links, resolved inputs, and purge reminder {MAIN_PAGE_PURGE_URL}.",
        ]),
    },
    "promoupdate": {
        "summary": "Update a dedicated non-draw MainPageDraw promo subtemplate.",
        "details": "\n".join([
            "**/promoupdate**",
            "Usage: `/promoupdate promo_type:<suptix> promo_id:<id> end_date:<YYYY-MM-DD> end_time:<HH:MM> link_target:<wiki page>`",
            "- Purpose: Update a supported non-draw MainPageDraw promo subtemplate without using `/drawupdate`.",
            "- Inputs:",
            "  - `promo_type` - currently only `suptix`.",
            "  - `promo_id` - accepts the bare id, `banner_<id>`, `banner_<id>.png`, or the full CDN URL and resolves it to the wiki file name.",
            "  - `end_date` and `end_time` define the JST countdown cutoff.",
            "  - `link_target` defaults to `Surprise Ticket` for `suptix`.",
            "- Notes: `/promoupdate` does not upload the asset; it assumes the file already exists on the wiki.",
            f"- Output: updated page links, resolved file name, and purge reminder {MAIN_PAGE_PURGE_URL}.",
        ]),
    },
    "rateup": {
        "summary": "Update MainPageDraw rate-up templates with pipe-separated character lists.",
        "details": "\n".join([
            "**/rateup**",
            "Usage: `/rateup end_date:<YYYY-MM-DD> end_time:<HH:MM> rateups:<A|B> sparkable:<C|D>`",
            "- Purpose: Update `Template:MainPageDraw/RateUps` and `Template:MainPageDraw/RateUpsEndDate`.",
            "- Inputs:",
            "  - `end_date` and `end_time` define the JST cutoff.",
            "  - `rateups` and `sparkable` are required pipe-separated character lists.",
            "- Notes: `/rateup` keeps its end date separate from draw banner rotation end dates.",
            f"- Output: updated page links, rendered wikitext preview, and purge reminder {MAIN_PAGE_PURGE_URL}.",
        ]),
    },
    "risingrotation": {
        "summary": "Insert a new GBVSR rotation row on the dedicated rotation subpage.",
        "details": "\n".join([
            "**/risingrotation**",
            "Usage: `/risingrotation start_date:<YYYY-MM-DD> c2:<name> c3:<name?> c4:<name?> c5:<name?> c1:<name?> notes:<text?> week_override:<int?> start_time_override:<HH:MM?> end_date_override:<YYYY-MM-DD?> end_time_override:<HH:MM?>`",
            f"- Purpose: Insert one new `{{{{RisingRotation/Row}}}}` at the top of `{RISING_ROTATION_PAGE}` without hand-editing the page.",
            "- Inputs:",
            "  - `start_date` is required and uses JST `YYYY-MM-DD` format.",
            f"  - `start_time_override` is optional; otherwise start defaults to `{RISING_ROTATION_DEFAULT_START_TIME}` JST.",
            f"  - `end` defaults to start date + 7 days at `{RISING_ROTATION_DEFAULT_END_TIME}` JST.",
            "  - `end_date_override` and `end_time_override` must be supplied together for special cases.",
            "  - `c2` is required; `c1`, `c3`, `c4`, and `c5` are optional free-form character fields with autocomplete suggestions.",
            "  - `week_override` is optional for backfills/corrections and cannot jump ahead of the next automatic week.",
            "- Notes: the command reads the current top row to compute the next week and aborts if the resolved week already exists.",
            "- Output: updated page link, resolved week/start/end values, and a copyable `wikitext` preview of the inserted row.",
        ]),
    },
    "synccommands": {
        "summary": "Force a slash-command sync when Discord falls out of sync.",
        "details": "\n".join([
            "**/synccommands**",
            "Usage: `/synccommands`",
            "- Purpose: Force-register slash commands when Discord command sync drifts.",
            "- Requirements: server-only and administrator-only.",
            "- Output: sync scope, total command count, and fallback error details when relevant.",
        ]),
    },
    "itemupload": {
        "summary": "Upload the square and icon variants for a single item id.",
        "details": "\n".join([
            "**/itemupload**",
            "Usage: `/itemupload item_type:<folder> item_id:<cdn id> item_name:<display name>`",
            "- Purpose: Upload square/icon item variants and create redirects for the supplied name.",
            "- Inputs:",
            "  - `item_type` - free-form CDN folder with autocomplete suggestions.",
            "  - `item_id` - item path fragment from the CDN URL.",
            "  - `item_name` - wiki-facing redirect name.",
            "- Output: processed/uploaded/duplicate totals and canonical/redirect links.",
        ]),
    },
    "eventupload": {
        "summary": "Upload event banners, guide panels, teaser assets, and raid thumbnails.",
        "details": "\n".join([
            "**/eventupload**",
            "Usage: `/eventupload event_id:<folder id> event_name:<display name> asset_type:<...> max_index:<optional>`",
            "- Purpose: Upload event notice/start banners, guide panels, trailer audio, trailer banners, teaser top art, and raid thumbnails.",
            "- Inputs:",
            "  - `event_id` - lowercase folder slug.",
            "  - `event_name` - display name used for redirects where applicable.",
            "  - `asset_type` - strict dropdown covering `notice`, `start`, `guide`, `trailer_mp3`, `voice_banner`, `top`, and `raid_thumb`.",
            "  - `max_index` - defaults vary by asset family; fixed-file assets use 1.",
            "- Notes: indexed families stop on the first missing base index; guide skips missing subindices; `trailer_mp3` creates no redirect.",
            "- Output: processed/uploaded/duplicate/failed counts, canonical links, and asset-specific copy blocks where applicable.",
        ]),
    },
    "enemyupload": {
        "summary": "Upload the S and M icon variants for a single enemy id.",
        "details": "\n".join([
            "**/enemyupload**",
            "Usage: `/enemyupload id:<enemy id>`",
            "- Purpose: Upload enemy S/M icons and create matching redirects.",
            "- Inputs:",
            "  - `id` - numeric CDN identifier.",
            "- Output: processed/uploaded/duplicate/failed counts and canonical/redirect links.",
        ]),
    },
}
HELP_COMMAND_CHOICES = sorted(HELP_COMMAND_DETAILS.keys())

def normalize_item_type_input(raw_value: str | None) -> str:
    """
    Normalize slash-command item type input.
    Always lower-cases and trims; returns an empty string when not provided.
    """
    return (raw_value or "").strip().lower()

def chunk_text_for_discord(content: str, limit: int = 2000) -> list[str]:
    """Split content into Discord-safe chunks, preserving line breaks when possible."""
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    current = ""

    for line in content.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]

        current = line

    if current or not chunks:
        chunks.append(current)

    return chunks

async def edit_or_followup_long_message(
    msg: discord.Message, interaction: discord.Interaction, content: str
) -> None:
    """Edit the original message, then send follow-ups if content exceeds 2000 chars."""
    chunks = chunk_text_for_discord(content, limit=2000)
    msg = await edit_public_message(msg, chunks[0] if chunks else "")
    for chunk in chunks[1:]:
        await msg.channel.send(content=chunk)

async def get_persistent_response_message(interaction: discord.Interaction) -> discord.Message:
    """Return a normal channel message when possible so edits survive interaction token expiry."""
    response_message = await interaction.original_response()
    channel = interaction.channel
    if channel is None:
        return response_message

    try:
        return await channel.fetch_message(response_message.id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return response_message

async def edit_public_message(msg: discord.Message, content: str) -> discord.Message:
    """
    Edit a public bot message.
    Falls back to a channel-fetched message if the interaction-backed message can no longer edit.
    """
    try:
        await msg.edit(content=content)
        return msg
    except (discord.NotFound, discord.HTTPException):
        channel = getattr(msg, "channel", None)
        if channel is None:
            raise

        refreshed = await channel.fetch_message(msg.id)
        await refreshed.edit(content=content)
        return refreshed

async def edit_or_followup_long_message_ephemeral(
    msg: discord.Message, interaction: discord.Interaction, content: str
) -> None:
    """Edit the original ephemeral message, then send ephemeral follow-ups if needed."""
    chunks = chunk_text_for_discord(content, limit=2000)
    await msg.edit(content=chunks[0] if chunks else "")
    for chunk in chunks[1:]:
        await interaction.followup.send(content=chunk, ephemeral=True)

def normalize_help_command_input(raw_value: str | None) -> str:
    return (raw_value or "").strip().lower().lstrip("/")

def validate_page_name(page_name: str) -> tuple[bool, str]:
    """
    Validate a wiki page name string.
    Returns (is_valid, error_message). If valid, error_message is "".
    """
    page_name = page_name.strip()

    # Length check
    if len(page_name) == 0 or len(page_name) > MAX_PAGE_NAME_LEN:
        return False, f"❌ Invalid page name. Must be between 1 and {MAX_PAGE_NAME_LEN} characters."

    # Character check
    if PAGE_NAME_INVALID_PATTERN.search(page_name):
        return False, "❌ Invalid page name. Characters #, <, >, [, ], {, }, |, or control characters are not allowed."

    return True, page_name

def validate_item_id(item_id: str) -> tuple[bool, str]:
    """
    Validate a single item upload id.
    Returns (is_valid, cleaned_value/err_message).
    """
    item_id = item_id.strip()

    if len(item_id) == 0 or len(item_id) > MAX_ITEM_ID_LEN:
        return False, f"Invalid item id. Must be between 1 and {MAX_ITEM_ID_LEN} characters."

    if not VALID_ITEM_ID_REGEX.match(item_id):
        return False, "Invalid item id. Only letters, numbers, _, and - are allowed."

    return True, item_id

def validate_item_name(item_name: str) -> tuple[bool, str]:
    """
    Validate a single item display name.
    Returns (is_valid, cleaned_value/err_message).
    """
    item_name = item_name.strip()

    if len(item_name) == 0 or len(item_name) > MAX_ITEM_NAME_LEN:
        return False, f"Invalid item name. Must be between 1 and {MAX_ITEM_NAME_LEN} characters."

    if PAGE_NAME_INVALID_PATTERN.search(item_name):
        return False, "Invalid item name. Characters #, <, >, [, ], {, }, |, or control characters are not allowed."

    return True, item_name

def validate_event_file_name(event_name: str) -> tuple[bool, str]:
    """
    Validate an event display name used for wiki file redirects.
    Returns (is_valid, cleaned_value/error_message).
    """
    event_name = event_name.strip()

    if len(event_name) == 0 or len(event_name) > MAX_ITEM_NAME_LEN:
        return False, f"Invalid event name. Must be between 1 and {MAX_ITEM_NAME_LEN} characters."

    if FILE_NAME_INVALID_PATTERN.search(event_name):
        return False, "Invalid event name. Characters #, <, >, [, ], {, }, |, :, or control characters are not allowed."

    return True, event_name

def validate_event_id(event_id: str) -> tuple[bool, str]:
    """
    Validate an event identifier used for CDN folder resolution.
    """
    event_id = (event_id or "").strip().lower()

    if len(event_id) == 0 or len(event_id) > MAX_EVENT_ID_LEN:
        return False, f"Invalid event id. Must be between 1 and {MAX_EVENT_ID_LEN} characters."

    if not VALID_EVENT_ID_REGEX.match(event_id):
        return False, "Invalid event id. Only lowercase letters, numbers, and underscores are allowed."

    return True, event_id

def validate_status_id(status_id: str) -> tuple[bool, str]:
    """
    Validate a status icon identifier.
    Returns (is_valid, cleaned_value/error_message).
    """
    status_id = status_id.strip()

    if len(status_id) == 0 or len(status_id) > MAX_STATUS_ID_LEN:
        return False, f"Invalid status id. Must be between 1 and {MAX_STATUS_ID_LEN} characters."

    if not VALID_STATUS_ID_REGEX.match(status_id):
        return False, "Invalid status id. Only letters, numbers, underscores, and an optional trailing # are allowed."

    return True, status_id

def validate_enemy_id(enemy_id: str) -> tuple[bool, str]:
    """
    Validate an enemy id for the enemy upload command.
    """
    enemy_id = (enemy_id or "").strip()

    if len(enemy_id) == 0 or len(enemy_id) > MAX_ENEMY_ID_LEN:
        return False, f"Invalid enemy id. Must be between 1 and {MAX_ENEMY_ID_LEN} digits."

    if not VALID_ENEMY_ID_REGEX.match(enemy_id):
        return False, "Invalid enemy id. Only digits are allowed."

    return True, enemy_id

def validate_class_skin_filter(filter_value: str) -> tuple[bool, str]:
    """Validate the ClassSkin filter id input."""
    filter_value = (filter_value or "").strip()

    if len(filter_value) == 0 or len(filter_value) > MAX_CLASS_SKIN_ID_LEN:
        return False, f"Invalid filter id. Must be between 1 and {MAX_CLASS_SKIN_ID_LEN} digits."

    if not VALID_CLASS_SKIN_ID_REGEX.match(filter_value):
        return False, "Invalid filter id. Only digits are allowed."

    return True, filter_value

def normalize_banner_id_input(raw_value: str | None) -> str:
    """
    Accept a bare banner id, `banner_<id>`, `banner_<id>.png`, or a full CDN URL
    and normalize it to the id portion used by banner-related commands.
    """
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return ""

    cleaned = cleaned.rstrip("/")
    cleaned = cleaned.rsplit("/", 1)[-1]

    if cleaned.lower().endswith(".png"):
        cleaned = cleaned[:-4]
    elif cleaned.lower().endswith(".jpg"):
        cleaned = cleaned[:-4]
    elif cleaned.lower().endswith(".jpeg"):
        cleaned = cleaned[:-5]

    if cleaned.lower().startswith("banner_"):
        cleaned = cleaned[7:]

    return cleaned

def validate_banner_id(banner_id: str) -> tuple[bool, str]:
    """
    Validate a gacha banner identifier (portion between `banner_` and the index).
    Returns (is_valid, cleaned_value/error_message).
    """
    banner_id = normalize_banner_id_input(banner_id)

    if len(banner_id) == 0 or len(banner_id) > MAX_BANNER_ID_LEN:
        return False, f"Invalid banner id. Must be between 1 and {MAX_BANNER_ID_LEN} characters."

    if not VALID_BANNER_ID_REGEX.match(banner_id):
        return False, "Invalid banner id. Only letters, numbers, and underscores are allowed."

    return True, banner_id

def validate_draw_end_date(end_date: str) -> tuple[bool, str]:
    """
    Validate draw update end date in strict JST date format (YYYY-MM-DD).
    """
    cleaned = (end_date or "").strip()
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError:
        return False, "Invalid end_date. Use YYYY-MM-DD in JST, e.g. 2026-03-01."
    return True, cleaned

def validate_draw_end_time(end_time: str) -> tuple[bool, str]:
    """
    Validate draw update end time in strict HH:MM 24-hour format.
    """
    cleaned = (end_time or "").strip()
    try:
        datetime.strptime(cleaned, "%H:%M")
    except ValueError:
        return False, "Invalid end_time. Use HH:MM (24-hour), e.g. 18:59."
    return True, cleaned

def validate_rising_rotation_notes(notes: str | None) -> tuple[bool, str]:
    cleaned = (notes or "").strip()
    if not cleaned:
        return True, ""
    if len(cleaned) > MAX_RISING_ROTATION_NOTES_LEN:
        return False, f"Invalid notes. Must be at most {MAX_RISING_ROTATION_NOTES_LEN} characters."
    if CONTROL_CHAR_PATTERN.search(cleaned):
        return False, "Invalid notes. Control characters are not allowed."
    return True, cleaned

def validate_rising_rotation_character(
    value: str | None,
    field_name: str,
    *,
    required: bool = False,
) -> tuple[bool, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            return False, f"`{field_name}` is required."
        return True, ""
    is_valid, result = validate_page_name(cleaned)
    if not is_valid:
        error_text = result[2:] if result.startswith("❌ ") else result
        return False, f"Invalid `{field_name}`: {error_text}"
    return True, result

def build_rising_rotation_row(
    week: int,
    start_datetime_text: str,
    end_datetime_text: str,
    character_slots: dict[str, str],
    notes: str = "",
) -> str:
    lines = [
        "{{RisingRotation/Row",
        f"|{'week':<6}= {week}",
        f"|{'start':<6}= {start_datetime_text} JST",
        f"|{'end':<6}= {end_datetime_text} JST",
    ]
    for field_name in ("c1", "c2", "c3", "c4", "c5"):
        value = character_slots.get(field_name, "").strip()
        if value:
            lines.append(f"|{field_name:<6}= {value}")
    if notes:
        lines.append(f"|{'notes':<6}= {notes}")
    lines.append("}}")
    return "\n".join(lines)

def _extract_rising_rotation_weeks(page_text: str) -> list[int]:
    weeks: list[int] = []
    for match in re.finditer(r"\{\{RisingRotation/Row\b(.*?)\}\}", page_text, re.DOTALL):
        row_text = match.group(1)
        week_match = re.search(r"^\|week\s*=\s*(\d+)\s*$", row_text, re.MULTILINE)
        if week_match:
            weeks.append(int(week_match.group(1)))
    return weeks

def get_rising_rotation_top_week(page_text: str) -> int:
    weeks = _extract_rising_rotation_weeks(page_text)
    if not weeks:
        raise ValueError(f'Could not find any `{{{{RisingRotation/Row}}}}` entries on "{RISING_ROTATION_PAGE}".')
    return weeks[0]

def insert_rising_rotation_row(page_text: str, row_text: str) -> str:
    pattern = re.compile(r"(\{\{RisingRotation\|)\s*", re.DOTALL)
    if not pattern.search(page_text):
        raise ValueError(f'Could not find the `{{{{RisingRotation|` wrapper on "{RISING_ROTATION_PAGE}".')
    return pattern.sub(
        lambda match: f"{match.group(1)}\n\n{row_text.strip()}\n\n",
        page_text,
        count=1,
    )

def validate_draw_link_target(link_target: str) -> tuple[bool, str]:
    """
    Validate the wiki link target used in generated File links.
    """
    cleaned = (link_target or "").strip()
    if len(cleaned) == 0 or len(cleaned) > MAX_PAGE_NAME_LEN:
        return False, f"Invalid link target. Must be between 1 and {MAX_PAGE_NAME_LEN} characters."
    if PAGE_NAME_INVALID_PATTERN.search(cleaned):
        return False, "Invalid link target. Characters #, <, >, [, ], {, }, |, or control characters are not allowed."
    return True, cleaned

def validate_pipe_separated_page_names(
    raw_value: str | None,
    field_name: str,
) -> tuple[bool, list[str] | str]:
    """
    Validate a pipe-separated list of wiki-facing names.
    Returns (is_valid, parsed_list/error_message).
    Empty input is allowed and returns an empty list.
    """
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return True, []
    if len(cleaned) > MAX_RATEUP_INPUT_LEN:
        return False, f"Invalid {field_name}. Must be at most {MAX_RATEUP_INPUT_LEN} characters."

    parts = [part.strip() for part in cleaned.split("|")]
    if any(not part for part in parts):
        return False, f"Invalid {field_name}. Empty entries are not allowed; use `Name A|Name B|Name C`."

    parsed_names: list[str] = []
    for index, part in enumerate(parts, start=1):
        is_valid_name, name_result = validate_page_name(part)
        if not is_valid_name:
            error_text = name_result[2:] if name_result.startswith("❌ ") else name_result
            return False, f"Invalid {field_name} entry #{index}: {error_text}"
        parsed_names.append(name_result)

    return True, parsed_names

def build_draw_gallery_swap_images(file_names: list[str], link_target: str) -> str:
    """
    Build GallerySwapImages wikitext for 230x110 draw banners.
    """
    lines = ["{{GallerySwapImages|w=230|h=110"]
    for file_name in file_names:
        lines.append(f"|[[File:{file_name}|230px|link={link_target}]]")
    lines.append("}}")
    return "\n".join(lines)

def build_character_icons_template(character_names: list[str]) -> str:
    if not character_names:
        raise ValueError("At least one character name is required to build CharacterIcons.")
    return "{{CharacterIcons|format=square|size=50px|tooltip=yes|" + "|".join(character_names) + "}}"

def build_rateup_content(rateup_names: list[str], sparkable_names: list[str]) -> str:
    """
    Build MainPageDraw rate-up section wikitext.
    The section renders up to two groups:
    - draw rate-up characters
    - sparkable characters
    """
    if not rateup_names and not sparkable_names:
        raise ValueError("At least one of rateups or sparkable must contain a character.")

    inner_lines = ['<div style="max-width: 470px; display:flex; justify-content: center;">']
    if rateup_names and sparkable_names:
        inner_lines.extend([
            '<div style="margin-right:10px;">',
            f"{build_character_icons_template(rateup_names)}<br />",
            "{{icon|drawrateup|size=100}}",
            "</div>",
            "<div>",
            f"{build_character_icons_template(sparkable_names)}<br />",
            "{{label|sparkable|size=73|link=Spark}}",
            "</div>",
        ])
    elif rateup_names:
        inner_lines.extend([
            f"{build_character_icons_template(rateup_names)}<br />",
            "{{icon|drawrateup|size=100}}",
        ])
    else:
        inner_lines.extend([
            f"{build_character_icons_template(sparkable_names)}<br />",
            "{{label|sparkable|size=73|link=Spark}}",
        ])
    inner_lines.append("</div>")

    return (
        "{{ScheduledContent|end_time={{MainPageDraw/RateUpsEndDate}} JST|content=\n"
        + "\n".join(inner_lines)
        + "}}"
    )

def build_suptix_promo_content(promo_id: str, end_datetime_text: str, link_target: str) -> str:
    file_name = f"banner_{promo_id}.png"
    return "\n".join([
        '{{ScheduledContent|end_time={{MainPageDraw/SuptixPromoEndDate}} JST|content=',
        '<div style="max-width:470px;">',
        '{{GallerySwapImages|w=450|h=110',
        f'|[[File:{file_name}|450px|link={link_target}]]',
        '}}',
        '{{EventCountdown|{{MainPageDraw/SuptixPromoEndDate}} JST|text_after=This banner promotion has ended.}}',
        '</div>}}',
    ])

def _format_jst_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M JST")

def build_draw_element_mode_content(
    left_files: list[str],
    end_datetime: datetime,
    link_target: str,
    start_element: str,
    right_files: list[str] | None = None,
) -> tuple[str, str]:
    """
    Build element-mode banner schedule and icon schedule wikitext.
    For element-single, day content uses left slug banner pairs (1,2), (3,4), etc.
    For element-double, each side uses its own slug pairs and renders in a left/right layout.
    Counts may differ, but at least one side must provide 12 banners.
    """
    if not left_files:
        raise ValueError("Element mode requires at least 1 banner.")

    is_double = right_files is not None
    if is_double:
        if not right_files:
            raise ValueError("Element-double mode requires at least 1 banner on each side.")
        if len(left_files) != 12 and len(right_files) != 12:
            raise ValueError(
                f"Element-double mode requires at least one side to have 12 banners. Left={len(left_files)}, Right={len(right_files)}."
            )

    if start_element not in DRAW_ELEMENT_ORDER:
        raise ValueError("Invalid start element for element mode.")

    def pair_count(files: list[str]) -> int:
        return (len(files) + 1) // 2

    def pair_for_day(files: list[str], day_index: int) -> list[str]:
        first_index = day_index * 2
        if first_index >= len(files):
            return [files[-1], files[-1]]
        second_index = min(first_index + 1, len(files) - 1)
        return [files[first_index], files[second_index]]

    left_days = pair_count(left_files)
    right_days = pair_count(right_files) if right_files else 0
    element_count = max(left_days, right_days if is_double else 0)
    # Element windows begin at end_time+1 minute and step backward one day per element.
    first_start = (end_datetime + timedelta(minutes=1)) - timedelta(days=element_count)
    slot_starts = [first_start + timedelta(days=i) for i in range(element_count)]
    slot_ends = [start + timedelta(days=1) - timedelta(minutes=1) for start in slot_starts]

    start_index = DRAW_ELEMENT_ORDER.index(start_element)
    ordered_elements = [
        DRAW_ELEMENT_ORDER[(start_index + idx) % len(DRAW_ELEMENT_ORDER)]
        for idx in range(element_count)
    ]

    # Banner content: initial day always visible, then swap daily with ScheduledContent.
    day_contents: list[str] = []
    for idx in range(element_count):
        left_pair = pair_for_day(left_files, idx)
        if is_double:
            right_pair = pair_for_day(right_files, idx)
            day_content = (
                '<div style="max-width: 230px; width:100%;">\n'
                f'{build_draw_gallery_swap_images(left_pair, link_target)}\n'
                '</div>\n'
                '<div style="max-width: 230px; width:100%;">\n'
                f'{build_draw_gallery_swap_images(right_pair, link_target)}\n'
                '</div>\n'
            )
        else:
            day_content = build_draw_gallery_swap_images(left_pair, link_target)
        day_contents.append(day_content)

    # Every day is wrapped in ScheduledContent to prevent overlaps.
    banner_lines: list[str] = []
    for idx in range(element_count):
        start_text = _format_jst_datetime(slot_starts[idx])
        end_text = _format_jst_datetime(slot_ends[idx])
        if idx == element_count - 1:
            end_text = f"{end_text} + 3 days"
        banner_lines.append(
            "{{ScheduledContent|"
            + f"{start_text}|{end_text}|content={day_contents[idx]}"
            + "}}"
        )

    # Icon content: active element at 36px, others at 20px.
    icon_lines = ["Element changes every day as follows:<br />"]
    for idx, element in enumerate(ordered_elements):
        start_text = _format_jst_datetime(slot_starts[idx])
        if idx < element_count - 1:
            end_text = _format_jst_datetime(slot_ends[idx])
            scheduled_size = f"{{{{ScheduledContent|{start_text}|{end_text}|36|20}}}}"
            icon_lines.append(f"{{{{Icon|{element}|size={scheduled_size}}}}}")
        else:
            scheduled_size = f"{{{{ScheduledContent|{start_text}|content=36|alt_content=20}}}}"
            icon_lines.append(f"{{{{Icon|{element}|size={scheduled_size}}}}}")

    # Prevent whitespace/newline rendering gaps between scheduled blocks.
    banner_text = "<!--\n-->".join(banner_lines)
    if is_double:
        banner_text = (
            '<div class="double-promotion" style="max-width: 470px; display:flex; justify-content: space-between;">\n'
            f"{banner_text}\n"
            "</div>"
        )
    return banner_text, "\n".join(icon_lines)

def _file_exists_or_redirects_to_file(site, file_name: str) -> bool:
    """
    Check if a file title exists or redirects to a real file page with imageinfo.
    """
    title = f"File:{file_name}"
    result = site.api(
        "query",
        titles=title,
        redirects=1,
        prop="info|imageinfo",
        iiprop="timestamp",
    )
    pages = result.get("query", {}).get("pages", {})
    if not pages:
        return False

    page = next(iter(pages.values()))
    if "missing" in page:
        return False
    imageinfo = page.get("imageinfo")
    return bool(imageinfo)

def _resolve_draw_file_list(site, banner_id: str, count: int | None, max_probe: int) -> list[str]:
    """
    Resolve draw file list either by explicit count or probing until first miss.
    """
    found_files: list[str] = []
    if count is not None:
        for index in range(1, count + 1):
            file_name = f"banner_{banner_id}_{index}.png"
            if not _file_exists_or_redirects_to_file(site, file_name):
                raise ValueError(
                    f'Missing banner at index {index} for "{banner_id}" while validating required contiguous range 1-{count}.'
                )
            found_files.append(file_name)
        return found_files

    for index in range(1, max_probe + 1):
        file_name = f"banner_{banner_id}_{index}.png"
        if not _file_exists_or_redirects_to_file(site, file_name):
            break
        found_files.append(file_name)

    if not found_files:
        raise ValueError(f'No banners found for "{banner_id}" (missing banner_{banner_id}_1.png).')
    return found_files

async def run_draw_update(
    mode: str,
    end_datetime_text: str,
    left_banner_id: str,
    right_banner_id: str | None,
    left_count: int | None,
    right_count: int | None,
    max_probe: int,
    link_target: str,
    element_start: str,
    status: dict | None = None,
) -> tuple[int, str, str]:
    """
    Update MainPageDraw draw subtemplates in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def update_status(stage: str, **kwargs):
        if status is not None:
            status.update({"stage": stage, **kwargs})

    def upload_task():
        try:
            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            site = wi.wiki

            update_status("resolving_files")
            left_files = _resolve_draw_file_list(site, left_banner_id, left_count, max_probe)
            right_files: list[str] = []
            if mode in ("double", "element-double") and right_banner_id:
                right_files = _resolve_draw_file_list(site, right_banner_id, right_count, max_probe)

            page_updates: list[tuple[str, str]] = []
            if mode == "single":
                page_updates.append(
                    (DRAW_PAGE_SINGLE, build_draw_gallery_swap_images(left_files, link_target))
                )
            elif mode == "double":
                page_updates.append(
                    (DRAW_PAGE_DOUBLE_LEFT, build_draw_gallery_swap_images(left_files, link_target))
                )
                page_updates.append(
                    (DRAW_PAGE_DOUBLE_RIGHT, build_draw_gallery_swap_images(right_files, link_target))
                )
            elif mode == "element-single":
                end_datetime = datetime.strptime(end_datetime_text, "%Y-%m-%d %H:%M")
                element_banners, element_icons = build_draw_element_mode_content(
                    left_files,
                    end_datetime,
                    link_target,
                    element_start,
                )
                page_updates.append((DRAW_PAGE_ELEMENT_BANNERS, element_banners))
                page_updates.append((DRAW_PAGE_ELEMENT_ICONS, element_icons))
            elif mode == "element-double":
                end_datetime = datetime.strptime(end_datetime_text, "%Y-%m-%d %H:%M")
                element_banners, element_icons = build_draw_element_mode_content(
                    left_files,
                    end_datetime,
                    link_target,
                    element_start,
                    right_files=right_files,
                )
                page_updates.append((DRAW_PAGE_ELEMENT_BANNERS, element_banners))
                page_updates.append((DRAW_PAGE_ELEMENT_ICONS, element_icons))
            else:
                raise ValueError(f"Unsupported draw mode: {mode}")

            promo_mode_value = "element" if mode in ("element-single", "element-double") else mode

            # Safety order: content first, then end date, then mode switch.
            page_updates.append((DRAW_PAGE_END_DATE, end_datetime_text))
            page_updates.append((DRAW_PAGE_PROMO_MODE, promo_mode_value))

            update_status(
                "saving_pages",
                left_files=left_files,
                right_files=right_files,
                pages=[title for title, _ in page_updates],
            )

            saved_pages: list[str] = []
            for page_title, page_text in page_updates:
                page = site.pages[page_title]
                if DRY_RUN and hasattr(wi, "_patch_page_save"):
                    wi._patch_page_save(page)
                if page_title == DRAW_PAGE_END_DATE:
                    save_summary = f"Bot: update MainPageDraw EndDate to {end_datetime_text} JST"
                elif page_title == DRAW_PAGE_PROMO_MODE:
                    save_summary = f"Bot: update MainPageDraw PromoMode to {promo_mode_value}"
                else:
                    save_summary = "Bot: update MainPageDraw draw promotion"
                page.save(page_text, summary=save_summary, minor=False, bot=True)
                saved_pages.append(page_title)

            update_status(
                "completed",
                saved_pages=saved_pages,
                left_files=left_files,
                right_files=right_files,
            )
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(
            f"Starting draw update (mode: {mode}, left: {left_banner_id}, right: {right_banner_id}, "
            f"max_probe: {max_probe}, element_start: {element_start})"
        )
        if DRY_RUN:
            print("DRY RUN MODE - No actual saves will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as exc:
        error_msg = f"Draw update task failed: {exc}"
        print(error_msg)
        return 1, "", str(exc)

async def run_rateup_update(
    end_datetime_text: str,
    rateup_names: list[str],
    sparkable_names: list[str],
    status: dict | None = None,
) -> tuple[int, str, str]:
    """
    Update MainPageDraw rate-up subtemplates in a thread.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def update_status(stage: str, **kwargs):
        if status is not None:
            status.update({"stage": stage, **kwargs})

    def upload_task():
        try:
            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            site = wi.wiki

            rateup_text = build_rateup_content(rateup_names, sparkable_names)
            page_updates = [
                (DRAW_PAGE_RATE_UPS, rateup_text),
                (DRAW_PAGE_RATE_UPS_END_DATE, end_datetime_text),
            ]

            update_status(
                "saving_pages",
                pages=[title for title, _ in page_updates],
            )

            saved_pages: list[str] = []
            for page_title, page_text in page_updates:
                page = site.pages[page_title]
                if DRY_RUN and hasattr(wi, "_patch_page_save"):
                    wi._patch_page_save(page)
                if page_title == DRAW_PAGE_RATE_UPS_END_DATE:
                    save_summary = f"Bot: update MainPageDraw RateUpsEndDate to {end_datetime_text} JST"
                else:
                    save_summary = "Bot: update MainPageDraw rate ups"
                page.save(page_text, summary=save_summary, minor=False, bot=True)
                saved_pages.append(page_title)

            update_status(
                "completed",
                saved_pages=saved_pages,
                rateups=rateup_names,
                sparkable=sparkable_names,
            )
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(
            f"Starting MainPageDraw rate-up update "
            f"(rateups: {len(rateup_names)}, sparkable: {len(sparkable_names)}, end: {end_datetime_text})"
        )
        if DRY_RUN:
            print("DRY RUN MODE - No actual saves will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as exc:
        error_msg = f"Rate-up update task failed: {exc}"
        print(error_msg)
        return 1, "", str(exc)

async def run_promo_update(
    promo_type: str,
    promo_id: str,
    end_datetime_text: str,
    link_target: str,
    status: dict | None = None,
) -> tuple[int, str, str]:
    """
    Update a supported MainPageDraw promo subtemplate without uploading assets.
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def update_status(stage: str, **kwargs):
        if status is not None:
            status.update({"stage": stage, **kwargs})

    def upload_task():
        try:
            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            site = wi.wiki

            if promo_type == "suptix":
                resolved_file_name = f"banner_{promo_id}.png"
                update_status(
                    "validating_file",
                    resolved_file_name=resolved_file_name,
                )
                if not _file_exists_or_redirects_to_file(site, resolved_file_name):
                    raise ValueError(
                        f'No wiki file found for "{resolved_file_name}". '
                        "Upload the file first before using /promoupdate."
                    )
                page_updates = [
                    (
                        DRAW_PAGE_SUPTIX_PROMO,
                        build_suptix_promo_content(promo_id, end_datetime_text, link_target),
                    ),
                    (
                        DRAW_PAGE_SUPTIX_PROMO_END_DATE,
                        end_datetime_text,
                    ),
                ]
            else:
                raise ValueError(f"Unsupported promo type: {promo_type}")

            update_status(
                "saving_pages",
                pages=[title for title, _ in page_updates],
                resolved_file_name=resolved_file_name,
            )

            saved_pages: list[str] = []
            for page_title, page_text in page_updates:
                page = site.pages[page_title]
                if DRY_RUN and hasattr(wi, "_patch_page_save"):
                    wi._patch_page_save(page)
                if page_title == DRAW_PAGE_SUPTIX_PROMO_END_DATE:
                    save_summary = f"Bot: update MainPageDraw SuptixPromoEndDate to {end_datetime_text} JST"
                else:
                    save_summary = f"Bot: update MainPageDraw {promo_type} promo"
                page.save(
                    page_text,
                    summary=save_summary,
                    minor=False,
                    bot=True,
                )
                saved_pages.append(page_title)

            update_status(
                "completed",
                saved_pages=saved_pages,
                resolved_file_name=resolved_file_name,
            )
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(
            f"Starting promo update (type: {promo_type}, promo id: {promo_id}, end: {end_datetime_text})"
        )
        if DRY_RUN:
            print("DRY RUN MODE - No actual saves will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as exc:
        error_msg = f"Promo update task failed: {exc}"
        print(error_msg)
        return 1, "", str(exc)

async def run_rising_rotation_update(
    start_datetime_text: str,
    end_datetime_text: str,
    character_slots: dict[str, str],
    notes: str,
    week_override: int | None,
    status: dict | None = None,
) -> tuple[int, str, str]:
    """
    Insert a new row into the GBVSR rotation page.
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def update_status(stage: str, **kwargs):
        if status is not None:
            status.update({"stage": stage, **kwargs})

    def upload_task():
        try:
            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            site = wi.wiki
            page = site.pages[RISING_ROTATION_PAGE]

            update_status("loading_page", page=RISING_ROTATION_PAGE)
            page_text = page.text()

            existing_weeks = _extract_rising_rotation_weeks(page_text)
            if not existing_weeks:
                raise ValueError(
                    f'Could not determine the current top week from "{RISING_ROTATION_PAGE}".'
                )

            top_week = existing_weeks[0]
            next_auto_week = top_week + 1
            resolved_week = next_auto_week
            week_source = "auto"
            if week_override is not None:
                if week_override > next_auto_week:
                    raise ValueError(
                        f"`week_override` cannot jump ahead of the next automatic week "
                        f"({next_auto_week})."
                    )
                resolved_week = week_override
                week_source = "override"

            if resolved_week in existing_weeks:
                raise ValueError(
                    f'Week {resolved_week} already exists on "{RISING_ROTATION_PAGE}".'
                )

            row_text = build_rising_rotation_row(
                resolved_week,
                start_datetime_text,
                end_datetime_text,
                character_slots,
                notes,
            )
            updated_page_text = insert_rising_rotation_row(page_text, row_text)

            update_status(
                "saving_page",
                page=RISING_ROTATION_PAGE,
                resolved_week=resolved_week,
                week_source=week_source,
                row_text=row_text,
                start_datetime_text=start_datetime_text,
                end_datetime_text=end_datetime_text,
            )

            if DRY_RUN and hasattr(wi, "_patch_page_save"):
                wi._patch_page_save(page)
            page.save(
                updated_page_text,
                summary=f"Bot: add GBVSR rotation week {resolved_week}",
                minor=False,
                bot=True,
            )

            update_status(
                "completed",
                page=RISING_ROTATION_PAGE,
                resolved_week=resolved_week,
                week_source=week_source,
                row_text=row_text,
                start_datetime_text=start_datetime_text,
                end_datetime_text=end_datetime_text,
            )
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(
            f"Starting GBVSR rotation update (start: {start_datetime_text}, end: {end_datetime_text}, "
            f"week_override: {week_override})"
        )
        if DRY_RUN:
            print("DRY RUN MODE - No actual saves will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as exc:
        error_msg = f"Rising rotation update task failed: {exc}"
        print(error_msg)
        return 1, "", str(exc)

async def run_wiki_upload(
    page_type: str,
    page_name: str,
    status: dict = None,
    filter_value: str | None = None,
) -> tuple[int, str, str]:
    """
    Run wiki image upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    
    def upload_task():
        try:
            if status:
                status["stage"] = "initializing"
                
            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            # Keep the Discord bot on its historical pacing. Local CLI runs use
            # images.py env-driven safeguards, but the bot should stay at the
            # established 5s delay so slash-command uploads do not become
            # noticeably slower in normal operation.
            wi.delay = 5
            
            page = wi.wiki.pages[page_name]
            
            if DRY_RUN and hasattr(wi, '_patch_page_save'):
                wi._patch_page_save(page)
                
            if status:
                status["stage"] = "downloading"
            
            # Set status callback for progress updates
            if status:
                def update_status(stage, **kwargs):
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None
            
            if page_type == 'character':
                wi.check_character(page)
            elif page_type == 'character_full':
                wi.check_character_full(page)
            elif page_type == 'character_fs_skin':
                wi.check_character_fs_skin(page)
            elif page_type == 'weapon':
                wi.check_weapon(page)
            elif page_type == 'summon':
                wi.check_summon(page)
            elif page_type == 'class':
                wi.check_class(page)
            elif page_type == 'class_skin':
                if not filter_value:
                    raise ValueError("class_skin uploads require a filter id")
                wi.check_class_skin(page, filter_value)
            elif page_type == 'skin':
                wi.check_skin(page)
            elif page_type == 'npc':
                wi.check_npc(page)
            elif page_type == 'item':
                wi.upload_item_article_images(page)
            elif page_type == 'artifact':
                wi.check_artifact(page)
            elif page_type == 'manatura':
                wi.check_manatura(page)
            elif page_type == 'shield':
                wi.check_shield(page)
            elif page_type == 'skill_icons':
                wi.check_skill_icons(page)
            elif page_type == 'bullet':
                wi.check_bullet(page)
            elif page_type == 'advyrnture_gear':
                wi.check_advyrnture_gear(page)
            elif page_type == 'advyrnture_pal':
                wi.check_advyrnture_pal(page)
            else:
                raise ValueError(f"Unknown page type: {page_type}")
                
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    
    try:
        # Create tee outputs that write to both console and buffer
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)
        
        # Add some console logging
        print(f"🚀 Starting upload task for {page_type}: {page_name}")
        if DRY_RUN:
            print("🧪 DRY RUN MODE - No actual uploads will be performed")
        
        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)
        
        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"❌ Upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

async def run_item_upload(item_type: str, item_id: str, item_name: str, status: dict = None) -> tuple[int, str, str]:
    """
    Run single item image upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def upload_task():
        try:
            if status:
                status["stage"] = "initializing"

            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            wi.delay = 5

            if status:
                def update_status(stage, **kwargs):
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None

            if status:
                status["item_type"] = item_type
                status["stage"] = "processing"

            wi.upload_single_item_images(item_type, item_id, item_name)
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(f"Starting single item upload for {item_name} (type: {item_type}, ID: {item_id})")
        if DRY_RUN:
            print("DRY RUN MODE - No actual uploads will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"Single item upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

async def run_event_upload(
    event_id: str,
    event_name: str,
    asset_type: str,
    max_index: int,
    status: dict | None = None,
) -> tuple[int, str, str]:
    """
    Run event asset upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def upload_task():
        try:
            if status is not None:
                status.setdefault("stage", "initializing")
                status.setdefault("processed", 0)
                status.setdefault("uploaded", 0)
                status.setdefault("duplicates", 0)
                status.setdefault("failed", 0)
                status.setdefault("total_urls", 0)
                status.setdefault("files", [])
                status.setdefault("asset_type", asset_type)

            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            wi.delay = 5

            if status is not None:
                def update_status(stage, **kwargs):
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None

            result = wi.upload_event_assets(event_id, event_name, asset_type, max_index)
            if status is not None:
                status.update(result)
                status["stage"] = "completed"
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(
            f"Starting event upload for {event_name} (event id: {event_id}, asset type: {asset_type})"
        )
        if DRY_RUN:
            print("DRY RUN MODE - No actual uploads will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"Event upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

async def run_enemy_upload(enemy_id: str, status: dict | None = None) -> tuple[int, str, str]:
    """
    Run enemy icon upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def upload_task():
        try:
            if status is not None:
                status.setdefault("stage", "initializing")
                status.setdefault("processed", 0)
                status.setdefault("uploaded", 0)
                status.setdefault("duplicates", 0)
                status.setdefault("failed", 0)
                status.setdefault("total", 2)
                status.setdefault("files", [])

            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            wi.delay = 5

            if status is not None:
                def update_status(stage, **kwargs):
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None

            result = wi.upload_enemy_images(enemy_id)
            if status is not None:
                status.update(result)
                status["stage"] = "completed"
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(f"Starting enemy upload for id {enemy_id}")
        if DRY_RUN:
            print("DRY RUN MODE - No actual uploads will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"Enemy upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

async def run_status_upload(status_identifier: str, max_index: int | None, status: dict = None) -> tuple[int, str, str]:
    """
    Run status icon upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def upload_task():
        try:
            if status is not None:
                status.setdefault("status_id", status_identifier)
                status.setdefault("processed", 0)
                status.setdefault("uploaded", 0)
                status.setdefault("failed", 0)
                status.setdefault("total", 1 if max_index is None else max_index + 1)
                status.setdefault("downloaded_files", [])
                status["stage"] = "initializing"

            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            wi.delay = 5

            if status is not None:
                def update_status(stage, **kwargs):
                    downloaded_file = kwargs.pop("downloaded_file", None)
                    if downloaded_file:
                        status.setdefault("downloaded_files", []).append(downloaded_file)
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None

            wi.upload_status_icons(status_identifier, max_index)

            if status is not None and status.get("stage") != "completed":
                status["stage"] = "completed"

            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(f"Starting status upload for {status_identifier} (max index: {max_index})")
        if DRY_RUN:
            print("DRY RUN MODE - No actual uploads will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"Status upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

async def run_banner_upload(banner_identifier: str, max_index: int, status: dict = None) -> tuple[int, str, str]:
    """
    Run gacha banner upload in a thread and capture stdout/stderr.
    Returns (return_code, stdout, stderr)
    """
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    def upload_task():
        try:
            if status is not None:
                status.setdefault("banner_id", banner_identifier)
                status.setdefault("processed", 0)
                status.setdefault("uploaded", 0)
                status.setdefault("failed", 0)
                status.setdefault("total", max_index)
                status.setdefault("downloaded_files", [])
                status["stage"] = "initializing"

            wi = DryRunWikiImages() if DRY_RUN else WikiImages()
            wi.delay = 5

            if status is not None:
                def update_status(stage, **kwargs):
                    downloaded_file = kwargs.pop("downloaded_file", None)
                    if downloaded_file:
                        status.setdefault("downloaded_files", []).append(downloaded_file)
                    status.update({"stage": stage, **kwargs})
                wi._status_callback = update_status
            else:
                wi._status_callback = lambda stage, **kwargs: None

            wi.upload_gacha_banners(banner_identifier, max_index)

            if status is not None and status.get("stage") != "completed":
                status["stage"] = "completed"

            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        tee_stdout = TeeOutput(sys.stdout, stdout_buffer)
        tee_stderr = TeeOutput(sys.stderr, stderr_buffer)

        print(f"Starting banner upload for {banner_identifier} (max index: {max_index})")
        if DRY_RUN:
            print("DRY RUN MODE - No actual uploads will be performed")

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            return_code = await asyncio.to_thread(upload_task)

        return return_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()
    except Exception as e:
        error_msg = f"Gacha banner upload task failed: {e}"
        print(error_msg)
        return 1, "", str(e)

# --- BOT SETUP ---
class WikiBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._sync_lock: asyncio.Lock | None = None
        self._commands_synced = False
        self._last_sync_scope = "unsynced"

    async def setup_hook(self):
        await self.sync_app_commands(initial=True, force=True)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        if not self._commands_synced:
            await self.sync_app_commands()

    async def sync_app_commands(self, *, initial: bool = False, force: bool = False) -> dict:
        if self._sync_lock is None:
            self._sync_lock = asyncio.Lock()

        async with self._sync_lock:
            if self._commands_synced and not force:
                return {
                    "scope": self._last_sync_scope,
                    "count": len(self.tree.get_commands()),
                    "status": "already_synced",
                    "error": None,
                }

            guild = discord.Object(id=GUILD_ID)
            if initial:
                # Copy all global commands to the guild for faster propagation
                self.tree.copy_global_to(guild=guild)

            last_error = None
            try:
                commands = await self.tree.sync(guild=guild)
                self._commands_synced = True
                self._last_sync_scope = "guild"
                print(f"Synced {len(commands)} commands to guild {GUILD_ID}")
                return {
                    "scope": "guild",
                    "count": len(commands),
                    "status": "synced",
                    "error": None,
                }
            except Exception as exc:
                last_error = exc
                print(f"Guild slash-command sync failed: {exc}")

            print("Attempting global slash-command sync fallback...")
            try:
                commands = await self.tree.sync()
                self._commands_synced = True
                self._last_sync_scope = "global"
                print(f"Fallback global sync succeeded with {len(commands)} commands")
                return {
                    "scope": "global",
                    "count": len(commands),
                    "status": "fallback_synced",
                    "error": last_error,
                }
            except Exception as final_exc:
                self._commands_synced = False
                print(f"Global slash-command sync failed: {final_exc}")
                raise


bot = WikiBot()


# --- SLASH COMMAND ---
from discord import app_commands

@bot.tree.command(
    name="imgupload",
    description="Upload a page's images to the wiki",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)  # only allow your roles
@app_commands.describe(
    page_type="Type of page",
    page_name="Wiki page name",
    page_filter="Additional filter parameter (required for class skin uploads)",
)
@app_commands.rename(page_filter="filter")
@app_commands.choices(page_type=[app_commands.Choice(name=pt, value=pt) for pt in PAGE_TYPES])
async def upload(
    interaction: discord.Interaction,
    page_type: app_commands.Choice[str],
    page_name: str,
    page_filter: str | None = None,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"❌ You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True
        )
        return
    
    is_valid, result = validate_page_name(page_name)
    if not is_valid:
        await interaction.response.send_message(result, ephemeral=True)
        return
    page_name = result  # validated + stripped

    class_skin_filter = None
    if page_type.value == "class_skin":
        is_valid_filter, filter_result = validate_class_skin_filter(page_filter or "")
        if not is_valid_filter:
            await interaction.response.send_message(filter_result, ephemeral=True)
            return
        class_skin_filter = filter_result

    display_target = (
        f"{page_name} [filter: {class_skin_filter}]"
        if class_skin_filter
        else page_name
    )

    # Check cooldown
    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"⚠️ You must wait {remaining}s before using `/upload` again.",
            ephemeral=True
        )
        return
    last_used[interaction.user.id] = now

    # Ensure only one upload runs at a time
    if upload_lock.locked():
        await interaction.response.send_message(
            "⚠️ Another upload is already running. Please wait until it finishes.",
            ephemeral=True
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Upload started for `{display_target}` ({page_type.value}). This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()

        status = {"stage": "starting", "details": ""}
        
        # define updater function here
        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                
                if status["stage"] == "downloading":
                    content = f"{dry_run_prefix}Downloading images for `{display_target}` ({page_type.value})... ({elapsed}s elapsed)"
                elif status["stage"] == "processing":
                    processed = status.get("processed", 0)
                    total = status.get("total", 0)
                    current_image = status.get("current_image", "")
                    content = f"{dry_run_prefix}Processing {processed}/{total} images for `{display_target}` ({page_type.value}). Current: {current_image} ({elapsed}s elapsed)"
                elif status["stage"] == "downloaded":
                    successful = status.get("successful", 0)
                    failed = status.get("failed", 0)
                    content = f"{dry_run_prefix}Downloaded {successful} images, {failed} failed for `{display_target}` ({page_type.value}). Starting processing... ({elapsed}s elapsed)"
                else:
                    content = f"{dry_run_prefix}Upload for `{display_target}` ({page_type.value}) still running... ({elapsed}s elapsed)"
                
                msg = await edit_public_message(msg, content)

        # start the updater task
        updater_task = asyncio.create_task(progress_updater())

        filter_arg = class_skin_filter if page_type.value == "class_skin" else None
        return_code, stdout, stderr = await run_wiki_upload(
            page_type.value,
            page_name,
            status,
            filter_value=filter_arg,
        )
        
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            # Create summary from final status
            downloaded = status.get("successful", 0)
            processed = status.get("processed", 0)
            uploaded = status.get("uploaded", 0)
            duplicates = status.get("duplicates", 0)
            failed = status.get("failed", 0)
            total_checked = status.get("total_urls", 0)
            
            summary = f"{dry_run_prefix}Upload completed for `{display_target}` ({page_type.value}) in {elapsed}s!\n"
            summary += f"**Summary:**\n"
            summary += f"• Images downloaded: {downloaded}\n"
            summary += f"• Images uploaded: {uploaded}\n"
            summary += f"• Images found as duplicates: {duplicates}\n"
            summary += f"• Images processed: {processed}\n" 
            summary += f"• Download failures: {failed}\n"
            summary += f"• Total URLs checked: {total_checked}"
            
            msg = await edit_public_message(msg, summary)
        else:
            msg = await edit_public_message(msg, f"{dry_run_prefix}Upload failed for `{display_target}` ({page_type.value}) in {elapsed}s!")
            # Show error details in Discord if there were errors
            if stderr.strip():
                error_preview = stderr.strip()[:500]  # First 500 chars
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@bot.tree.command(
    name="statusupload",
    description="Upload status icon variants to the wiki",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    status_id="Status identifier (e.g. 1438, status_1438, 1438#)",
    max_index="Maximum index when using # (1-100, defaults to 10)",
)
async def statusupload(
    interaction: discord.Interaction,
    status_id: str,
    max_index: app_commands.Range[int, 1, 100] = 10,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_status, cleaned_status_id = validate_status_id(status_id)
    if not is_valid_status:
        await interaction.response.send_message(cleaned_status_id, ephemeral=True)
        return

    ranged = cleaned_status_id.endswith("#")
    max_index_value = max_index if ranged else None
    total_expected = (max_index_value + 1) if ranged else 1

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/statusupload` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        range_text = f" (up to {max_index_value} icons)" if ranged else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Status upload started for `{cleaned_status_id}`{range_text}. This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "status_id": cleaned_status_id,
            "processed": 0,
            "uploaded": 0,
            "failed": 0,
            "total": total_expected,
            "downloaded_files": [],
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)

                stage = status_info.get("stage", "processing")
                processed = status_info.get("processed", 0)
                total = status_info.get("total") or "?"
                current_identifier = status_info.get("current_identifier")

                if stage == "processing":
                    current_segment = (
                        f" Current icon: `{current_identifier}`" if current_identifier else ""
                    )
                    content = (
                        f"{dry_run_prefix}Processing {processed}/{total} status icons "
                        f"for `{cleaned_status_id}`.{current_segment} ({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}Status upload for `{cleaned_status_id}` "
                        f"is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Status upload for `{cleaned_status_id}` "
                        f"is {stage} ({elapsed}s elapsed)"
                    )

                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_status_upload(cleaned_status_id, max_index_value, status_info)
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            processed = status_info.get("processed", total_expected)
            uploaded = status_info.get("uploaded", 0)
            failed = status_info.get("failed", 0)
            downloaded_files = status_info.get("downloaded_files") or []

            summary_lines = [
                f"{dry_run_prefix}Status upload completed for `{cleaned_status_id}` in {elapsed}s!",
                "**Summary:**",
                f"- Icons processed: {processed}",
                f"- Icons uploaded: {uploaded}",
                f"- Icons failed: {failed}",
            ]

            if downloaded_files:
                base_url = "https://gbf.wiki/File:"
                unique_files = list(dict.fromkeys(downloaded_files))
                link_lines = ["", "**Links:**"]
                link_lines.extend(
                    f"- {file_name}: <{base_url}{file_name.replace(' ', '_')}>"
                    for file_name in unique_files
                )
                summary_lines.extend(link_lines)

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=(
                    f"{dry_run_prefix}Status upload failed for `{cleaned_status_id}` in {elapsed}s!"
                )
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@bot.tree.command(
    name="bannerupload",
    description="Upload gacha banner variants to the wiki",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    banner_id="Identifier between `banner_` and the trailing index in the CDN URL",
    max_index="Highest banner index to attempt (1-50, defaults to 12)",
)
async def bannerupload(
    interaction: discord.Interaction,
    banner_id: str,
    max_index: app_commands.Range[int, 1, 50] = 12,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_banner, cleaned_banner_id = validate_banner_id(banner_id)
    if not is_valid_banner:
        await interaction.response.send_message(cleaned_banner_id, ephemeral=True)
        return

    max_index_value = int(max_index or 12)

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/bannerupload` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Banner upload started for `{cleaned_banner_id}` "
            f"(up to index {max_index_value}). This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "banner_id": cleaned_banner_id,
            "processed": 0,
            "uploaded": 0,
            "failed": 0,
            "total": max_index_value,
            "downloaded_files": [],
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                stage = status_info.get("stage", "processing")
                processed = status_info.get("processed", 0)
                total = status_info.get("total") or max_index_value
                current_identifier = status_info.get("current_identifier")

                if stage == "processing":
                    current_segment = (
                        f" Current banner: `{current_identifier}`" if current_identifier else ""
                    )
                    content = (
                        f"{dry_run_prefix}Processing {processed}/{total} gacha banners "
                        f"for `{cleaned_banner_id}`{current_segment} ({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}Banner upload for `{cleaned_banner_id}` "
                        f"is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Banner upload for `{cleaned_banner_id}` "
                        f"is {stage} ({elapsed}s elapsed)"
                    )

                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_banner_upload(
            cleaned_banner_id, max_index_value, status_info
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            processed = status_info.get("processed", max_index_value)
            uploaded = status_info.get("uploaded", 0)
            failed = status_info.get("failed", 0)
            downloaded_files = status_info.get("downloaded_files") or []
            banner_duplicates = status_info.get("banner_duplicates") or []

            summary_lines = [
                f"{dry_run_prefix}Banner upload completed for `{cleaned_banner_id}` in {elapsed}s!",
                "**Summary:**",
                f"- Banners processed: {processed}",
                f"- Banners uploaded: {uploaded}",
                f"- Banners failed: {failed}",
            ]

            if downloaded_files:
                base_url = "https://gbf.wiki/File:"
                unique_files = list(dict.fromkeys(downloaded_files))
                link_lines = ["", "**Links:**"]
                link_lines.extend(
                    f"- {file_name}: <{base_url}{file_name.replace(' ', '_')}>"
                    for file_name in unique_files
                )
                summary_lines.extend(link_lines)

            if banner_duplicates:
                base_url = "https://gbf.wiki/File:"
                summary_lines.append("")
                summary_lines.append("**Duplicates handled:**")
                for entry in banner_duplicates:
                    requested = entry.get("requested")
                    canonical = entry.get("canonical")
                    duplicates = entry.get("duplicates") or []
                    redirect_link = (
                        f"<{base_url}{requested.replace(' ', '_')}>"
                        if requested
                        else "N/A"
                    )
                    dupe_links = ", ".join(
                        f"<{base_url}{name.replace(' ', '_')}>"
                        for name in duplicates
                    ) if duplicates else "None listed"
                    summary_lines.append(
                        f"- `{requested}` redirected to `{canonical}`"
                    )
                    summary_lines.append(
                        f"  Redirect: {redirect_link}; Dupes: {dupe_links}"
                    )

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=f"{dry_run_prefix}Banner upload failed for `{cleaned_banner_id}` in {elapsed}s!"
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@bot.tree.command(
    name="drawupdate",
    description="Update MainPageDraw single/double/element draw promotion subtemplates",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    mode="Which main draw layout to publish",
    end_date="Banner end date in JST (YYYY-MM-DD)",
    end_time="Banner end time in JST (HH:MM). Common values: 18:59, 11:59, 23:59",
    left_banner_id="Left/only banner id (between banner_ and _index)",
    right_banner_id="Right banner id (required for double/element-double modes)",
    left_count="Manual count for left/only banners (optional override)",
    right_count="Manual count for right banners (double/element-double modes)",
    max_probe="Auto-detect max index to check when count is not provided",
    link_target="Wiki link target for banner clicks",
    element_start="Starting element for element mode (default: fire)",
)
@app_commands.choices(mode=DRAW_MODE_CHOICES, element_start=DRAW_ELEMENT_CHOICES)
async def drawupdate(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    end_date: str,
    end_time: str,
    left_banner_id: str,
    right_banner_id: str | None = None,
    left_count: app_commands.Range[int, 1, 50] | None = None,
    right_count: app_commands.Range[int, 1, 50] | None = None,
    max_probe: app_commands.Range[int, 1, 50] = DRAW_MAX_PROBE_DEFAULT,
    link_target: str = "Draw",
    element_start: app_commands.Choice[str] | None = None,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    mode_value = mode.value
    if mode_value not in DRAW_MODE_SET:
        await interaction.response.send_message("Invalid mode option.", ephemeral=True)
        return
    element_start_value = element_start.value if element_start else DRAW_ELEMENT_ORDER[0]

    is_valid_date, cleaned_end_date = validate_draw_end_date(end_date)
    if not is_valid_date:
        await interaction.response.send_message(cleaned_end_date, ephemeral=True)
        return

    is_valid_time, cleaned_end_time = validate_draw_end_time(end_time)
    if not is_valid_time:
        await interaction.response.send_message(cleaned_end_time, ephemeral=True)
        return

    cleaned_end_datetime = f"{cleaned_end_date} {cleaned_end_time}"

    is_valid_left_banner, cleaned_left_banner = validate_banner_id(left_banner_id)
    if not is_valid_left_banner:
        await interaction.response.send_message(cleaned_left_banner, ephemeral=True)
        return

    cleaned_right_banner: str | None = None
    if right_banner_id:
        is_valid_right_banner, right_banner_result = validate_banner_id(right_banner_id)
        if not is_valid_right_banner:
            await interaction.response.send_message(right_banner_result, ephemeral=True)
            return
        cleaned_right_banner = right_banner_result

    if mode_value == "single":
        if cleaned_right_banner:
            await interaction.response.send_message(
                'right_banner_id must not be set when mode is "single".',
                ephemeral=True,
            )
            return
        if right_count is not None:
            await interaction.response.send_message(
                'right_count must not be set when mode is "single".',
                ephemeral=True,
            )
            return
    elif mode_value == "double":
        if not cleaned_right_banner:
            await interaction.response.send_message(
                'right_banner_id is required when mode is "double".',
                ephemeral=True,
            )
            return
    elif mode_value == "element-single":
        if cleaned_right_banner:
            await interaction.response.send_message(
                'right_banner_id must not be set when mode is "element-single".',
                ephemeral=True,
            )
            return
        if right_count is not None:
            await interaction.response.send_message(
                'right_count must not be set when mode is "element-single".',
                ephemeral=True,
            )
            return
    elif mode_value == "element-double":
        if not cleaned_right_banner:
            await interaction.response.send_message(
                'right_banner_id is required when mode is "element-double".',
                ephemeral=True,
            )
            return

    is_valid_link, cleaned_link_target = validate_draw_link_target(link_target)
    if not is_valid_link:
        await interaction.response.send_message(cleaned_link_target, ephemeral=True)
        return

    max_probe_value = int(max_probe or DRAW_MAX_PROBE_DEFAULT)
    left_count_value = int(left_count) if left_count is not None else None
    right_count_value = int(right_count) if right_count is not None else None

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/drawupdate` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Draw update started for mode `{mode_value}` "
            f"(left: `{cleaned_left_banner}`, element_start: `{element_start_value}`). This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "left_files": [],
            "right_files": [],
            "saved_pages": [],
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                stage = status_info.get("stage", "processing")
                if stage == "resolving_files":
                    content = (
                        f"{dry_run_prefix}Resolving banner files for `{mode_value}` mode "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "saving_pages":
                    pages = status_info.get("pages") or []
                    content = (
                        f"{dry_run_prefix}Saving draw subtemplates ({len(pages)} pages) "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}Draw update is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Draw update is {stage} ({elapsed}s elapsed)"
                    )
                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_draw_update(
            mode_value,
            cleaned_end_datetime,
            cleaned_left_banner,
            cleaned_right_banner,
            left_count_value,
            right_count_value,
            max_probe_value,
            cleaned_link_target,
            element_start_value,
            status_info,
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            left_files = status_info.get("left_files") or []
            right_files = status_info.get("right_files") or []
            saved_pages = status_info.get("saved_pages") or []

            left_count_used = len(left_files)
            right_count_used = len(right_files)
            left_count_source = (
                f"manual={left_count_value}" if left_count_value is not None else f"auto={left_count_used}"
            )
            right_count_source = (
                f"manual={right_count_value}" if right_count_value is not None else f"auto={right_count_used}"
            )

            summary_lines = [
                f"{dry_run_prefix}Draw update completed in {elapsed}s.",
                "**Inputs used:**",
                f"- mode: `{mode_value}`",
                f"- end_date: `{cleaned_end_date}`",
                f"- end_time: `{cleaned_end_time}`",
                f"- left_banner_id: `{cleaned_left_banner}`",
                f"- left_count: `{left_count_source}`",
                f"- max_probe: `{max_probe_value}`",
                f"- link_target: `{cleaned_link_target}`",
                f"- element_start: `{element_start_value}`",
            ]

            if mode_value in ("double", "element-double"):
                summary_lines.append(f"- right_banner_id: `{cleaned_right_banner}`")
                summary_lines.append(f"- right_count: `{right_count_source}`")

            summary_lines.append("")
            summary_lines.append("**Updated pages:**")
            for page in saved_pages:
                page_url = f"https://gbf.wiki/{page.replace(' ', '_')}"
                summary_lines.append(f"- <{page_url}>")

            summary_lines.append("")
            summary_lines.append("**Banner files used:**")
            summary_lines.extend(f"- Left: `{name}`" for name in left_files)
            if mode_value in ("double", "element-double"):
                summary_lines.extend(f"- Right: `{name}`" for name in right_files)

            summary_lines.append("")
            summary_lines.append(
                f"Please purge Main Page to show changes immediately: {MAIN_PAGE_PURGE_URL}"
            )

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(msg, f"{dry_run_prefix}Draw update failed in {elapsed}s.")
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@drawupdate.autocomplete("end_time")
async def drawupdate_end_time_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    """Suggest common draw end times while allowing custom HH:MM input."""
    current_clean = (current or "").strip()
    filtered = [
        t for t in DRAW_COMMON_END_TIMES
        if not current_clean or current_clean in t
    ]
    return [app_commands.Choice(name=t, value=t) for t in filtered[:25]]


@bot.tree.command(
    name="promoupdate",
    description="Update a supported MainPageDraw promo subtemplate",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    promo_type="Which promo section to update",
    promo_id="Promo asset id, banner filename, or full CDN URL",
    end_date="Promo end date in JST (YYYY-MM-DD)",
    end_time="Promo end time in JST (HH:MM)",
    link_target="Wiki link target for promo clicks",
)
@app_commands.choices(promo_type=PROMO_TYPE_CHOICES)
async def promoupdate(
    interaction: discord.Interaction,
    promo_type: app_commands.Choice[str],
    promo_id: str,
    end_date: str,
    end_time: str,
    link_target: str = "Surprise Ticket",
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    promo_type_value = promo_type.value
    if promo_type_value not in PROMO_TYPE_SET:
        await interaction.response.send_message("Invalid promo_type option.", ephemeral=True)
        return

    is_valid_date, cleaned_end_date = validate_draw_end_date(end_date)
    if not is_valid_date:
        await interaction.response.send_message(cleaned_end_date, ephemeral=True)
        return

    is_valid_time, cleaned_end_time = validate_draw_end_time(end_time)
    if not is_valid_time:
        await interaction.response.send_message(cleaned_end_time, ephemeral=True)
        return

    is_valid_promo, cleaned_promo_id = validate_banner_id(promo_id)
    if not is_valid_promo:
        await interaction.response.send_message(cleaned_promo_id, ephemeral=True)
        return

    is_valid_link, cleaned_link_target = validate_draw_link_target(link_target)
    if not is_valid_link:
        await interaction.response.send_message(cleaned_link_target, ephemeral=True)
        return

    cleaned_end_datetime = f"{cleaned_end_date} {cleaned_end_time}"

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/promoupdate` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Promo update started for `{promo_type_value}` "
            f"using `{cleaned_promo_id}`. This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "saved_pages": [],
            "resolved_file_name": "",
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                stage = status_info.get("stage", "processing")
                if stage == "validating_file":
                    resolved_file_name = status_info.get("resolved_file_name") or "?"
                    content = (
                        f"{dry_run_prefix}Validating promo file `{resolved_file_name}` "
                        f"on the wiki ({elapsed}s elapsed)"
                    )
                elif stage == "saving_pages":
                    pages = status_info.get("pages") or []
                    content = (
                        f"{dry_run_prefix}Saving promo subtemplates ({len(pages)} pages) "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}Promo update is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Promo update is {stage} ({elapsed}s elapsed)"
                    )
                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_promo_update(
            promo_type_value,
            cleaned_promo_id,
            cleaned_end_datetime,
            cleaned_link_target,
            status_info,
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            saved_pages = status_info.get("saved_pages") or []
            resolved_file_name = status_info.get("resolved_file_name") or f"banner_{cleaned_promo_id}.png"

            summary_lines = [
                f"{dry_run_prefix}Promo update completed for `{promo_type_value}` in {elapsed}s.",
                "**Inputs used:**",
                f"- promo_type: `{promo_type_value}`",
                f"- promo_id: `{cleaned_promo_id}`",
                f"- end_date: `{cleaned_end_date}`",
                f"- end_time: `{cleaned_end_time}`",
                f"- link_target: `{cleaned_link_target}`",
                "",
                "**Resolved file:**",
                f"- `{resolved_file_name}`",
                "",
                "**Updated pages:**",
            ]

            for page in saved_pages:
                page_url = f"https://gbf.wiki/{page.replace(' ', '_')}"
                summary_lines.append(f"- <{page_url}>")

            summary_lines.append("")
            summary_lines.append(
                f"Please purge Main Page to show changes immediately: {MAIN_PAGE_PURGE_URL}"
            )

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=f"{dry_run_prefix}Promo update failed for `{promo_type_value}` in {elapsed}s."
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@promoupdate.autocomplete("end_time")
async def promoupdate_end_time_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    current_clean = (current or "").strip()
    filtered = [
        t for t in DRAW_COMMON_END_TIMES
        if not current_clean or current_clean in t
    ]
    return [app_commands.Choice(name=t, value=t) for t in filtered[:25]]


@bot.tree.command(
    name="rateup",
    description="Update MainPageDraw rate-up characters subtemplate",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    end_date="Rate-up end date in JST (YYYY-MM-DD)",
    end_time="Rate-up end time in JST (HH:MM). Common values: 18:59, 11:59, 23:59",
    rateups="Pipe-separated rate-up character names for CharacterIcons",
    sparkable="Pipe-separated sparkable character names for CharacterIcons",
)
async def rateup(
    interaction: discord.Interaction,
    end_date: str,
    end_time: str,
    rateups: str,
    sparkable: str,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_date, cleaned_end_date = validate_draw_end_date(end_date)
    if not is_valid_date:
        await interaction.response.send_message(cleaned_end_date, ephemeral=True)
        return

    is_valid_time, cleaned_end_time = validate_draw_end_time(end_time)
    if not is_valid_time:
        await interaction.response.send_message(cleaned_end_time, ephemeral=True)
        return

    cleaned_end_datetime = f"{cleaned_end_date} {cleaned_end_time}"

    are_rateups_valid, rateup_result = validate_pipe_separated_page_names(rateups, "rateups")
    if not are_rateups_valid:
        await interaction.response.send_message(rateup_result, ephemeral=True)
        return
    rateup_names = rateup_result
    if not rateup_names:
        await interaction.response.send_message(
            "`rateups` is required and must contain at least one character name.",
            ephemeral=True,
        )
        return

    are_sparkable_valid, sparkable_result = validate_pipe_separated_page_names(sparkable, "sparkable")
    if not are_sparkable_valid:
        await interaction.response.send_message(sparkable_result, ephemeral=True)
        return
    sparkable_names = sparkable_result
    if not sparkable_names:
        await interaction.response.send_message(
            "`sparkable` is required and must contain at least one character name.",
            ephemeral=True,
        )
        return

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/rateup` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Rate-up update started "
            f"(rateups: `{len(rateup_names)}`, sparkable: `{len(sparkable_names)}`). This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "saved_pages": [],
            "rateups": rateup_names,
            "sparkable": sparkable_names,
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                stage = status_info.get("stage", "processing")
                if stage == "saving_pages":
                    pages = status_info.get("pages") or []
                    content = (
                        f"{dry_run_prefix}Saving rate-up subtemplates ({len(pages)} pages) "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}Rate-up update is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Rate-up update is {stage} ({elapsed}s elapsed)"
                    )
                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_rateup_update(
            cleaned_end_datetime,
            rateup_names,
            sparkable_names,
            status_info,
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            saved_pages = status_info.get("saved_pages") or []

            summary_lines = [
                f"{dry_run_prefix}Rate-up update completed in {elapsed}s.",
                "**Inputs used:**",
                f"- end_date: `{cleaned_end_date}`",
                f"- end_time: `{cleaned_end_time}`",
                f"- rateups: `{ '|'.join(rateup_names) if rateup_names else '(none)' }`",
                f"- sparkable: `{ '|'.join(sparkable_names) if sparkable_names else '(none)' }`",
                "",
                "**Updated pages:**",
            ]
            for page in saved_pages:
                page_url = f"https://gbf.wiki/{page.replace(' ', '_')}"
                summary_lines.append(f"- <{page_url}>")

            summary_lines.extend([
                "",
                f"Please purge Main Page to show changes immediately: {MAIN_PAGE_PURGE_URL}",
            ])

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(msg, f"{dry_run_prefix}Rate-up update failed in {elapsed}s.")
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@rateup.autocomplete("end_time")
async def rateup_end_time_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    """Suggest common rate-up end times while allowing custom HH:MM input."""
    current_clean = (current or "").strip()
    filtered = [
        t for t in DRAW_COMMON_END_TIMES
        if not current_clean or current_clean in t
    ]
    return [app_commands.Choice(name=t, value=t) for t in filtered[:25]]


@bot.tree.command(
    name="risingrotation",
    description="Insert a new GBVSR rotation row on the dedicated rotation subpage",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    start_date="Rotation start date in JST (YYYY-MM-DD). Default start time is 11:00.",
    c2="Second character slot. Suggestions provided; custom entries allowed.",
    c3="Third character slot. Suggestions provided; custom entries allowed.",
    c4="Fourth character slot. Suggestions provided; custom entries allowed.",
    c5="Fifth character slot. Suggestions provided; custom entries allowed.",
    c1="Optional first slot override for exception weeks. Suggestions provided; custom entries allowed.",
    notes="Optional notes text for the row.",
    week_override="Optional manual week for backfills/corrections.",
    start_time_override="Optional start time override in JST (HH:MM). Defaults to 11:00.",
    end_date_override="Optional end date override in JST (YYYY-MM-DD). Must be paired with end_time_override.",
    end_time_override="Optional end time override in JST (HH:MM). Must be paired with end_date_override.",
)
async def risingrotation(
    interaction: discord.Interaction,
    start_date: str,
    c2: str,
    c3: str = "",
    c4: str = "",
    c5: str = "",
    c1: str = "",
    notes: str = "",
    week_override: app_commands.Range[int, 1, 9999] | None = None,
    start_time_override: str = "",
    end_date_override: str = "",
    end_time_override: str = "",
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_start_date, cleaned_start_date = validate_draw_end_date(start_date)
    if not is_valid_start_date:
        await interaction.response.send_message(
            cleaned_start_date.replace("end_date", "start_date"),
            ephemeral=True,
        )
        return

    cleaned_start_time = RISING_ROTATION_DEFAULT_START_TIME
    start_time_source = "auto"
    if (start_time_override or "").strip():
        is_valid_start_time, cleaned_start_time = validate_draw_end_time(start_time_override)
        if not is_valid_start_time:
            await interaction.response.send_message(
                cleaned_start_time.replace("end_time", "start_time_override"),
                ephemeral=True,
            )
            return
        start_time_source = "override"

    has_end_date_override = bool((end_date_override or "").strip())
    has_end_time_override = bool((end_time_override or "").strip())
    if has_end_date_override != has_end_time_override:
        await interaction.response.send_message(
            "`end_date_override` and `end_time_override` must be supplied together.",
            ephemeral=True,
        )
        return

    start_datetime = datetime.strptime(
        f"{cleaned_start_date} {cleaned_start_time}",
        "%Y-%m-%d %H:%M",
    )
    cleaned_end_date = (start_datetime + timedelta(days=7)).strftime("%Y-%m-%d")
    cleaned_end_time = RISING_ROTATION_DEFAULT_END_TIME
    end_source = "auto"
    if has_end_date_override and has_end_time_override:
        is_valid_end_date, cleaned_end_date = validate_draw_end_date(end_date_override)
        if not is_valid_end_date:
            await interaction.response.send_message(cleaned_end_date, ephemeral=True)
            return
        is_valid_end_time, cleaned_end_time = validate_draw_end_time(end_time_override)
        if not is_valid_end_time:
            await interaction.response.send_message(cleaned_end_time, ephemeral=True)
            return
        end_source = "override"

    validated_characters: dict[str, str] = {}
    for field_name, raw_value, required in (
        ("c1", c1, False),
        ("c2", c2, True),
        ("c3", c3, False),
        ("c4", c4, False),
        ("c5", c5, False),
    ):
        is_valid_character, cleaned_character = validate_rising_rotation_character(
            raw_value,
            field_name,
            required=required,
        )
        if not is_valid_character:
            await interaction.response.send_message(cleaned_character, ephemeral=True)
            return
        validated_characters[field_name] = cleaned_character

    is_valid_notes, cleaned_notes = validate_rising_rotation_notes(notes)
    if not is_valid_notes:
        await interaction.response.send_message(cleaned_notes, ephemeral=True)
        return

    start_datetime_text = f"{cleaned_start_date} {cleaned_start_time}"
    end_datetime_text = f"{cleaned_end_date} {cleaned_end_time}"

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/risingrotation` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}GBVSR rotation update started for `{RISING_ROTATION_PAGE}`. This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status_info = {
            "stage": "starting",
            "page": RISING_ROTATION_PAGE,
            "resolved_week": None,
            "week_source": "auto",
            "row_text": "",
            "start_datetime_text": start_datetime_text,
            "end_datetime_text": end_datetime_text,
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                stage = status_info.get("stage", "processing")
                if stage == "loading_page":
                    content = (
                        f"{dry_run_prefix}Loading `{RISING_ROTATION_PAGE}` "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "saving_page":
                    resolved_week = status_info.get("resolved_week")
                    content = (
                        f"{dry_run_prefix}Saving week `{resolved_week}` to `{RISING_ROTATION_PAGE}` "
                        f"({elapsed}s elapsed)"
                    )
                elif stage == "completed":
                    content = (
                        f"{dry_run_prefix}GBVSR rotation update is wrapping up ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}GBVSR rotation update is {stage} ({elapsed}s elapsed)"
                    )
                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_rising_rotation_update(
            start_datetime_text,
            end_datetime_text,
            validated_characters,
            cleaned_notes,
            week_override,
            status_info,
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            resolved_week = status_info.get("resolved_week")
            row_text = status_info.get("row_text") or ""
            page_url = f"https://gbf.wiki/{RISING_ROTATION_PAGE.replace(' ', '_')}"

            summary_lines = [
                f"{dry_run_prefix}GBVSR rotation update completed in {elapsed}s.",
                "**Resolved values:**",
                f"- page: `{RISING_ROTATION_PAGE}`",
                f"- week: `{resolved_week}` ({status_info.get('week_source', 'auto')})",
                f"- start: `{start_datetime_text} JST` ({start_time_source})",
                f"- end: `{end_datetime_text} JST` ({end_source})",
                "",
                "**Characters:**",
            ]
            for field_name in ("c1", "c2", "c3", "c4", "c5"):
                value = validated_characters.get(field_name, "")
                if value:
                    summary_lines.append(f"- {field_name}: `{value}`")
            if cleaned_notes:
                summary_lines.append(f"- notes: `{cleaned_notes}`")

            summary_lines.extend([
                "",
                "**Updated page:**",
                f"- <{page_url}>",
                "",
                "**Inserted row:**",
                "```wikitext",
                row_text,
                "```",
            ])

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(msg, f"{dry_run_prefix}GBVSR rotation update failed in {elapsed}s.")
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@risingrotation.autocomplete("c1")
@risingrotation.autocomplete("c2")
@risingrotation.autocomplete("c3")
@risingrotation.autocomplete("c4")
@risingrotation.autocomplete("c5")
async def risingrotation_character_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current_lower = (current or "").strip().lower()
    filtered = [
        name
        for name in RISING_ROTATION_AUTOCOMPLETE_NAMES
        if not current_lower or current_lower in name.lower()
    ]
    return [app_commands.Choice(name=name, value=name) for name in filtered[:25]]


@bot.tree.command(
    name="help",
    description="Show slash-command help and usage details.",
)
@app_commands.describe(
    command="Optional slash command name to show in detail.",
)
async def help_command(
    interaction: discord.Interaction,
    command: str | None = None,
):
    normalized_command = normalize_help_command_input(command)

    if not normalized_command:
        overview_lines = [
            "**Slash Commands**",
            "Use `/help command:<name>` for detailed help on one command.",
            "",
        ]
        overview_lines.extend(
            f"- `/{name}` — {HELP_COMMAND_DETAILS[name]['summary']}"
            for name in HELP_COMMAND_CHOICES
        )
        await interaction.response.send_message("\n".join(overview_lines), ephemeral=True)
        return

    help_entry = HELP_COMMAND_DETAILS.get(normalized_command)
    if help_entry is None:
        suggestions = [
            name for name in HELP_COMMAND_CHOICES
            if normalized_command in name
        ][:5]
        suggestion_text = (
            f" Try one of: {', '.join(f'`/{name}`' for name in suggestions)}."
            if suggestions
            else ""
        )
        await interaction.response.send_message(
            f"Unknown command `{normalized_command}`.{suggestion_text}",
            ephemeral=True,
        )
        return

    await interaction.response.send_message("Loading help...", ephemeral=True)
    msg = await interaction.original_response()
    await edit_or_followup_long_message_ephemeral(msg, interaction, help_entry["details"])

@help_command.autocomplete("command")
async def help_command_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current_clean = normalize_help_command_input(current)
    filtered = [
        command_name
        for command_name in HELP_COMMAND_CHOICES
        if not current_clean or current_clean in command_name
    ]
    return [app_commands.Choice(name=name, value=name) for name in filtered[:25]]


@bot.tree.command(
    name="synccommands",
    description="Force a slash-command sync (admins only).",
)
@app_commands.guild_only()
async def synccommands(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return

    member = guild.get_member(interaction.user.id)
    is_admin = False
    if member:
        perms = member.guild_permissions
        is_admin = perms.administrator or member.id == guild.owner_id

    if not is_admin:
        await interaction.response.send_message(
            "You must be a server administrator to sync commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        result = await bot.sync_app_commands(force=True)
    except Exception as exc:
        await interaction.followup.send(
            f"Slash-command sync failed: `{exc}`", ephemeral=True
        )
        return

    response_lines = [
        f"Commands synced via `{result['scope']}` scope.",
        f"Total registered commands: {result['count']}.",
    ]

    if result.get("status") == "fallback_synced" and result.get("error"):
        error_preview = str(result["error"])[:400]
        response_lines.append(f"Guild sync error (used fallback): `{error_preview}`")

    await interaction.followup.send("\n".join(response_lines), ephemeral=True)


@bot.tree.command(
    name="itemupload",
    description="Upload square/icon variants for a single item by id",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    item_type="CDN folder name (article, normal, etc.). Suggestions provided; custom entries allowed.",
    item_id="Item ID (from the image URL path)",
    item_name="Item Name (creates redirects with this name)"
)
async def itemupload(
    interaction: discord.Interaction,
    item_type: str,
    item_id: str,
    item_name: str
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True
        )
        return

    is_valid_id, cleaned_id = validate_item_id(item_id)
    if not is_valid_id:
        await interaction.response.send_message(cleaned_id, ephemeral=True)
        return

    is_valid_name, cleaned_name = validate_item_name(item_name)
    if not is_valid_name:
        await interaction.response.send_message(cleaned_name, ephemeral=True)
        return

    item_type_value = normalize_item_type_input(item_type)
    if not item_type_value:
        await interaction.response.send_message(
            "Item type is required. Provide any CDN folder name such as `article`.",
            ephemeral=True
        )
        return

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/itemupload` again.",
            ephemeral=True
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Single-item upload started for `{cleaned_name}` "
            f"(type: `{item_type_value}`, ID: `{cleaned_id}`). This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()

        status = {"stage": "starting", "details": "", "item_type": item_type_value}

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)

                stage = status.get("stage", "processing")
                if stage == "processing":
                    processed = status.get("processed", 0)
                    total = status.get("total")
                    total_display = total if total else "?"
                    current_image = status.get("current_image")
                    current_segment = f" Current: {current_image}" if current_image else ""
                    content = (
                        f"{dry_run_prefix}Processing {processed}/{total_display} images for "
                        f"`{cleaned_name}` (type: `{item_type_value}`, ID: `{cleaned_id}`)."
                        f"{current_segment} ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Item upload for `{cleaned_name}` "
                        f"(type: `{item_type_value}`, ID: `{cleaned_id}`) "
                        f"is running... ({elapsed}s elapsed)"
                    )

                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_item_upload(item_type_value, cleaned_id, cleaned_name, status)
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            processed = status.get("processed", 0)
            uploaded = status.get("uploaded", 0)
            duplicate_matches = status.get("duplicates", 0)
            total_checked = status.get("total_urls", 0)

            summary_lines = [
                f"{dry_run_prefix}Item upload completed for `{cleaned_name}` "
                f"(type: `{item_type_value}`, ID: `{cleaned_id}`) in {elapsed}s!",
                "**Summary:**",
                f"- Variants processed: {processed}",
                f"- Images uploaded: {uploaded}",
                f"- Images found as duplicates: {duplicate_matches}",
                f"- Total URLs checked: {total_checked}",
            ]

            base_url = "https://gbf.wiki/File:"
            canonical_s_file = f"Item_{item_type_value}_s_{cleaned_id}.jpg"
            canonical_m_file = f"Item_{item_type_value}_m_{cleaned_id}.jpg"
            redirect_square_file = f"{cleaned_name} square.jpg"
            redirect_icon_file = f"{cleaned_name} icon.jpg"

            link_lines = [
                "",
                "**Links:**",
                f"- Canonical S: <{base_url}{canonical_s_file.replace(' ', '_')}>",
                f"- Canonical M: <{base_url}{canonical_m_file.replace(' ', '_')}>",
                f"- Redirect Square: <{base_url}{redirect_square_file.replace(' ', '_')}>",
                f"- Redirect Icon: <{base_url}{redirect_icon_file.replace(' ', '_')}>",
            ]

            summary_lines.extend(link_lines)

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=(
                    f"{dry_run_prefix}Item upload failed for `{cleaned_name}` "
                    f"(type: `{item_type_value}`, ID: `{cleaned_id}`) in {elapsed}s!"
                )
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")



@bot.tree.command(
    name="eventupload",
    description="Upload event banner and teaser assets",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    event_id="Event folder identifier (e.g. treasureraid169 or biography042)",
    event_name="Event display name (used for redirects)",
    asset_type="Select which event asset type to upload.",
    max_index="Max guide/banner index to attempt (default 20; raid_thumb defaults to 13; top/trailer mp3 use 1).",
)
@app_commands.choices(asset_type=EVENT_TEASER_ASSET_TYPE_CHOICES)
async def eventupload(
    interaction: discord.Interaction,
    event_id: str,
    event_name: str,
    asset_type: app_commands.Choice[str],
    max_index: int | None = None,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_event_id, cleaned_event_id = validate_event_id(event_id)
    if not is_valid_event_id:
        await interaction.response.send_message(cleaned_event_id, ephemeral=True)
        return

    is_valid_event_name, cleaned_event_name = validate_event_file_name(event_name)
    if not is_valid_event_name:
        await interaction.response.send_message(cleaned_event_name, ephemeral=True)
        return

    asset_type_value = asset_type.value
    if asset_type_value not in EVENT_TEASER_ASSET_TYPE_SET:
        await interaction.response.send_message("Invalid asset type option.", ephemeral=True)
        return

    if max_index is None:
        if asset_type_value == "raid_thumb":
            max_index = 13
        elif asset_type_value in {"top", "trailer_mp3"}:
            max_index = 1
        else:
            max_index = WikiImages.EVENT_BANNER_MAX_INDEX
    if max_index < 1:
        await interaction.response.send_message(
            "Invalid max index. It must be at least 1.",
            ephemeral=True,
        )
        return

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/eventupload` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Event upload started for `{cleaned_event_name}` "
            f"(event id: `{cleaned_event_id}`, asset type: `{asset_type_value}`, max index: `{max_index}`). "
            "This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status = {
            "stage": "starting",
            "event_id": cleaned_event_id,
            "asset_type": asset_type_value,
            "total": max_index,
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)

                stage = status.get("stage", "processing")
                processed = status.get("processed", 0)
                total = status.get("total") or max_index
                current_image = status.get("current_image")
                current_segment = f" Current: {current_image}" if current_image else ""

                if stage == "processing":
                    content = (
                        f"{dry_run_prefix}Processing {processed}/{total} event assets for "
                        f"`{cleaned_event_name}` (event id: `{cleaned_event_id}`, asset type: `{asset_type_value}`)."
                        f"{current_segment} ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Event upload for `{cleaned_event_name}` "
                        f"(event id: `{cleaned_event_id}`, asset type: `{asset_type_value}`) "
                        f"is running... ({elapsed}s elapsed)"
                    )

                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_event_upload(
            cleaned_event_id, cleaned_event_name, asset_type_value, max_index, status
        )
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            processed = status.get("processed", 0)
            uploaded = status.get("uploaded", 0)
            duplicates = status.get("duplicates", 0)
            failed = status.get("failed", 0)
            files = status.get("files", [])

            summary_lines = [
                f"{dry_run_prefix}Event upload completed for `{cleaned_event_name}` "
                f"(event id: `{cleaned_event_id}`, asset type: `{asset_type_value}`) in {elapsed}s!",
                "**Summary:**",
                f"- Images processed: {processed}",
                f"- Images uploaded: {uploaded}",
                f"- Images found as duplicates: {duplicates}",
                f"- Images failed validation: {failed}",
            ]

            if files:
                template_redirects = [
                    redirect_name.replace(" ", "_")
                    for entry in files
                    if (redirect_name := entry.get("redirect"))
                ]
                if template_redirects and asset_type_value in {"notice", "start"}:
                    summary_lines.extend([
                        "",
                        "**Paste into EventHistory template:**",
                        "```text",
                        ";".join(template_redirects),
                        "```",
                    ])
                if asset_type_value == "guide":
                    gallery_lines = [
                        redirect_name.replace(" ", "_")
                        for entry in files
                        if (redirect_name := entry.get("redirect"))
                    ]
                    if gallery_lines:
                        summary_lines.extend([
                            "",
                            "**Paste into guide gallery:**",
                            "```text",
                            '<gallery mode="nolines" perrow="3" widths="280px" heights="300px" position="left">',
                            *gallery_lines,
                            "</gallery>",
                            "```",
                        ])

                base_url = "https://gbf.wiki/File:"
                link_lines = ["", "**Links:**"]
                for entry in files:
                    index = entry.get("index")
                    canonical_name = entry.get("canonical")
                    redirect_names = entry.get("redirects") or []
                    if not redirect_names and entry.get("redirect"):
                        redirect_names = [entry["redirect"]]
                    line = (
                        f"- #{index}: Canonical <{base_url}{canonical_name.replace(' ', '_')}>"
                    )
                    if redirect_names:
                        redirect_links = ", ".join(
                            f"<{base_url}{redirect_name.replace(' ', '_')}>"
                            for redirect_name in redirect_names
                        )
                        line += f" | Redirect(s) {redirect_links}"
                    link_lines.append(line)
                summary_lines.extend(link_lines)

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=(
                    f"{dry_run_prefix}Event upload failed for `{cleaned_event_name}` "
                    f"(event id: `{cleaned_event_id}`, asset type: `{asset_type_value}`) in {elapsed}s!"
                )
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


@itemupload.autocomplete("item_type")
async def itemupload_item_type_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    """Suggest common CDN folders while keeping the input free-form."""
    current_lower = (current or "").lower()
    filtered = [it for it in ITEM_TYPES if not current_lower or current_lower in it]
    return [
        app_commands.Choice(name=it.title(), value=it)
        for it in filtered[:25]
    ]


@bot.tree.command(
    name="enemyupload",
    description="Upload enemy S/M icons by id",
)
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    enemy_id="Enemy identifier (numeric) used in the CDN URL.",
)
async def enemyupload(
    interaction: discord.Interaction,
    enemy_id: str,
):
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not (
        any(role.name in ALLOWED_ROLES for role in member.roles)
        or member.guild.owner_id == interaction.user.id
    ):
        await interaction.response.send_message(
            f"You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}",
            ephemeral=True,
        )
        return

    is_valid_enemy_id, cleaned_enemy_id = validate_enemy_id(enemy_id)
    if not is_valid_enemy_id:
        await interaction.response.send_message(cleaned_enemy_id, ephemeral=True)
        return

    now = time.time()
    last = last_used.get(interaction.user.id, 0)
    if now - last < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - (now - last))
        await interaction.response.send_message(
            f"Please wait {remaining}s before using `/enemyupload` again.",
            ephemeral=True,
        )
        return
    last_used[interaction.user.id] = now

    if upload_lock.locked():
        await interaction.response.send_message(
            "Another upload is already running. Please wait until it finishes.",
            ephemeral=True,
        )
        return

    async with upload_lock:
        dry_run_prefix = "[DRY RUN] " if DRY_RUN else ""
        await interaction.response.send_message(
            f"{dry_run_prefix}Enemy upload started for id `{cleaned_enemy_id}`. This may take a while..."
        )
        msg = await get_persistent_response_message(interaction)

    try:
        start_time = time.time()
        status = {
            "stage": "starting",
            "enemy_id": cleaned_enemy_id,
            "total": 2,
        }

        async def progress_updater():
            nonlocal msg
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)

                stage = status.get("stage", "processing")
                processed = status.get("processed", 0)
                total = status.get("total", 2)
                current_image = status.get("current_image")
                current_segment = f" Current: {current_image}" if current_image else ""

                if stage == "processing":
                    content = (
                        f"{dry_run_prefix}Processing {processed}/{total} enemy images for "
                        f"`{cleaned_enemy_id}`.{current_segment} ({elapsed}s elapsed)"
                    )
                else:
                    content = (
                        f"{dry_run_prefix}Enemy upload for `{cleaned_enemy_id}` "
                        f"is running... ({elapsed}s elapsed)"
                    )

                msg = await edit_public_message(msg, content)

        updater_task = asyncio.create_task(progress_updater())
        return_code, stdout, stderr = await run_enemy_upload(cleaned_enemy_id, status)
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        if return_code == 0:
            processed = status.get("processed", 0)
            uploaded = status.get("uploaded", 0)
            duplicates = status.get("duplicates", 0)
            failed = status.get("failed", 0)
            files = status.get("files", [])

            summary_lines = [
                f"{dry_run_prefix}Enemy upload completed for `{cleaned_enemy_id}` in {elapsed}s!",
                "**Summary:**",
                f"- Variants processed: {processed}",
                f"- Images uploaded: {uploaded}",
                f"- Images found as duplicates: {duplicates}",
                f"- Images failed validation: {failed}",
            ]

            if files:
                base_url = "https://gbf.wiki/File:"
                link_lines = ["", "**Links:**"]
                canonical_s = next((f["canonical"] for f in files if f.get("variant") == "s"), None)
                canonical_m = next((f["canonical"] for f in files if f.get("variant") == "m"), None)
                redirect_s = next((f["redirect"] for f in files if f.get("variant") == "s"), None)
                redirect_m = next((f["redirect"] for f in files if f.get("variant") == "m"), None)

                if canonical_s:
                    link_lines.append(
                        f"- Canonical S: <{base_url}{canonical_s.replace(' ', '_')}>"
                    )
                if canonical_m:
                    link_lines.append(
                        f"- Canonical M: <{base_url}{canonical_m.replace(' ', '_')}>"
                    )
                if redirect_s:
                    link_lines.append(
                        f"- Redirect S: <{base_url}{redirect_s.replace(' ', '_')}>"
                    )
                if redirect_m:
                    link_lines.append(
                        f"- Redirect M: <{base_url}{redirect_m.replace(' ', '_')}>"
                    )

                if len(link_lines) > 2:
                    summary_lines.extend(link_lines)

            await edit_or_followup_long_message(msg, interaction, "\n".join(summary_lines))
        else:
            msg = await edit_public_message(
                msg,
                content=(
                    f"{dry_run_prefix}Enemy upload failed for `{cleaned_enemy_id}` "
                    f"in {elapsed}s!"
                )
            )
            if stderr.strip():
                error_preview = stderr.strip()[:500]
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        msg = await edit_public_message(msg, f"Error while running script after {elapsed}s:\n```{e}```")


# --- START BOT ---
bot.run(DISCORD_TOKEN)
