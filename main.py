import os
import subprocess
import discord
from discord import app_commands
import io
import asyncio
import time
import re

upload_lock = asyncio.Lock() # Global lock so only one upload runs at a time
last_used = {}  # maps user_id -> timestamp of last command

# --- CONFIG ---
COOLDOWN_SECONDS = 5
MAX_PAGE_NAME_LEN = 100
VALID_PAGE_NAME_REGEX = re.compile(r"^[\w\s\-\(\)\'\"\.]+$")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.environ["GUILD_ID"]
# Comma-separated list of allowed roles, e.g. "Wiki Editor,Wiki Admin"
ALLOWED_ROLES = [r.strip() for r in os.getenv("ALLOWED_ROLES", "Wiki Editor,Wiki Admin,Wiki Discord Moderator,Verified Editor").split(",")]

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
        await interaction.response.send_message(
            f"⏳ Upload started for `{page_name}` ({page_type.value}). This may take a while..."
        )
        msg = await interaction.original_response()

    try:
        quoted_page_name = f'"{page_name}"' if " " in page_name else page_name
        start_time = time.time()

        process = await asyncio.create_subprocess_exec(
            "python3", "images.py", page_type.value, quoted_page_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # define updater function here
        async def progress_updater():
            while True:
                await asyncio.sleep(30)
                if process.returncode is not None:
                    break
                elapsed = int(time.time() - start_time)
                await msg.edit(
                    content=f"⏳ Upload for `{page_name}` ({page_type.value}) still running... ({elapsed}s elapsed)"
                )

        # start the updater task
        updater_task = asyncio.create_task(progress_updater())

        stdout, stderr = await process.communicate()
        updater_task.cancel()
        elapsed = int(time.time() - start_time)

        # Save logs to txt
        log_content = f"=== STDOUT ===\n{stdout.decode()}\n\n=== STDERR ===\n{stderr.decode()}"
        log_file = discord.File(io.BytesIO(log_content.encode()), filename=f"upload_{page_name}.txt")

        if process.returncode == 0:
            await msg.edit(content=f"✅ Upload successful for `{page_name}` ({page_type.value}) in {elapsed}s!")
            await interaction.followup.send(
                f"📌 Upload successful for `{page_name}` ({page_type.value}) in {elapsed}s.",
                file=log_file
            )
        else:
            await msg.edit(content=f"❌ Upload failed for `{page_name}` ({page_type.value}) in {elapsed}s.")
            await interaction.followup.send(
                f"📌 Upload failed for `{page_name}` ({page_type.value}) in {elapsed}s.",
                file=log_file
            )

    except Exception as e:
        elapsed = int(time.time() - start_time)
        await msg.edit(content=f"⚠️ Error while running script after {elapsed}s:\n```{e}```")


# --- START BOT ---
bot.run(DISCORD_TOKEN)
