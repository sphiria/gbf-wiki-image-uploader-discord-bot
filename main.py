import os
import discord
from discord import app_commands
import io
import asyncio
import time
import re
import sys
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
VALID_PAGE_NAME_REGEX = re.compile(r"^[\w\s\-\(\)\'\"\.]+$")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
# Comma-separated list of allowed roles, e.g. "Wiki Editor,Wiki Admin"
ALLOWED_ROLES = [r.strip() for r in os.getenv("ALLOWED_ROLES", "Wiki Editor,Wiki Admin,Wiki Discord Moderator,Verified Editor").split(",")]
# Enable dry-run mode (no actual uploads, just logging)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

# Valid page types
PAGE_TYPES = ["character", "weapon", "summon", "skin", "npc", "artifact"]

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
    if not VALID_PAGE_NAME_REGEX.match(page_name):
        return False, "❌ Invalid page name. Only letters, numbers, spaces, -, (), ', \", and . are allowed."

    return True, page_name

async def run_wiki_upload(page_type: str, page_name: str, status: dict = None) -> tuple[int, str, str]:
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
            elif page_type == 'weapon':
                wi.check_weapon(page)
            elif page_type == 'summon':
                wi.check_summon(page)
            elif page_type == 'skin':
                wi.check_skin(page)
            elif page_type == 'npc':
                wi.check_npc(page)
            elif page_type == 'artifact':
                wi.check_artifact(page)
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

# --- BOT SETUP ---
class WikiBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        # Sync commands to your guild for testing (fast updates)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)


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
    page_name="Wiki page name"
)
@app_commands.choices(page_type=[app_commands.Choice(name=pt, value=pt) for pt in PAGE_TYPES])
async def upload(interaction: discord.Interaction, page_type: app_commands.Choice[str], page_name: str):
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
            f"{dry_run_prefix}Upload started for `{page_name}` ({page_type.value}). This may take a while..."
        )
        msg = await interaction.original_response()

    try:
        start_time = time.time()

        status = {"stage": "starting", "details": ""}
        
        # define updater function here
        async def progress_updater():
            while True:
                await asyncio.sleep(15)
                elapsed = int(time.time() - start_time)
                
                if status["stage"] == "downloading":
                    content = f"{dry_run_prefix}Downloading images for `{page_name}` ({page_type.value})... ({elapsed}s elapsed)"
                elif status["stage"] == "processing":
                    processed = status.get("processed", 0)
                    total = status.get("total", 0)
                    current_image = status.get("current_image", "")
                    content = f"{dry_run_prefix}Processing {processed}/{total} images for `{page_name}` ({page_type.value}). Current: {current_image} ({elapsed}s elapsed)"
                elif status["stage"] == "downloaded":
                    successful = status.get("successful", 0)
                    failed = status.get("failed", 0)
                    content = f"{dry_run_prefix}Downloaded {successful} images, {failed} failed for `{page_name}` ({page_type.value}). Starting processing... ({elapsed}s elapsed)"
                else:
                    content = f"{dry_run_prefix}Upload for `{page_name}` ({page_type.value}) still running... ({elapsed}s elapsed)"
                
                await msg.edit(content=content)

        # start the updater task
        updater_task = asyncio.create_task(progress_updater())

        return_code, stdout, stderr = await run_wiki_upload(page_type.value, page_name, status)
        
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
            
            summary = f"{dry_run_prefix}Upload completed for `{page_name}` ({page_type.value}) in {elapsed}s!\n"
            summary += f"**Summary:**\n"
            summary += f"• Images downloaded: {downloaded}\n"
            summary += f"• Images uploaded: {uploaded}\n"
            summary += f"• Images found as duplicates: {duplicates}\n"
            summary += f"• Images processed: {processed}\n" 
            summary += f"• Download failures: {failed}\n"
            summary += f"• Total URLs checked: {total_checked}"
            
            await msg.edit(content=summary)
        else:
            await msg.edit(content=f"{dry_run_prefix}Upload failed for `{page_name}` ({page_type.value}) in {elapsed}s!")
            # Show error details in Discord if there were errors
            if stderr.strip():
                error_preview = stderr.strip()[:500]  # First 500 chars
                await interaction.followup.send(f"Error details:\n```\n{error_preview}\n```")

    except Exception as e:
        elapsed = int(time.time() - start_time)
        await msg.edit(content=f"Error while running script after {elapsed}s:\n```{e}```")


# --- START BOT ---
bot.run(DISCORD_TOKEN)
