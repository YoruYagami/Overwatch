import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import settings
from db import (
    AsyncSessionLocal,
    User,
    MachineTemplate,
    MachineInstance,
    InstanceStatus,
)
from bot.utils.embeds import Embeds
from bot.utils.checks import has_subscription
from api.services.proxmox import ProxmoxService

logger = logging.getLogger("vulnlab.machines")


class MachinesCog(commands.Cog):
    """Commands for managing individual machines."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.proxmox = ProxmoxService()

    async def machine_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for machine names."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MachineTemplate)
                .where(MachineTemplate.is_active == True)
                .where(MachineTemplate.name.ilike(f"%{current}%"))
                .limit(25)
            )
            machines = result.scalars().all()

            return [
                app_commands.Choice(name=f"{m.display_name} ({m.difficulty})", value=m.name)
                for m in machines
            ]

    @app_commands.command(name="machine", description="Control panel for a vulnerable machine")
    @app_commands.describe(name="The machine name")
    @app_commands.autocomplete(name=machine_autocomplete)
    @has_subscription()
    async def machine(self, interaction: discord.Interaction, name: str):
        """Show machine control panel."""
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

            # Get machine template
            result = await session.execute(
                select(MachineTemplate).where(MachineTemplate.name == name.lower())
            )
            template = result.scalar_one_or_none()

            if not template:
                await interaction.followup.send(
                    embed=Embeds.error("Machine Not Found", f"Machine `{name}` does not exist."),
                    ephemeral=True,
                )
                return

            if not template.is_active:
                await interaction.followup.send(
                    embed=Embeds.warning("Machine Unavailable", "This machine is currently unavailable."),
                    ephemeral=True,
                )
                return

            # Check for existing instance
            result = await session.execute(
                select(MachineInstance)
                .where(MachineInstance.user_id == user.id)
                .where(MachineInstance.template_id == template.id)
                .where(MachineInstance.status.not_in([InstanceStatus.TERMINATED, InstanceStatus.STOPPED]))
            )
            instance = result.scalar_one_or_none()

            # Create embed
            embed = Embeds.machine_panel(
                machine_name=template.display_name,
                status=instance.status.value if instance else "stopped",
                ip=instance.assigned_ip if instance else None,
                expires_at=instance.expires_at if instance else None,
                difficulty=template.difficulty,
                os_type=template.os_type,
            )

            embed.add_field(
                name="Description",
                value=template.description or "No description available.",
                inline=False,
            )

            # Create view with buttons
            view = MachineControlView(
                template_id=template.id,
                instance_id=instance.id if instance else None,
                user_id=user.id,
                is_running=instance.status == InstanceStatus.RUNNING if instance else False,
            )

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="machines", description="List all available machines")
    @has_subscription()
    async def list_machines(self, interaction: discord.Interaction):
        """List all available machines."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MachineTemplate)
                .where(MachineTemplate.is_active == True)
                .order_by(MachineTemplate.difficulty, MachineTemplate.name)
            )
            machines = result.scalars().all()

            if not machines:
                await interaction.followup.send(
                    embed=Embeds.info("No Machines", "No machines are currently available."),
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🖥️ Available Machines",
                description=f"Use `/machine <name>` to start a machine.",
                color=0x9B59B6,
                timestamp=datetime.utcnow(),
            )

            # Group by difficulty
            difficulties = {"easy": [], "medium": [], "hard": [], "insane": []}
            for m in machines:
                diff = m.difficulty if m.difficulty in difficulties else "medium"
                os_emoji = "🐧" if m.os_type == "linux" else "🪟"
                difficulties[diff].append(f"{os_emoji} **{m.display_name}** (`{m.name}`)")

            difficulty_emojis = {"easy": "🟢", "medium": "🟡", "hard": "🟠", "insane": "🔴"}

            for diff, machine_list in difficulties.items():
                if machine_list:
                    embed.add_field(
                        name=f"{difficulty_emojis[diff]} {diff.title()}",
                        value="\n".join(machine_list[:10]) + (f"\n... and {len(machine_list) - 10} more" if len(machine_list) > 10 else ""),
                        inline=False,
                    )

            embed.set_footer(text=f"Total: {len(machines)} machines")
            await interaction.followup.send(embed=embed, ephemeral=True)


class MachineControlView(discord.ui.View):
    """Control panel view for a machine."""

    def __init__(
        self,
        template_id: int,
        instance_id: Optional[int],
        user_id: int,
        is_running: bool,
    ):
        super().__init__(timeout=300)
        self.template_id = template_id
        self.instance_id = instance_id
        self.user_id = user_id
        self.proxmox = ProxmoxService()

        # Update button states
        self.start_btn.disabled = is_running
        self.stop_btn.disabled = not is_running
        self.extend_btn.disabled = not is_running

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Start the machine."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get template
            result = await session.execute(
                select(MachineTemplate).where(MachineTemplate.id == self.template_id)
            )
            template = result.scalar_one_or_none()

            if not template:
                await interaction.followup.send(
                    embed=Embeds.error("Error", "Machine template not found."),
                    ephemeral=True,
                )
                return

            # Check if already running
            result = await session.execute(
                select(MachineInstance)
                .where(MachineInstance.user_id == self.user_id)
                .where(MachineInstance.template_id == self.template_id)
                .where(MachineInstance.status.not_in([InstanceStatus.TERMINATED, InstanceStatus.STOPPED]))
            )
            existing = result.scalar_one_or_none()

            if existing and existing.status == InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Already Running", "This machine is already running."),
                    ephemeral=True,
                )
                return

            # Create or update instance
            if existing:
                instance = existing
                instance.status = InstanceStatus.STARTING
            else:
                instance = MachineInstance(
                    user_id=self.user_id,
                    template_id=self.template_id,
                    proxmox_node=template.proxmox_node,
                    status=InstanceStatus.STARTING,
                )
                session.add(instance)

            await session.commit()
            await session.refresh(instance)

            # Start machine via Proxmox
            try:
                vm_info = await self.proxmox.start_machine(
                    template_id=template.proxmox_template_id,
                    instance_id=instance.id,
                    node=template.proxmox_node,
                )

                instance.proxmox_vmid = vm_info["vmid"]
                instance.assigned_ip = vm_info["ip"]
                instance.status = InstanceStatus.RUNNING
                instance.started_at = datetime.utcnow()
                instance.expires_at = datetime.utcnow() + timedelta(hours=settings.default_machine_duration_hours)

                await session.commit()

                embed = Embeds.success(
                    "Machine Started!",
                    f"**{template.display_name}** is now running.",
                )
                embed.add_field(name="IP Address", value=f"`{instance.assigned_ip}`", inline=True)
                embed.add_field(
                    name="Expires",
                    value=f"<t:{int(instance.expires_at.timestamp())}:R>",
                    inline=True,
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(f"Machine {template.name} started for user {self.user_id}, IP: {instance.assigned_ip}")

            except Exception as e:
                instance.status = InstanceStatus.ERROR
                instance.error_message = str(e)
                await session.commit()

                logger.error(f"Failed to start machine: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("Start Failed", f"Failed to start machine: {str(e)}"),
                    ephemeral=True,
                )

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stop the machine."""
        await interaction.response.defer(ephemeral=True)

        if not self.instance_id:
            await interaction.followup.send(
                embed=Embeds.warning("Not Running", "This machine is not running."),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MachineInstance).where(MachineInstance.id == self.instance_id)
            )
            instance = result.scalar_one_or_none()

            if not instance or instance.status != InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Running", "This machine is not running."),
                    ephemeral=True,
                )
                return

            instance.status = InstanceStatus.STOPPING
            await session.commit()

            try:
                await self.proxmox.stop_machine(
                    vmid=instance.proxmox_vmid,
                    node=instance.proxmox_node,
                )

                instance.status = InstanceStatus.STOPPED
                await session.commit()

                await interaction.followup.send(
                    embed=Embeds.success("Machine Stopped", "The machine has been stopped."),
                    ephemeral=True,
                )

                logger.info(f"Machine stopped for user {self.user_id}, VMID: {instance.proxmox_vmid}")

            except Exception as e:
                instance.status = InstanceStatus.ERROR
                instance.error_message = str(e)
                await session.commit()

                logger.error(f"Failed to stop machine: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("Stop Failed", f"Failed to stop machine: {str(e)}"),
                    ephemeral=True,
                )

    @discord.ui.button(label="Extend (+1h)", style=discord.ButtonStyle.primary, emoji="⏱️", row=0)
    async def extend_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Extend machine time."""
        await interaction.response.defer(ephemeral=True)

        if not self.instance_id:
            await interaction.followup.send(
                embed=Embeds.warning("Not Running", "This machine is not running."),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MachineInstance).where(MachineInstance.id == self.instance_id)
            )
            instance = result.scalar_one_or_none()

            if not instance or instance.status != InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Running", "This machine is not running."),
                    ephemeral=True,
                )
                return

            # Check extend limit
            if instance.extended_count >= settings.max_extend_hours:
                await interaction.followup.send(
                    embed=Embeds.warning(
                        "Extend Limit Reached",
                        f"You can only extend a machine {settings.max_extend_hours} times.",
                    ),
                    ephemeral=True,
                )
                return

            # Extend by 1 hour
            instance.expires_at = instance.expires_at + timedelta(hours=1)
            instance.extended_count += 1
            await session.commit()

            await interaction.followup.send(
                embed=Embeds.success(
                    "Time Extended!",
                    f"Machine extended by 1 hour.\nNew expiration: <t:{int(instance.expires_at.timestamp())}:R>",
                ),
                ephemeral=True,
            )

            logger.info(f"Machine extended for user {self.user_id}, new expiry: {instance.expires_at}")

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reset the machine to initial state."""
        await interaction.response.defer(ephemeral=True)

        if not self.instance_id:
            await interaction.followup.send(
                embed=Embeds.warning("Not Running", "This machine is not running."),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MachineInstance)
                .where(MachineInstance.id == self.instance_id)
                .options(selectinload(MachineInstance.template))
            )
            instance = result.scalar_one_or_none()

            if not instance:
                await interaction.followup.send(
                    embed=Embeds.error("Error", "Instance not found."),
                    ephemeral=True,
                )
                return

            try:
                # Reset via Proxmox (stop, restore snapshot, start)
                await self.proxmox.reset_machine(
                    vmid=instance.proxmox_vmid,
                    node=instance.proxmox_node,
                )

                await interaction.followup.send(
                    embed=Embeds.success("Machine Reset", "The machine has been reset to its initial state."),
                    ephemeral=True,
                )

                logger.info(f"Machine reset for user {self.user_id}, VMID: {instance.proxmox_vmid}")

            except Exception as e:
                logger.error(f"Failed to reset machine: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("Reset Failed", f"Failed to reset machine: {str(e)}"),
                    ephemeral=True,
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(MachinesCog(bot))
