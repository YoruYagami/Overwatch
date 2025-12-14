import logging
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from db import AsyncSessionLocal, User, Voucher, Subscription, SubscriptionTier, VoucherType
from bot.utils.embeds import Embeds

logger = logging.getLogger("vulnlab.registration")

# Simple email regex
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class RegistrationCog(commands.Cog):
    """Commands for user registration and voucher activation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register", description="Register with your Patreon email")
    @app_commands.describe(email="Your Patreon email address")
    async def register(self, interaction: discord.Interaction, email: str):
        """Register a new user account with Patreon email verification."""
        await interaction.response.defer(ephemeral=True)

        # Validate email format
        email_clean = email.strip().lower()
        if not EMAIL_REGEX.match(email_clean):
            await interaction.followup.send(
                embed=Embeds.error(
                    "Invalid Email",
                    "Please provide a valid email address.",
                ),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            # Check if already registered
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            existing_user = result.scalar_one_or_none()

            if existing_user:
                # User exists - check if they want to re-verify or already verified
                if existing_user.patreon_email and existing_user.has_active_subscription:
                    embed = discord.Embed(
                        title="📋 Already Registered",
                        description="Your account is already registered and active!",
                        color=0x3498DB,
                        timestamp=datetime.utcnow(),
                    )
                    embed.add_field(name="Email", value=f"`{existing_user.patreon_email}`", inline=True)
                    sub = existing_user.active_subscription
                    embed.add_field(
                        name="Subscription",
                        value=f"✅ {sub.tier.value.replace('patreon_', '').replace('_', ' ').title()}",
                        inline=True,
                    )
                    embed.add_field(
                        name="Expires",
                        value=f"<t:{int(sub.expires_at.timestamp())}:R>",
                        inline=True,
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                # User exists but no active sub - allow re-registration with new email
                user = existing_user
            else:
                # Create new user
                user = User(
                    discord_id=interaction.user.id,
                    discord_username=interaction.user.name,
                    discord_discriminator=interaction.user.discriminator,
                )
                session.add(user)
                await session.flush()  # Get the user ID

            # Verify email against Patreon
            if not hasattr(self.bot, 'patreon_service') or not self.bot.patreon_service:
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Patreon Not Configured",
                        "Patreon integration is not available. Please contact an admin.",
                    ),
                    ephemeral=True,
                )
                return

            # Fetch Patreon members
            try:
                members = await self.bot.patreon_service.get_campaign_members()
            except Exception as e:
                logger.error(f"Failed to fetch Patreon members: {e}")
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Patreon Error",
                        "Failed to verify your Patreon status. Please try again later.",
                    ),
                    ephemeral=True,
                )
                return

            # Find member by email
            patron = None
            for member in members:
                member_email = member.get("email", "").lower()
                if member_email == email_clean:
                    patron = member
                    break

            if not patron:
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Email Not Found",
                        "This email is not associated with an active Patreon subscription.\n\n"
                        "Make sure you're using the **same email** as your Patreon account.",
                    ),
                    ephemeral=True,
                )
                return

            # Check patron status
            if patron.get("patron_status") != "active_patron":
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Subscription Not Active",
                        "Your Patreon subscription is not currently active.\n"
                        "Please check your Patreon payment status.",
                    ),
                    ephemeral=True,
                )
                return

            # Get tier from Patreon
            tier = self.bot.patreon_service.get_subscription_tier(patron)
            if not tier:
                tier = SubscriptionTier.PATREON_TIER1

            # Update user with Patreon info
            user.patreon_id = patron.get("patreon_id")
            user.patreon_email = email_clean
            user.patreon_tier = tier.value

            # Create or update subscription
            expires_at = datetime.utcnow() + timedelta(days=35)

            existing_sub = None
            if existing_user:
                for sub in user.subscriptions:
                    if sub.source == "patreon" and sub.is_active:
                        existing_sub = sub
                        break

            if existing_sub:
                existing_sub.tier = tier
                existing_sub.expires_at = expires_at
            else:
                subscription = Subscription(
                    user_id=user.id,
                    tier=tier,
                    source="patreon",
                    expires_at=expires_at,
                )
                session.add(subscription)

            await session.commit()

            # Format tier name
            tier_name = tier.value.replace("patreon_", "").replace("_", " ").title()

            embed = Embeds.success(
                "Registration Complete!",
                "Your Patreon subscription has been verified and your account is now active.",
            )
            embed.add_field(name="Email", value=f"`{email_clean}`", inline=True)
            embed.add_field(name="Tier", value=tier_name, inline=True)
            embed.add_field(
                name="Expires",
                value=f"<t:{int(expires_at.timestamp())}:R>\n*(auto-renews with Patreon)*",
                inline=False,
            )
            embed.add_field(
                name="Next Step",
                value="Use `/vpn` to generate your VPN configuration!",
                inline=False,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

            logger.info(f"User registered: {interaction.user} (ID: {interaction.user.id}) - email: {email_clean}")

    @app_commands.command(name="activate", description="Activate a subscription voucher")
    @app_commands.describe(voucher_code="The voucher code to redeem")
    async def activate(self, interaction: discord.Interaction, voucher_code: str):
        """Activate subscription via voucher code."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get user
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.followup.send(
                    embed=Embeds.error(
                        "Not Registered",
                        "You need to register first! Use `/register <email>` with your Patreon email.",
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
                expires_at = active_sub.expires_at + timedelta(days=voucher.duration_days)
                active_sub.expires_at = expires_at
                active_sub.tier = tier
            else:
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
                    value="Use `/register <email>` with your Patreon email\nor `/activate <voucher>` for voucher codes",
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))
