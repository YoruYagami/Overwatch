import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import commands

from config import settings
from db import init_db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vulnlab")


class VulnLabBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            description="VulnLab - Vulnerable Machine Lab Platform",
        )

    async def setup_hook(self):
        """Load cogs and sync commands."""
        logger.info("Initializing database...")
        await init_db()

        # Load all cogs
        cogs_path = Path(__file__).parent / "cogs"
        for cog_file in cogs_path.glob("*.py"):
            if cog_file.name.startswith("_"):
                continue
            cog_name = f"bot.cogs.{cog_file.stem}"
            try:
                await self.load_extension(cog_name)
                logger.info(f"Loaded cog: {cog_name}")
            except Exception as e:
                logger.error(f"Failed to load cog {cog_name}: {e}")

        # Sync commands to guild
        guild = discord.Object(id=settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info(f"Synced commands to guild {settings.discord_guild_id}")

    async def on_ready(self):
        logger.info(f"Bot ready! Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guild(s)")

        # Set presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="vulnerable machines",
            )
        )

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Command error: {error}", exc_info=error)


bot = VulnLabBot()


async def main():
    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
