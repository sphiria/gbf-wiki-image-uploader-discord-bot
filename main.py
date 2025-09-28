import os
import subprocess
import discord
from discord import app_commands

# --- CONFIG ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = 377751105334935553  # your server ID
ALLOWED_ROLE_NAME = "Wiki Editor"  # role allowed to run uploads

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
# Enforce valid choices for page type
@app_commands.choices(page_type=[app_commands.Choice(name=pt, value=pt) for pt in PAGE_TYPES])
async def upload(interaction: discord.Interaction, page_type: app_commands.Choice[str], page_name: str):
    # Check user role
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        await interaction.response.send_message("⚠️ Could not find you in the server.", ephemeral=True)
        return

    if ALLOWED_ROLE_NAME not in [role.name for role in member.roles]:
        await interaction.response.send_message(
            f"❌ You must have the `{ALLOWED_ROLE_NAME}` role to use this command.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        # Automatically quote page names if needed
        quoted_page_name = f'"{page_name}"' if " " in page_name else page_name

        # Run uploader script
        result = subprocess.run(
            ["python", "images.py", page_type.value, quoted_page_name],
            text=True,
            capture_output=True
        )

        if result.returncode == 0:
            await interaction.followup.send(f"✅ Upload successful for `{page_name}` ({page_type.value})!\n```{result.stdout}```")
        else:
            await interaction.followup.send(f"❌ Upload failed:\n```{result.stderr}```")

    except Exception as e:
        await interaction.followup.send(f"⚠️ Error while running script:\n```{e}```")


# --- START BOT ---
bot.run(DISCORD_TOKEN)
