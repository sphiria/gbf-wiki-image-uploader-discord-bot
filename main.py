import os
import subprocess
import discord
from discord import app_commands
import io
import asyncio

upload_lock = asyncio.Lock()

# --- CONFIG ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = 377751105334935553  # your server ID
# Comma-separated list of allowed roles, e.g. "Wiki Editor,Wiki Admin"
ALLOWED_ROLES = [r.strip() for r in os.getenv("ALLOWED_ROLES", "Wiki Editor").split(",")]

# Valid page types
PAGE_TYPES = ["character", "weapon", "summon", "class", "skin"]

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
@bot.tree.command(name="upload", description="Upload an image to the wiki")
@app_commands.describe(
    page_type="Type of page",
    page_name="Wiki page name"
)
@app_commands.choices(page_type=[app_commands.Choice(name=pt, value=pt) for pt in PAGE_TYPES])
async def upload(interaction: discord.Interaction, page_type: app_commands.Choice[str], page_name: str):
    # Check user role
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

    # Initial response
    await interaction.response.send_message(
        f"⏳ Upload started for `{page_name}` ({page_type.value}). This may take a while..."
    )
    msg = await interaction.original_response()

    try:
        quoted_page_name = f'"{page_name}"' if " " in page_name else page_name

        # Run images.py asynchronously
        process = await asyncio.create_subprocess_exec(
            "python3", "images.py", page_type.value, quoted_page_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            # Success
            output = stdout.decode().strip()
            # Limit to 1500 chars to avoid hitting Discord's 2000 char limit
            if len(output) > 1500:
                output = output[:1500] + "\n... (output truncated)"
            await msg.edit(content=f"✅ Upload successful for `{page_name}` ({page_type.value})!\n```{output}```")
        else:
            # Error from script
            error_output = stderr.decode().strip()
            if len(error_output) > 1500:
                error_output = error_output[:1500] + "\n... (output truncated)"
            await msg.edit(content=f"❌ Upload failed for `{page_name}`:\n```{error_output}```")

    except Exception as e:
        await msg.edit(content=f"⚠️ Error while running script:\n```{e}```")

# --- START BOT ---
bot.run(DISCORD_TOKEN)
