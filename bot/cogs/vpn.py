import io
import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from config import settings
from db import AsyncSessionLocal, User, VPNConfig
from bot.utils.embeds import Embeds
from bot.utils.checks import has_subscription
from api.services.wireguard import WireGuardService

logger = logging.getLogger("vulnlab.vpn")


class VPNCog(commands.Cog):
    """Commands for VPN configuration generation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.wg_service = WireGuardService()

    @app_commands.command(name="vpn", description="Generate or retrieve your WireGuard VPN configuration")
    @has_subscription()
    async def vpn(self, interaction: discord.Interaction):
        """Generate VPN configuration for the user."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get user
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.followup.send(
                    embed=Embeds.error("Not Registered", "Use `/register` first."),
                    ephemeral=True,
                )
                return

            # Check for existing valid VPN config
            result = await session.execute(
                select(VPNConfig)
                .where(VPNConfig.user_id == user.id)
                .where(VPNConfig.is_active == True)
                .where(VPNConfig.is_revoked == False)
                .order_by(VPNConfig.created_at.desc())
            )
            existing_config = result.scalar_one_or_none()

            if existing_config and not existing_config.is_expired:
                # Return existing config
                config_content = self.wg_service.generate_client_config(
                    private_key=existing_config.private_key,
                    address=existing_config.assigned_ip,
                )

                embed = Embeds.vpn_config(
                    username=str(interaction.user),
                    assigned_ip=existing_config.assigned_ip,
                    expires_at=existing_config.expires_at,
                    server_endpoint=settings.wg_server_endpoint,
                )
                embed.description = "Here's your existing VPN configuration."

                # Create config file
                config_file = discord.File(
                    io.BytesIO(config_content.encode()),
                    filename=f"vulnlab-{interaction.user.name}.conf",
                )

                view = VPNActionsView(existing_config.id)
                await interaction.followup.send(embed=embed, file=config_file, view=view, ephemeral=True)
                return

            # Generate new VPN config
            try:
                # Generate keys
                private_key, public_key = self.wg_service.generate_keypair()

                # Allocate IP
                assigned_ip = await self.wg_service.allocate_ip(session)

                # Create VPN config record
                expires_at = datetime.utcnow() + timedelta(days=settings.vpn_cert_validity_days)

                vpn_config = VPNConfig(
                    user_id=user.id,
                    private_key=private_key,
                    public_key=public_key,
                    assigned_ip=assigned_ip,
                    expires_at=expires_at,
                )
                session.add(vpn_config)

                # Add peer to WireGuard server
                await self.wg_service.add_peer(
                    public_key=public_key,
                    allowed_ips=f"{assigned_ip}/32",
                )

                await session.commit()

                # Generate client config
                config_content = self.wg_service.generate_client_config(
                    private_key=private_key,
                    address=assigned_ip,
                )

                embed = Embeds.vpn_config(
                    username=str(interaction.user),
                    assigned_ip=assigned_ip,
                    expires_at=expires_at,
                    server_endpoint=settings.wg_server_endpoint,
                )

                # Create config file
                config_file = discord.File(
                    io.BytesIO(config_content.encode()),
                    filename=f"vulnlab-{interaction.user.name}.conf",
                )

                view = VPNActionsView(vpn_config.id)
                await interaction.followup.send(embed=embed, file=config_file, view=view, ephemeral=True)

                logger.info(f"VPN config generated for {interaction.user} (ID: {interaction.user.id}), IP: {assigned_ip}")

            except Exception as e:
                logger.error(f"Failed to generate VPN config: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("VPN Generation Failed", f"An error occurred: {str(e)}"),
                    ephemeral=True,
                )

    @app_commands.command(name="vpn-revoke", description="Revoke your current VPN configuration")
    @has_subscription()
    async def vpn_revoke(self, interaction: discord.Interaction):
        """Revoke current VPN configuration."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.discord_id == interaction.user.id)
            )
            user = result.scalar_one_or_none()

            if not user:
                await interaction.followup.send(
                    embed=Embeds.error("Not Registered", "Use `/register` first."),
                    ephemeral=True,
                )
                return

            # Find active config
            result = await session.execute(
                select(VPNConfig)
                .where(VPNConfig.user_id == user.id)
                .where(VPNConfig.is_active == True)
                .where(VPNConfig.is_revoked == False)
            )
            vpn_config = result.scalar_one_or_none()

            if not vpn_config:
                await interaction.followup.send(
                    embed=Embeds.warning("No Active VPN", "You don't have an active VPN configuration."),
                    ephemeral=True,
                )
                return

            # Revoke
            vpn_config.is_revoked = True
            vpn_config.is_active = False

            # Remove peer from WireGuard server
            try:
                await self.wg_service.remove_peer(vpn_config.public_key)
            except Exception as e:
                logger.warning(f"Failed to remove peer from WG server: {e}")

            await session.commit()

            await interaction.followup.send(
                embed=Embeds.success(
                    "VPN Revoked",
                    "Your VPN configuration has been revoked.\nUse `/vpn` to generate a new one.",
                ),
                ephemeral=True,
            )

            logger.info(f"VPN config revoked for {interaction.user} (ID: {interaction.user.id})")


class VPNActionsView(discord.ui.View):
    """View with VPN action buttons."""

    def __init__(self, vpn_config_id: int):
        super().__init__(timeout=300)
        self.vpn_config_id = vpn_config_id

    @discord.ui.button(label="Regenerate", style=discord.ButtonStyle.primary, emoji="🔄")
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Regenerate VPN configuration."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(VPNConfig).where(VPNConfig.id == self.vpn_config_id)
            )
            vpn_config = result.scalar_one_or_none()

            if vpn_config:
                # Revoke old config
                vpn_config.is_revoked = True
                vpn_config.is_active = False
                await session.commit()

        # Trigger new VPN generation
        await interaction.followup.send(
            "🔄 Old config revoked. Use `/vpn` to generate a new configuration.",
            ephemeral=True,
        )

    @discord.ui.button(label="Show QR Code", style=discord.ButtonStyle.secondary, emoji="📱")
    async def show_qr(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show QR code for mobile import."""
        await interaction.response.send_message(
            "📱 QR code generation coming soon!\nFor now, transfer the `.conf` file to your device.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VPNCog(bot))
