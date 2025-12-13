from datetime import datetime, timedelta
from typing import Optional, List

import discord


class Colors:
    """Color constants for embeds."""
    SUCCESS = 0x2ECC71  # Green
    ERROR = 0xE74C3C    # Red
    WARNING = 0xF39C12  # Orange
    INFO = 0x3498DB     # Blue
    PURPLE = 0x9B59B6   # Purple
    DARK = 0x2C3E50     # Dark blue


class Embeds:
    """Utility class for creating consistent Discord embeds."""

    @staticmethod
    def success(title: str, description: str = None, **kwargs) -> discord.Embed:
        embed = discord.Embed(
            title=f"✅ {title}",
            description=description,
            color=Colors.SUCCESS,
            timestamp=datetime.utcnow(),
        )
        for key, value in kwargs.items():
            embed.add_field(name=key, value=value, inline=True)
        return embed

    @staticmethod
    def error(title: str, description: str = None) -> discord.Embed:
        return discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=Colors.ERROR,
            timestamp=datetime.utcnow(),
        )

    @staticmethod
    def warning(title: str, description: str = None) -> discord.Embed:
        return discord.Embed(
            title=f"⚠️ {title}",
            description=description,
            color=Colors.WARNING,
            timestamp=datetime.utcnow(),
        )

    @staticmethod
    def info(title: str, description: str = None) -> discord.Embed:
        return discord.Embed(
            title=f"ℹ️ {title}",
            description=description,
            color=Colors.INFO,
            timestamp=datetime.utcnow(),
        )

    @staticmethod
    def machine_panel(
        machine_name: str,
        status: str,
        ip: Optional[str],
        expires_at: Optional[datetime],
        difficulty: str = "medium",
        os_type: str = "linux",
    ) -> discord.Embed:
        """Create a machine control panel embed."""
        status_emoji = {
            "stopped": "🔴",
            "starting": "🟡",
            "running": "🟢",
            "stopping": "🟠",
            "error": "❗",
        }.get(status, "⚪")

        difficulty_emoji = {
            "easy": "🟢",
            "medium": "🟡",
            "hard": "🟠",
            "insane": "🔴",
        }.get(difficulty, "⚪")

        os_emoji = "🐧" if os_type == "linux" else "🪟"

        embed = discord.Embed(
            title=f"🖥️ {machine_name}",
            color=Colors.PURPLE,
            timestamp=datetime.utcnow(),
        )

        embed.add_field(name="Status", value=f"{status_emoji} {status.title()}", inline=True)
        embed.add_field(name="Difficulty", value=f"{difficulty_emoji} {difficulty.title()}", inline=True)
        embed.add_field(name="OS", value=f"{os_emoji} {os_type.title()}", inline=True)

        if ip:
            embed.add_field(name="IP Address", value=f"`{ip}`", inline=True)

        if expires_at:
            remaining = expires_at - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                embed.add_field(name="Time Remaining", value=f"⏱️ {hours}h {minutes}m", inline=True)
            else:
                embed.add_field(name="Time Remaining", value="⏱️ Expired", inline=True)

        return embed

    @staticmethod
    def vpn_config(
        username: str,
        assigned_ip: str,
        expires_at: datetime,
        server_endpoint: str,
    ) -> discord.Embed:
        """Create a VPN configuration embed."""
        embed = discord.Embed(
            title="🔐 VPN Configuration Generated",
            description="Your WireGuard VPN pack has been generated.",
            color=Colors.SUCCESS,
            timestamp=datetime.utcnow(),
        )

        embed.add_field(name="Assigned IP", value=f"`{assigned_ip}`", inline=True)
        embed.add_field(name="Server", value=f"`{server_endpoint}`", inline=True)
        embed.add_field(
            name="Valid Until",
            value=f"<t:{int(expires_at.timestamp())}:F>",
            inline=False,
        )

        embed.set_footer(text=f"Generated for {username}")
        return embed

    @staticmethod
    def subscription_info(
        tier: str,
        expires_at: datetime,
        source: str,
    ) -> discord.Embed:
        """Create a subscription info embed."""
        embed = discord.Embed(
            title="📋 Subscription Status",
            color=Colors.INFO,
            timestamp=datetime.utcnow(),
        )

        tier_display = {
            "free": "🆓 Free",
            "basic": "⭐ Basic (90 days)",
            "pro": "💎 Pro (365 days)",
            "patreon_tier1": "🎖️ Patreon Tier 1",
            "patreon_tier2": "🏆 Patreon Tier 2",
            "patreon_tier3": "👑 Patreon Tier 3",
        }.get(tier, tier)

        embed.add_field(name="Tier", value=tier_display, inline=True)
        embed.add_field(name="Source", value=source.title(), inline=True)
        embed.add_field(
            name="Expires",
            value=f"<t:{int(expires_at.timestamp())}:R>",
            inline=True,
        )

        return embed

    @staticmethod
    def chain_panel(
        chain_name: str,
        machines: List[dict],
        status: str,
        expires_at: Optional[datetime],
    ) -> discord.Embed:
        """Create a chain control panel embed."""
        status_emoji = {
            "stopped": "🔴",
            "starting": "🟡",
            "running": "🟢",
            "stopping": "🟠",
            "error": "❗",
        }.get(status, "⚪")

        embed = discord.Embed(
            title=f"🔗 Chain: {chain_name}",
            color=Colors.PURPLE,
            timestamp=datetime.utcnow(),
        )

        embed.add_field(name="Status", value=f"{status_emoji} {status.title()}", inline=True)
        embed.add_field(name="Machines", value=str(len(machines)), inline=True)

        # List machines
        machine_list = []
        for i, m in enumerate(machines, 1):
            m_status = "🟢" if m.get("status") == "running" else "🔴"
            m_ip = m.get("ip", "N/A")
            machine_list.append(f"{i}. {m_status} **{m['name']}** - `{m_ip}`")

        embed.add_field(
            name="Machine Status",
            value="\n".join(machine_list) if machine_list else "No machines",
            inline=False,
        )

        if expires_at:
            remaining = expires_at - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                embed.add_field(name="Time Remaining", value=f"⏱️ {hours}h {minutes}m", inline=True)

        return embed

    @staticmethod
    def rtlab_panel(
        lab_name: str,
        participants: int,
        max_participants: int,
        reset_votes: int,
        votes_required: int,
        status: str,
    ) -> discord.Embed:
        """Create an RT Lab panel embed."""
        embed = discord.Embed(
            title=f"🎯 Red Team Lab: {lab_name}",
            color=Colors.PURPLE,
            timestamp=datetime.utcnow(),
        )

        status_text = {
            "available": "🟢 Available",
            "in_progress": "🟡 In Progress",
            "completed": "✅ Completed",
        }.get(status, status)

        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(
            name="Participants",
            value=f"👥 {participants}/{max_participants}",
            inline=True,
        )
        embed.add_field(
            name="Reset Votes",
            value=f"🗳️ {reset_votes}/{votes_required}",
            inline=True,
        )

        return embed
