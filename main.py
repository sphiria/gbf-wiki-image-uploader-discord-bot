import os
import subprocess
import discord
from discord import app_commands

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
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        await interaction.response.send_message("⚠️ Could not find you in the server.", ephemeral=True)
        return

    # ✅ Allow if server owner OR has one of the allowed roles
    if (
        interaction.user.id != interaction.guild.owner_id
        and not any(role.name in ALLOWED_ROLES for role in member.roles)
    ):
        await interaction.response.send_message(
            f"❌ You must have one of the following roles to use this command: {', '.join(ALLOWED_ROLES)}, "
            f"or be the server owner.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        quoted_page_name = f'"{page_name}"' if " " in page_name else page_name

        result = subprocess.run(
            ["python", "images.py", page_type.value, quoted_page_name],
            text=True,
            capture_output=True
        )

        if result.returncode == 0:
            if len(result.stdout) > 1900:
                # Send output as a text file
                file = discord.File(io.StringIO(result.stdout), filename="upload_log.txt")
                await interaction.followup.send(
                    f"✅ Upload successful for `{page_name}` ({page_type.value})! (see log attached)",
                    file=file
                )
            else:
                await interaction.followup.send(
                    f"✅ Upload successful for `{page_name}` ({page_type.value})!\n```{result.stdout}```"
                )
        else:
            if len(result.stderr) > 1900:
                # Send error output as a text file
                file = discord.File(io.StringIO(result.stderr), filename="upload_error.txt")
                await interaction.followup.send(
                    f"❌ Upload failed for `{page_name}` ({page_type.value}) (see error log attached)",
                    file=file
                )
            else:
                await interaction.followup.send(f"❌ Upload failed:\n```{result.stderr}```")

    except Exception as e:
        await interaction.followup.send(f"⚠️ Error while running script:\n```{e}```")


# --- START BOT ---
bot.run(DISCORD_TOKEN)
