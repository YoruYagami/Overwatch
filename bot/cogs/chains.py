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
    Chain,
    ChainInstance,
    ChainMachineInstance,
    InstanceStatus,
)
from bot.utils.embeds import Embeds
from bot.utils.checks import has_subscription
from api.services.proxmox import ProxmoxService

logger = logging.getLogger("vulnlab.chains")


class ChainsCog(commands.Cog):
    """Commands for managing machine chains."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.proxmox = ProxmoxService()

    async def chain_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for chain names."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Chain)
                .where(Chain.is_active == True)
                .where(Chain.name.ilike(f"%{current}%"))
                .limit(25)
            )
            chains = result.scalars().all()

            return [
                app_commands.Choice(name=f"{c.display_name} ({c.difficulty})", value=c.name)
                for c in chains
            ]

    @app_commands.command(name="chain", description="Control panel for a machine chain")
    @app_commands.describe(name="The chain name")
    @app_commands.autocomplete(name=chain_autocomplete)
    @has_subscription()
    async def chain(self, interaction: discord.Interaction, name: str):
        """Show chain control panel."""
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

            # Get chain with machines
            result = await session.execute(
                select(Chain)
                .where(Chain.name == name.lower())
                .options(selectinload(Chain.machines))
            )
            chain = result.scalar_one_or_none()

            if not chain:
                await interaction.followup.send(
                    embed=Embeds.error("Chain Not Found", f"Chain `{name}` does not exist."),
                    ephemeral=True,
                )
                return

            if not chain.is_active:
                await interaction.followup.send(
                    embed=Embeds.warning("Chain Unavailable", "This chain is currently unavailable."),
                    ephemeral=True,
                )
                return

            # Check for existing instance
            result = await session.execute(
                select(ChainInstance)
                .where(ChainInstance.user_id == user.id)
                .where(ChainInstance.chain_id == chain.id)
                .where(ChainInstance.status.not_in([InstanceStatus.TERMINATED, InstanceStatus.STOPPED]))
                .options(selectinload(ChainInstance.machine_instances))
            )
            instance = result.scalar_one_or_none()

            # Build machine info
            machines_info = []
            if instance:
                for mi in instance.machine_instances:
                    machines_info.append({
                        "name": f"Machine {mi.id}",
                        "status": mi.status.value,
                        "ip": mi.assigned_ip or "N/A",
                    })
            else:
                for cm in chain.machines:
                    machines_info.append({
                        "name": cm.machine_template.display_name if cm.machine_template else f"Machine {cm.id}",
                        "status": "stopped",
                        "ip": "N/A",
                    })

            # Create embed
            embed = Embeds.chain_panel(
                chain_name=chain.display_name,
                machines=machines_info,
                status=instance.status.value if instance else "stopped",
                expires_at=instance.expires_at if instance else None,
            )

            embed.add_field(
                name="Description",
                value=chain.description or "No description available.",
                inline=False,
            )

            embed.add_field(
                name="Estimated Time",
                value=f"⏱️ ~{chain.estimated_time_hours} hours",
                inline=True,
            )

            # Create view with buttons
            view = ChainControlView(
                chain_id=chain.id,
                instance_id=instance.id if instance else None,
                user_id=user.id,
                is_running=instance.status == InstanceStatus.RUNNING if instance else False,
            )

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="chains", description="List all available chains")
    @has_subscription()
    async def list_chains(self, interaction: discord.Interaction):
        """List all available chains."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Chain)
                .where(Chain.is_active == True)
                .options(selectinload(Chain.machines))
                .order_by(Chain.difficulty, Chain.name)
            )
            chains = result.scalars().all()

            if not chains:
                await interaction.followup.send(
                    embed=Embeds.info("No Chains", "No chains are currently available."),
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🔗 Available Chains",
                description="Use `/chain <name>` to start a chain.",
                color=0x9B59B6,
                timestamp=datetime.utcnow(),
            )

            for chain in chains:
                difficulty_emoji = {
                    "easy": "🟢",
                    "medium": "🟡",
                    "hard": "🟠",
                    "insane": "🔴",
                }.get(chain.difficulty, "⚪")

                machine_count = len(chain.machines)

                embed.add_field(
                    name=f"{difficulty_emoji} {chain.display_name}",
                    value=(
                        f"`{chain.name}` • {machine_count} machines\n"
                        f"⏱️ ~{chain.estimated_time_hours}h • {chain.description[:100] if chain.description else 'No description'}..."
                    ),
                    inline=False,
                )

            embed.set_footer(text=f"Total: {len(chains)} chains")
            await interaction.followup.send(embed=embed, ephemeral=True)


class ChainControlView(discord.ui.View):
    """Control panel view for a chain."""

    def __init__(
        self,
        chain_id: int,
        instance_id: Optional[int],
        user_id: int,
        is_running: bool,
    ):
        super().__init__(timeout=300)
        self.chain_id = chain_id
        self.instance_id = instance_id
        self.user_id = user_id
        self.proxmox = ProxmoxService()

        # Update button states
        self.start_btn.disabled = is_running
        self.stop_btn.disabled = not is_running
        self.extend_btn.disabled = not is_running

    @discord.ui.button(label="Start All", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Start all machines in the chain."""
        await interaction.response.defer(ephemeral=True)

        async with AsyncSessionLocal() as session:
            # Get chain with machines
            result = await session.execute(
                select(Chain)
                .where(Chain.id == self.chain_id)
                .options(selectinload(Chain.machines))
            )
            chain = result.scalar_one_or_none()

            if not chain:
                await interaction.followup.send(
                    embed=Embeds.error("Error", "Chain not found."),
                    ephemeral=True,
                )
                return

            # Check for existing instance
            result = await session.execute(
                select(ChainInstance)
                .where(ChainInstance.user_id == self.user_id)
                .where(ChainInstance.chain_id == self.chain_id)
                .where(ChainInstance.status.not_in([InstanceStatus.TERMINATED, InstanceStatus.STOPPED]))
            )
            existing = result.scalar_one_or_none()

            if existing and existing.status == InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Already Running", "This chain is already running."),
                    ephemeral=True,
                )
                return

            # Create chain instance
            if existing:
                chain_instance = existing
                chain_instance.status = InstanceStatus.PROVISIONING
            else:
                chain_instance = ChainInstance(
                    user_id=self.user_id,
                    chain_id=self.chain_id,
                    status=InstanceStatus.PROVISIONING,
                )
                session.add(chain_instance)
                await session.flush()

            await session.commit()
            await session.refresh(chain_instance)

            # Start all machines
            started_machines = []
            try:
                for chain_machine in chain.machines:
                    template = chain_machine.machine_template

                    # Create machine instance
                    machine_instance = ChainMachineInstance(
                        chain_instance_id=chain_instance.id,
                        machine_template_id=template.id,
                        status=InstanceStatus.STARTING,
                    )
                    session.add(machine_instance)
                    await session.flush()

                    # Start via Proxmox
                    vm_info = await self.proxmox.start_machine(
                        template_id=template.proxmox_template_id,
                        instance_id=machine_instance.id,
                        node=template.proxmox_node,
                    )

                    machine_instance.proxmox_vmid = vm_info["vmid"]
                    machine_instance.assigned_ip = vm_info["ip"]
                    machine_instance.status = InstanceStatus.RUNNING

                    started_machines.append({
                        "name": template.display_name,
                        "ip": vm_info["ip"],
                    })

                # Update chain instance
                chain_instance.status = InstanceStatus.RUNNING
                chain_instance.started_at = datetime.utcnow()
                chain_instance.expires_at = datetime.utcnow() + timedelta(
                    hours=chain.estimated_time_hours
                )

                await session.commit()

                # Create success embed
                embed = Embeds.success(
                    "Chain Started!",
                    f"**{chain.display_name}** is now running.",
                )

                machines_text = "\n".join([f"• **{m['name']}**: `{m['ip']}`" for m in started_machines])
                embed.add_field(name="Machines", value=machines_text, inline=False)
                embed.add_field(
                    name="Expires",
                    value=f"<t:{int(chain_instance.expires_at.timestamp())}:R>",
                    inline=True,
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

                logger.info(f"Chain {chain.name} started for user {self.user_id}")

            except Exception as e:
                chain_instance.status = InstanceStatus.ERROR
                chain_instance.error_message = str(e)
                await session.commit()

                logger.error(f"Failed to start chain: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("Start Failed", f"Failed to start chain: {str(e)}"),
                    ephemeral=True,
                )

    @discord.ui.button(label="Stop All", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stop all machines in the chain."""
        await interaction.response.defer(ephemeral=True)

        if not self.instance_id:
            await interaction.followup.send(
                embed=Embeds.warning("Not Running", "This chain is not running."),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChainInstance)
                .where(ChainInstance.id == self.instance_id)
                .options(selectinload(ChainInstance.machine_instances))
            )
            chain_instance = result.scalar_one_or_none()

            if not chain_instance or chain_instance.status != InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Running", "This chain is not running."),
                    ephemeral=True,
                )
                return

            chain_instance.status = InstanceStatus.STOPPING
            await session.commit()

            try:
                # Stop all machines
                for mi in chain_instance.machine_instances:
                    if mi.proxmox_vmid:
                        await self.proxmox.stop_machine(
                            vmid=mi.proxmox_vmid,
                            node="pve",  # TODO: Get from machine template
                        )
                        mi.status = InstanceStatus.STOPPED

                chain_instance.status = InstanceStatus.STOPPED
                await session.commit()

                await interaction.followup.send(
                    embed=Embeds.success("Chain Stopped", "All machines have been stopped."),
                    ephemeral=True,
                )

                logger.info(f"Chain stopped for user {self.user_id}")

            except Exception as e:
                chain_instance.status = InstanceStatus.ERROR
                chain_instance.error_message = str(e)
                await session.commit()

                logger.error(f"Failed to stop chain: {e}")
                await interaction.followup.send(
                    embed=Embeds.error("Stop Failed", f"Failed to stop chain: {str(e)}"),
                    ephemeral=True,
                )

    @discord.ui.button(label="Extend (+2h)", style=discord.ButtonStyle.primary, emoji="⏱️", row=0)
    async def extend_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Extend chain time."""
        await interaction.response.defer(ephemeral=True)

        if not self.instance_id:
            await interaction.followup.send(
                embed=Embeds.warning("Not Running", "This chain is not running."),
                ephemeral=True,
            )
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ChainInstance).where(ChainInstance.id == self.instance_id)
            )
            chain_instance = result.scalar_one_or_none()

            if not chain_instance or chain_instance.status != InstanceStatus.RUNNING:
                await interaction.followup.send(
                    embed=Embeds.warning("Not Running", "This chain is not running."),
                    ephemeral=True,
                )
                return

            # Check extend limit
            if chain_instance.extended_count >= settings.max_extend_hours:
                await interaction.followup.send(
                    embed=Embeds.warning(
                        "Extend Limit Reached",
                        f"You can only extend a chain {settings.max_extend_hours} times.",
                    ),
                    ephemeral=True,
                )
                return

            # Extend by 2 hours for chains
            chain_instance.expires_at = chain_instance.expires_at + timedelta(hours=2)
            chain_instance.extended_count += 1
            await session.commit()

            await interaction.followup.send(
                embed=Embeds.success(
                    "Time Extended!",
                    f"Chain extended by 2 hours.\nNew expiration: <t:{int(chain_instance.expires_at.timestamp())}:R>",
                ),
                ephemeral=True,
            )

            logger.info(f"Chain extended for user {self.user_id}, new expiry: {chain_instance.expires_at}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ChainsCog(bot))
