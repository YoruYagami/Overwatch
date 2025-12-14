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

    @app_commands.command(name="activate", description="Activate with voucher code or Patreon email")
    @app_commands.describe(code_or_email="Voucher code OR your Patreon email address")
    async def activate(self, interaction: discord.Interaction, code_or_email: str):
        """Activate subscription via voucher code or Patreon email."""
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

            input_clean = code_or_email.strip()

            # Check if it's an email (Patreon activation)
            if EMAIL_REGEX.match(input_clean):
                await self._activate_patreon(interaction, session, user, input_clean.lower())
            else:
                # Treat as voucher code
                await self._activate_voucher(interaction, session, user, input_clean.upper())

    async def _activate_patreon(
        self,
        interaction: discord.Interaction,
        session,
        user: User,
        email: str,
    ):
        """Activate subscription via Patreon email verification."""
        # Check if Patreon service is available
        if not hasattr(self.bot, 'patreon_service') or not self.bot.patreon_service:
            await interaction.followup.send(
                embed=Embeds.error(
                    "Patreon Not Configured",
                    "Patreon integration is not available. Please use a voucher code or contact an admin.",
                ),
                ephemeral=True,
            )
            return

        # Check if user already has Patreon linked
        if user.patreon_email and user.patreon_email.lower() != email:
            await interaction.followup.send(
                embed=Embeds.error(
                    "Different Email",
                    f"Your account is already linked to a different Patreon email.\nLinked: `{user.patreon_email}`",
                ),
                ephemeral=True,
            )
            return

        # Fetch Patreon members and find by email
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
            if member_email == email:
                patron = member
                break

        if not patron:
            await interaction.followup.send(
                embed=Embeds.error(
                    "Email Not Found",
                    "This email is not associated with an active Patreon subscription.\n\n"
                    "Make sure you're using the same email as your Patreon account.",
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
            tier = SubscriptionTier.PATREON_TIER1  # Default to tier 1

        # Update user with Patreon info
        user.patreon_id = patron.get("patreon_id")
        user.patreon_email = email
        user.patreon_tier = tier.value

        # Create or update subscription (Patreon subs renew monthly, give 35 days buffer)
        expires_at = datetime.utcnow() + timedelta(days=35)

        # Check for existing Patreon subscription
        existing_sub = None
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

        # Format tier name nicely
        tier_name = tier.value.replace("patreon_", "").replace("_", " ").title()

        embed = Embeds.success(
            "Patreon Verified!",
            f"Your Patreon subscription has been linked and activated.",
        )
        embed.add_field(name="Email", value=f"`{email}`", inline=True)
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

        logger.info(f"Patreon activated for {interaction.user} (ID: {interaction.user.id}) - email: {email}")

    async def _activate_voucher(
        self,
        interaction: discord.Interaction,
        session,
        user: User,
        voucher_code: str,
    ):
        """Activate subscription via voucher code."""
        # Find voucher
        result = await session.execute(
            select(Voucher).where(Voucher.code == voucher_code)
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

        logger.info(f"Voucher {voucher_code} activated by {interaction.user} (ID: {interaction.user.id})")

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
