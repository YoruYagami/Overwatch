import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from db import AsyncSessionLocal, User, Voucher, Subscription, SubscriptionTier, VoucherType
from bot.utils.embeds import Embeds

logger = logging.getLogger("vulnlab.registration")


class RegistrationCog(commands.Cog):
    """Commands for user registration and voucher activation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register", description="Register your account and optionally link Patreon")
    async def register(self, interaction: discord.Interaction):
        """Register a new user account."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Check if already registered
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                # Show existing account info
                embed = discord.Embed(
                    title="📋 Account Already Registered",
                    description="Your account is already registered!",
                    color=0x3498DB,
                    timestamp=datetime.utcnow(),
                )
                embed.add_field(name="Discord", value=f"{interaction.user}", inline=True)
                embed.add_field(
                    name="Patreon",
                    value=f"✅ Linked ({existing_user.patreon_email})" if existing_user.patreon_id else "❌ Not linked",
                    inline=True,
                )

                if existing_user.has_active_subscription:
                    sub = existing_user.active_subscription
                    embed.add_field(
                        name="Subscription",
                        value=f"✅ {sub.tier.value.title()} (expires <t:{int(sub.expires_at.timestamp())}:R>)",
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Subscription",
                        value="❌ No active subscription\nUse `/activate <voucher>` to activate",
                        inline=False,
                    )

                # Add Patreon link button if not linked
                view = None
                if not existing_user.patreon_id:
                    view = PatreonLinkView()

                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return

            # Create new user
            new_user = User(
                discord_id=interaction.user.id,
                discord_username=interaction.user.name,
                discord_discriminator=interaction.user.discriminator,
            )
            session.add(new_user)
            await session.commit()

            embed = discord.Embed(
                title="✅ Registration Successful!",
                description="Welcome to VulnLab! Your account has been created.",
                color=0x2ECC71,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(
                name="Next Steps",
                value=(
                    "1️⃣ Use `/activate <voucher>` to redeem a voucher\n"
                    "2️⃣ Or link your Patreon for automatic access\n"
                    "3️⃣ Then use `/vpn` to generate your VPN pack"
                ),
                inline=False,
            )

            view = PatreonLinkView()
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

            logger.info(f"New user registered: {interaction.user} (ID: {interaction.user.id})")

    @app_commands.command(name="activate", description="Activate a subscription voucher")
    @app_commands.describe(voucher_code="The voucher code to activate")
    async def activate(self, interaction: discord.Interaction, voucher_code: str):
        """Activate a voucher code."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get or create user
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Not Registered",
                        "You need to register first! Use `/register` to get started.",
                    ),
                    ephemeral=True,
                )
                return

            if user.is_banned:
                await interaction.followup.send(
                    embed=Embeds.error("Account Banned", user.ban_reason or "No reason provided"),
                    ephemeral=True,
                )
                return

            # Find voucher
            voucher_code_clean = voucher_code.strip().upper()
            result = await session.execute(
                select(Voucher).where(Voucher.code == voucher_code_clean)
            )
            voucher = result.scalar_one_or_none()

            if not voucher:
                await interaction.followup.send(
                    embed=Embeds.error("Invalid Voucher", "This voucher code does not exist."),
                    ephemeral=True,
                )
                return

            if voucher.is_used:
                await interaction.followup.send(
                    embed=Embeds.error("Voucher Already Used", "This voucher has already been redeemed."),
                    ephemeral=True,
                )
                return

            if voucher.expires_at and voucher.expires_at < datetime.utcnow():
                await interaction.followup.send(
                    embed=Embeds.error("Voucher Expired", "This voucher code has expired."),
                    ephemeral=True,
                )
                return

            # Determine subscription tier based on voucher type
            tier = SubscriptionTier.BASIC if voucher.voucher_type == VoucherType.DAYS_90 else SubscriptionTier.PRO

            # Calculate expiration
            expires_at = datetime.utcnow() + timedelta(days=voucher.duration_days)

            # Check for existing active subscription and extend if exists
            if user.has_active_subscription:
                active_sub = user.active_subscription
                # Extend from current expiration
                expires_at = active_sub.expires_at + timedelta(days=voucher.duration_days)
                active_sub.expires_at = expires_at
                active_sub.tier = tier  # Upgrade tier if needed
            else:
                # Create new subscription
                subscription = Subscription(
                    user_id=user.id,
                    tier=tier,
                    source="voucher",
                    voucher_id=voucher.id,
                    expires_at=expires_at,
                )
                session.add(subscription)

            # Mark voucher as used
            voucher.is_used = True
            voucher.redeemed_by = user.id
            voucher.redeemed_at = datetime.utcnow()

            await session.commit()

            embed = Embeds.success(
                "Voucher Activated!",
                f"Your {voucher.duration_days}-day subscription has been activated.",
            )
            embed.add_field(name="Tier", value=tier.value.title(), inline=True)
            embed.add_field(
                name="Expires",
                value=f"<t:{int(expires_at.timestamp())}:F>",
                inline=True,
            )
            embed.add_field(
                name="Next Step",
                value="Use `/vpn` to generate your VPN configuration!",
                inline=False,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"Voucher {voucher_code_clean} activated by {interaction.user} (ID: {interaction.user.id})")

    @app_commands.command(name="status", description="Check your subscription status")
    async def status(self, interaction: discord.Interaction):
        """Check subscription status."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Not Registered",
                        "You need to register first! Use `/register` to get started.",
                    ),
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="📋 Account Status",
                color=0x3498DB,
                timestamp=datetime.utcnow(),
            )
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)

            embed.add_field(
                name="Patreon",
                value=f"✅ Linked" if user.patreon_id else "❌ Not linked",
                inline=True,
            )
            embed.add_field(
                name="Member Since",
                value=f"<t:{int(user.created_at.timestamp())}:D>",
                inline=True,
            )

            if user.has_active_subscription:
                sub = user.active_subscription
                embed.add_field(
                    name="Subscription",
                    value=f"✅ **{sub.tier.value.title()}**",
                    inline=True,
                )
                embed.add_field(
                    name="Source",
                    value=sub.source.title(),
                    inline=True,
                )
                embed.add_field(
                    name="Expires",
                    value=f"<t:{int(sub.expires_at.timestamp())}:R>",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Subscription",
                    value="❌ **No active subscription**",
                    inline=False,
                )
                embed.add_field(
                    name="Get Access",
                    value="Use `/activate <voucher>` or link Patreon",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)


class PatreonLinkView(discord.ui.View):
    """View with Patreon link button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Link Patreon", style=discord.ButtonStyle.link, url="https://www.patreon.com/oauth2/authorize")
    async def link_patreon(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # Link buttons don't need callbacks


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))
