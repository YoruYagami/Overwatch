import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from config import settings
from db import (
    AsyncSessionLocal,
    User,
    RTLab,
    RTLabSession,
    RTLabStatus,
)
from bot.utils.embeds import Embeds
from bot.utils.checks import has_subscription
from api.services.proxmox import ProxmoxService

logger = logging.getLogger("vulnlab.rtl")


class RTLCog(commands.Cog):
    """Commands for Red Team Labs (shared instances)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.proxmox = ProxmoxService()

    async def rtl_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for RTL names."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RTLab)
                .where(RTLab.is_active == True)
                .where(RTLab.name.ilike(f"%{current}%"))
                .limit(25)
            )
            labs = result.scalars().all()

            return [
                app_commands.Choice(name=f"{lab.display_name}", value=lab.name)
                for lab in labs
            ]

    @app_commands.command(name="rtl", description="Red Team Lab control panel")
    @app_commands.describe(name="The Red Team Lab name")
    @app_commands.autocomplete(name=rtl_autocomplete)
    @has_subscription()
    async def rtl(self, interaction: discord.Interaction, name: str):
        """Show RTL control panel."""
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

            # Get RTL
            result = await session.execute(
                select(RTLab)
                .where(RTLab.name == name.lower())
                .options(selectinload(RTLab.sessions))
            )
            lab = result.scalar_one_or_none()

            if not lab:
                await interaction.followup.send(
                    embed=Embeds.error("RTL Not Found", f"Red Team Lab `{name}` does not exist."),
                    ephemeral=True,
                )
                return

            if not lab.is_active:
                await interaction.followup.send(
                    embed=Embeds.warning("RTL Unavailable", "This Red Team Lab is currently unavailable."),
                    ephemeral=True,
                )
                return

            # Count current participants in current session
            current_session = lab.current_session_id or 0
            result = await session.execute(
                select(func.count(RTLabSession.id))
                .where(RTLabSession.rtlab_id == lab.id)
                .where(RTLabSession.session_number == current_session)
                .where(RTLabSession.left_at == None)
            )
            participant_count = result.scalar() or 0

            # Count reset votes
            result = await session.execute(
                select(func.count(RTLabSession.id))
                .where(RTLabSession.rtlab_id == lab.id)
                .where(RTLabSession.session_number == current_session)
                .where(RTLabSession.has_voted_reset == True)
            )
            reset_votes = result.scalar() or 0

            # Check if user is participating
            result = await session.execute(
                select(RTLabSession)
                .where(RTLabSession.rtlab_id == lab.id)
                .where(RTLabSession.user_id == user.id)
                .where(RTLabSession.session_number == current_session)
                .where(RTLabSession.left_at == None)
            )
            user_session = result.scalar_one_or_none()

            # Create embed
            embed = Embeds.rtlab_panel(
                lab_name=lab.display_name,
                participants=participant_count,
                max_participants=lab.max_participants,
                reset_votes=reset_votes,
                votes_required=lab.reset_votes_required,
                status=lab.status.value,
            )

            embed.add_field(
                name="Description",
                value=lab.description or "No description available.",
                inline=False,
            )

            if user_session:
                embed.add_field(
                    name="Your Status",
                    value=f"✅ Participating (since <t:{int(user_session.joined_at.timestamp())}:R>)",
                    inline=True,
                )
                embed.add_field(
                    name="Reset Vote",
                    value="✅ Voted" if user_session.has_voted_reset else "❌ Not voted",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Your Status",
                    value="❌ Not participating",
                    inline=True,
                )

            # Create view
            view = RTLControlView(
                rtlab_id=lab.id,
                user_id=user.id,
                is_participating=user_session is not None,
                has_voted=user_session.has_voted_reset if user_session else False,
                session_number=current_session,
            )

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="rtls", description="List all available Red Team Labs")
    @has_subscription()
    async def list_rtls(self, interaction: discord.Interaction):
        """List all available RTLs."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RTLab)
                .where(RTLab.is_active == True)
                .order_by(RTLab.name)
            )
            labs = result.scalars().all()

            if not labs:
                await interaction.followup.send(
                    embed=Embeds.info("No RTLs", "No Red Team Labs are currently available."),
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🎯 Red Team Labs",
                description="Use `/rtl <name>` to join a lab.",
                color=0x9B59B6,
                timestamp=datetime.utcnow(),
            )

            for lab in labs:
                status_emoji = {
                    "available": "🟢",
                    "in_progress": "🟡",
                    "completed": "✅",
                }.get(lab.status.value, "⚪")

                # Get participant count
                result = await session.execute(
                    select(func.count(RTLabSession.id))
                    .where(RTLabSession.rtlab_id == lab.id)
                    .where(RTLabSession.session_number == (lab.current_session_id or 0))
                    .where(RTLabSession.left_at == None)
                )
                participants = result.scalar() or 0

                embed.add_field(
                    name=f"{status_emoji} {lab.display_name}",
                    value=(
                        f"`{lab.name}` • 👥 {participants}/{lab.max_participants}\n"
                        f"{lab.description[:80] if lab.description else 'No description'}..."
                    ),
                    inline=False,
                )

            embed.set_footer(text=f"Total: {len(labs)} labs")
            await interaction.followup.send(embed=embed, ephemeral=True)


class RTLControlView(discord.ui.View):
    """Control panel view for an RTL."""

    def __init__(
        self,
        rtlab_id: int,
        user_id: int,
        is_participating: bool,
        has_voted: bool,
        session_number: int,
    ):
        super().__init__(timeout=300)
        self.rtlab_id = rtlab_id
        self.user_id = user_id
        self.session_number = session_number
        self.proxmox = ProxmoxService()

        # Update button states
        self.join_btn.disabled = is_participating
        self.leave_btn.disabled = not is_participating
        self.vote_reset_btn.disabled = not is_participating or has_voted

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="➡️", row=0)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Join the RTL."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get RTL
            result = await session.execute(
                select(RTLab).where(RTLab.id == self.rtlab_id)
            )
            lab = result.scalar_one_or_none()

            if not lab:
                await interaction.followup.send(
                    embed=Embeds.error("Error", "Lab not found."),
                    ephemeral=True,
                )
                return

            # Check participant count
            result = await session.execute(
                select(func.count(RTLabSession.id))
                .where(RTLabSession.rtlab_id == lab.id)
                .where(RTLabSession.session_number == self.session_number)
                .where(RTLabSession.left_at == None)
            )
            participant_count = result.scalar() or 0

            if participant_count >= lab.max_participants:
                await interaction.followup.send(
                    embed=Embeds.warning("Lab Full", "This lab has reached maximum capacity."),
                    ephemeral=True,
                )
                return

            # Check if already participating
            result = await session.execute(
                select(RTLabSession)
                .where(RTLabSession.rtlab_id == lab.id)
                .where(RTLabSession.user_id == self.user_id)
                .where(RTLabSession.session_number == self.session_number)
                .where(RTLabSession.left_at == None)
            )
            existing = result.scalar_one_or_none()

            if existing:
                await interaction.followup.send(
                    embed=Embeds.warning("Already Joined", "You're already in this lab."),
                    ephemeral=True,
                )
                return

            # Create session
            rtl_session = RTLabSession(
                rtlab_id=lab.id,
                user_id=self.user_id,
                session_number=self.session_number,
            )
            session.add(rtl_session)

            # Update lab status if first participant
            if participant_count == 0:
                lab.status = RTLabStatus.IN_PROGRESS

            await session.commit()

            await interaction.followup.send(
                embed=Embeds.success(
                    "Joined RTL!",
                    f"You've joined **{lab.display_name}**.\n\nYour VPN should now have access to the lab environment.",
                ),
                ephemeral=True,
            )

            logger.info(f"User {self.user_id} joined RTL {lab.name}")

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, emoji="⬅️", row=0)
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Leave the RTL."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RTLabSession)
                .where(RTLabSession.rtlab_id == self.rtlab_id)
                .where(RTLabSession.user_id == self.user_id)
                .where(RTLabSession.session_number == self.session_number)
                .where(RTLabSession.left_at == None)
            )
            rtl_session = result.scalar_one_or_none()

            if not rtl_session:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Joined", "You're not in this lab."),
                    ephemeral=True,
                )
                return

            rtl_session.left_at = datetime.utcnow()
            await session.commit()

            await interaction.followup.send(
                embed=Embeds.success("Left RTL", "You've left the Red Team Lab."),
                ephemeral=True,
            )

            logger.info(f"User {self.user_id} left RTL {self.rtlab_id}")

    @discord.ui.button(label="Vote Reset", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def vote_reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Vote to reset the RTL."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get user session
            result = await session.execute(
                select(RTLabSession)
                .where(RTLabSession.rtlab_id == self.rtlab_id)
                .where(RTLabSession.user_id == self.user_id)
                .where(RTLabSession.session_number == self.session_number)
                .where(RTLabSession.left_at == None)
            )
            rtl_session = result.scalar_one_or_none()

            if not rtl_session:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Joined", "You need to join the lab first."),
                    ephemeral=True,
                )
                return

            if rtl_session.has_voted_reset:
                await interaction.followup.send(
                    embed=Embeds.warning("Already Voted", "You've already voted for reset."),
                    ephemeral=True,
                )
                return

            # Register vote
            rtl_session.has_voted_reset = True
            await session.flush()

            # Get RTL and check vote count
            result = await session.execute(
                select(RTLab).where(RTLab.id == self.rtlab_id)
            )
            lab = result.scalar_one_or_none()

            result = await session.execute(
                select(func.count(RTLabSession.id))
                .where(RTLabSession.rtlab_id == self.rtlab_id)
                .where(RTLabSession.session_number == self.session_number)
                .where(RTLabSession.has_voted_reset == True)
            )
            vote_count = result.scalar() or 0

            await session.commit()

            if vote_count >= lab.reset_votes_required:
                # Trigger reset
                await self._reset_lab(session, lab)

                await interaction.followup.send(
                    embed=Embeds.success(
                        "Lab Reset!",
                        f"Enough votes received ({vote_count}/{lab.reset_votes_required}).\n"
                        "The lab is being reset to its initial state.",
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=Embeds.success(
                        "Vote Recorded",
                        f"Your reset vote has been recorded.\n"
                        f"Current votes: {vote_count}/{lab.reset_votes_required}",
                    ),
                    ephemeral=True,
                )

            logger.info(f"User {self.user_id} voted to reset RTL {self.rtlab_id}")

    async def _reset_lab(self, session, lab: RTLab):
        """Reset the lab to initial state."""
        # Increment session number
        lab.current_session_id = (lab.current_session_id or 0) + 1
        lab.status = RTLabStatus.AVAILABLE

        # Reset via Proxmox would go here
        # await self.proxmox.reset_rtl_environment(lab)

        await session.commit()
        logger.info(f"RTL {lab.name} reset, new session: {lab.current_session_id}")

    @discord.ui.button(label="Extend (+1h)", style=discord.ButtonStyle.secondary, emoji="⏱️", row=1)
    async def extend_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Vote to extend lab time."""
        await interaction.response.send_message(
            embed=Embeds.info("Coming Soon", "Lab time extension feature coming soon!"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RTLCog(bot))
