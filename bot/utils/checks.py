from functools import wraps
from typing import Callable

import discord
from discord import app_commands
from sqlalchemy import select

from db import AsyncSessionLocal, User


def has_subscription():
    """Check if user has an active subscription."""
    async def predicate(interaction: discord.Interaction) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.response.send_message(
                    "❌ You need to register first! Use `/register` to get started.",
                    ephemeral=True,
                )
                return False

            if user.is_banned:
                await interaction.response.send_message(
                    f"❌ Your account has been banned. Reason: {user.ban_reason or 'No reason provided'}",
                    ephemeral=True,
                )
                return False

            if not user.has_active_subscription:
                await interaction.response.send_message(
                    "❌ You need an active subscription to use this feature.\n"
                    "Use `/activate <voucher>` to redeem a voucher or link your Patreon with `/register`.",
                    ephemeral=True,
                )
                return False

            return True

    return app_commands.check(predicate)


def is_admin():
    """Check if user has admin permissions."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ This command requires administrator permissions.",
                ephemeral=True,
            )
            return False
        return True

    return app_commands.check(predicate)


def is_registered():
    """Check if user is registered (but doesn't require subscription)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.response.send_message(
                    "❌ You need to register first! Use `/register` to get started.",
                    ephemeral=True,
                )
                return False

            if user.is_banned:
                await interaction.response.send_message(
                    f"❌ Your account has been banned. Reason: {user.ban_reason or 'No reason provided'}",
                    ephemeral=True,
                )
                return False

            return True

    return app_commands.check(predicate)
