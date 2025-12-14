"""
Patreon Integration Service

Syncs Patreon pledges with Discord roles and VulnLab subscriptions.
Uses Patreon API v2 for campaign member management.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

import aiohttp
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import User, Subscription, SubscriptionTier

logger = logging.getLogger("vulnlab.patreon")


# =============================================================================
# PATREON TIER MAPPING (loaded from settings)
# =============================================================================

def get_patreon_tier_map() -> Dict[str, SubscriptionTier]:
    """Build tier map from settings."""
    tier_map = {}
    if settings.patreon_tier1_id:
        tier_map[settings.patreon_tier1_id] = SubscriptionTier.PATREON_TIER1
    if settings.patreon_tier2_id:
        tier_map[settings.patreon_tier2_id] = SubscriptionTier.PATREON_TIER2
    if settings.patreon_tier3_id:
        tier_map[settings.patreon_tier3_id] = SubscriptionTier.PATREON_TIER3
    return tier_map


def get_discord_role_map() -> Dict[SubscriptionTier, int]:
    """Build Discord role map from settings."""
    role_map = {}
    if settings.discord_role_tier1:
        role_map[SubscriptionTier.PATREON_TIER1] = settings.discord_role_tier1
    if settings.discord_role_tier2:
        role_map[SubscriptionTier.PATREON_TIER2] = settings.discord_role_tier2
    if settings.discord_role_tier3:
        role_map[SubscriptionTier.PATREON_TIER3] = settings.discord_role_tier3
    return role_map


class PatreonService:
    """
    Patreon API integration service.

    Handles:
    - Fetching campaign members/pledges
    - Syncing Patreon status with database
    - Triggering Discord role updates
    """

    BASE_URL = "https://www.patreon.com/api/oauth2/v2"

    def __init__(self, discord_bot=None):
        self.access_token = settings.patreon_creator_access_token
        self.campaign_id = settings.patreon_campaign_id
        self.discord_bot = discord_bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                }
            )
        return self._session

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
    ) -> Optional[Dict]:
        """Make authenticated API request to Patreon."""
        if not self.access_token:
            logger.error("Patreon access token not configured")
            return None

        try:
            session = await self._get_session()

            async with session.request(
                method,
                f"{self.BASE_URL}{endpoint}",
                params=params,
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(f"Patreon API error: {resp.status} - {text}")
                    return None

                return await resp.json()

        except Exception as e:
            logger.error(f"Patreon API request failed: {e}")
            return None

    # =========================================================================
    # CAMPAIGN & MEMBERS
    # =========================================================================

    async def get_campaign(self) -> Optional[Dict]:
        """Get campaign details."""
        result = await self._api_request(
            "GET",
            "/campaigns",
            params={
                "fields[campaign]": "created_at,patron_count,summary,url",
            }
        )

        if result and "data" in result:
            campaigns = result["data"]
            if campaigns:
                return campaigns[0]

        return None

    async def get_campaign_members(
        self,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all campaign members/patrons.

        Returns list of members with their user info and pledge status.
        """
        if not self.campaign_id:
            logger.error("Patreon campaign ID not configured")
            return []

        members = []
        cursor = None

        while True:
            params = {
                "include": "user,currently_entitled_tiers",
                "fields[member]": "email,full_name,patron_status,pledge_relationship_start,currently_entitled_amount_cents,last_charge_date,last_charge_status",
                "fields[user]": "email,full_name,social_connections",
                "fields[tier]": "title,amount_cents",
                "page[size]": "100",
            }

            if cursor:
                params["page[cursor]"] = cursor

            result = await self._api_request(
                "GET",
                f"/campaigns/{self.campaign_id}/members",
                params=params,
            )

            if not result:
                break

            # Parse members
            data = result.get("data", [])
            included = result.get("included", [])

            # Build lookup maps for included data
            users_map = {}
            tiers_map = {}

            for item in included:
                if item["type"] == "user":
                    users_map[item["id"]] = item
                elif item["type"] == "tier":
                    tiers_map[item["id"]] = item

            for member in data:
                attrs = member.get("attributes", {})
                relationships = member.get("relationships", {})

                # Skip inactive if not requested
                if not include_inactive:
                    if attrs.get("patron_status") != "active_patron":
                        continue

                # Get user data
                user_rel = relationships.get("user", {}).get("data", {})
                user_id = user_rel.get("id")
                user_data = users_map.get(user_id, {})
                user_attrs = user_data.get("attributes", {})

                # Get entitled tiers
                tier_rels = relationships.get("currently_entitled_tiers", {}).get("data", [])
                entitled_tiers = []

                for tier_rel in tier_rels:
                    tier_id = tier_rel.get("id")
                    tier_data = tiers_map.get(tier_id, {})
                    tier_attrs = tier_data.get("attributes", {})
                    entitled_tiers.append({
                        "id": tier_id,
                        "title": tier_attrs.get("title"),
                        "amount_cents": tier_attrs.get("amount_cents"),
                    })

                # Get Discord connection
                discord_id = None
                social = user_attrs.get("social_connections", {})
                if social and "discord" in social:
                    discord_info = social.get("discord")
                    if discord_info:
                        discord_id = discord_info.get("user_id")

                members.append({
                    "patreon_id": user_id,
                    "member_id": member["id"],
                    "email": user_attrs.get("email") or attrs.get("email"),
                    "full_name": user_attrs.get("full_name") or attrs.get("full_name"),
                    "patron_status": attrs.get("patron_status"),
                    "pledge_cents": attrs.get("currently_entitled_amount_cents", 0),
                    "last_charge_date": attrs.get("last_charge_date"),
                    "last_charge_status": attrs.get("last_charge_status"),
                    "discord_id": discord_id,
                    "entitled_tiers": entitled_tiers,
                })

            # Check for next page
            links = result.get("links", {})
            next_url = links.get("next")

            if next_url:
                # Extract cursor from next URL
                import urllib.parse
                parsed = urllib.parse.urlparse(next_url)
                query = urllib.parse.parse_qs(parsed.query)
                cursor = query.get("page[cursor]", [None])[0]
            else:
                break

        logger.info(f"Fetched {len(members)} active Patreon members")
        return members

    # =========================================================================
    # SYNC OPERATIONS
    # =========================================================================

    def get_subscription_tier(self, member: Dict) -> Optional[SubscriptionTier]:
        """Determine subscription tier from Patreon member data."""
        entitled_tiers = member.get("entitled_tiers", [])

        if not entitled_tiers:
            return None

        # Get highest tier
        highest_tier = None
        highest_amount = 0

        tier_map = get_patreon_tier_map()

        for tier in entitled_tiers:
            tier_id = tier.get("id")
            amount = tier.get("amount_cents", 0)

            if tier_id in tier_map and amount > highest_amount:
                highest_tier = tier_map[tier_id]
                highest_amount = amount

        # If no mapped tier, use generic based on amount
        if highest_tier is None and entitled_tiers:
            # Fallback: assign tier based on pledge amount
            pledge_cents = member.get("pledge_cents", 0)

            if pledge_cents >= 3000:  # $30+
                highest_tier = SubscriptionTier.PATREON_TIER3
            elif pledge_cents >= 1500:  # $15+
                highest_tier = SubscriptionTier.PATREON_TIER2
            elif pledge_cents >= 500:  # $5+
                highest_tier = SubscriptionTier.PATREON_TIER1

        return highest_tier

    async def sync_member(
        self,
        member: Dict,
        db_session: AsyncSession,
    ) -> Tuple[bool, str]:
        """
        Sync a single Patreon member with the database.

        Returns (success, message) tuple.
        """
        discord_id = member.get("discord_id")
        patreon_id = member.get("patreon_id")
        email = member.get("email")

        if not discord_id:
            return False, f"No Discord linked for {email or patreon_id}"

        # Find user by Discord ID
        result = await db_session.execute(
            select(User).where(User.discord_id == int(discord_id))
        )
        user = result.scalar_one_or_none()

        if not user:
            return False, f"Discord user {discord_id} not registered"

        # Update Patreon info
        user.patreon_id = patreon_id
        user.patreon_email = email

        # Determine tier
        tier = self.get_subscription_tier(member)

        if not tier:
            return False, f"No valid tier for {email}"

        user.patreon_tier = tier.value

        # Check for existing Patreon subscription
        result = await db_session.execute(
            select(Subscription).where(
                and_(
                    Subscription.user_id == user.id,
                    Subscription.source == "patreon",
                    Subscription.is_active == True,
                )
            )
        )
        existing_sub = result.scalar_one_or_none()

        patron_status = member.get("patron_status")

        if patron_status == "active_patron":
            # Active patron - create or update subscription
            if existing_sub:
                # Update tier and extend expiration
                existing_sub.tier = tier
                existing_sub.expires_at = datetime.utcnow() + timedelta(days=35)  # Buffer for payment cycle
                logger.info(f"Updated Patreon subscription for user {user.id}")
            else:
                # Create new subscription
                new_sub = Subscription(
                    user_id=user.id,
                    tier=tier,
                    is_active=True,
                    source="patreon",
                    started_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(days=35),
                )
                db_session.add(new_sub)
                logger.info(f"Created Patreon subscription for user {user.id}")
        else:
            # Not active - deactivate subscription
            if existing_sub:
                existing_sub.is_active = False
                user.patreon_tier = None
                logger.info(f"Deactivated Patreon subscription for user {user.id}")

        await db_session.commit()
        return True, f"Synced {email} -> tier {tier.value if tier else 'none'}"

    async def sync_all_members(
        self,
        db_session: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Sync all Patreon members with database.

        Returns sync results summary.
        """
        members = await self.get_campaign_members(include_inactive=True)

        results = {
            "total": len(members),
            "synced": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }

        for member in members:
            try:
                success, message = await self.sync_member(member, db_session)

                if success:
                    results["synced"] += 1
                else:
                    results["skipped"] += 1
                    if "not registered" not in message:
                        results["errors"].append(message)

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{member.get('email')}: {str(e)}")
                logger.error(f"Failed to sync member: {e}")

        logger.info(
            f"Patreon sync complete: {results['synced']} synced, "
            f"{results['skipped']} skipped, {results['failed']} failed"
        )

        return results

    # =========================================================================
    # DISCORD ROLE SYNC
    # =========================================================================

    async def sync_discord_roles(
        self,
        user: User,
        guild_id: int,
    ) -> bool:
        """
        Sync Discord roles based on user's Patreon tier.

        Requires discord_bot to be set.
        """
        if not self.discord_bot:
            logger.warning("Discord bot not set, cannot sync roles")
            return False

        try:
            guild = self.discord_bot.get_guild(guild_id)
            if not guild:
                logger.error(f"Guild {guild_id} not found")
                return False

            member = guild.get_member(user.discord_id)
            if not member:
                # Try fetching
                try:
                    member = await guild.fetch_member(user.discord_id)
                except:
                    logger.error(f"Member {user.discord_id} not found in guild")
                    return False

            # Get role map
            role_map = get_discord_role_map()

            # Get current Patreon roles on member
            current_patreon_roles = set()
            for role in member.roles:
                if role.id in role_map.values():
                    current_patreon_roles.add(role.id)

            # Determine target role
            target_role_id = None

            if user.patreon_tier:
                try:
                    tier = SubscriptionTier(user.patreon_tier)
                    target_role_id = role_map.get(tier)
                except ValueError:
                    pass

            # Remove old roles
            roles_to_remove = []
            for role_id in current_patreon_roles:
                if role_id != target_role_id:
                    role = guild.get_role(role_id)
                    if role:
                        roles_to_remove.append(role)

            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Patreon sync")

            # Add new role
            if target_role_id and target_role_id not in current_patreon_roles:
                role = guild.get_role(target_role_id)
                if role:
                    await member.add_roles(role, reason="Patreon sync")

            logger.info(f"Synced Discord roles for user {user.discord_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to sync Discord roles: {e}")
            return False

    async def sync_all_discord_roles(
        self,
        db_session: AsyncSession,
        guild_id: int,
    ) -> Dict[str, int]:
        """Sync Discord roles for all users with Patreon data."""
        result = await db_session.execute(
            select(User).where(User.patreon_id.isnot(None))
        )
        users = result.scalars().all()

        stats = {"success": 0, "failed": 0}

        for user in users:
            if await self.sync_discord_roles(user, guild_id):
                stats["success"] += 1
            else:
                stats["failed"] += 1

            # Rate limiting
            await asyncio.sleep(0.5)

        return stats

    # =========================================================================
    # WEBHOOK HANDLING
    # =========================================================================

    async def handle_webhook(
        self,
        event_type: str,
        data: Dict,
        db_session: AsyncSession,
    ) -> bool:
        """
        Handle Patreon webhook events.

        Event types:
        - members:pledge:create
        - members:pledge:update
        - members:pledge:delete
        """
        try:
            member_data = data.get("data", {})
            included = data.get("included", [])

            # Extract user info from included
            user_data = None
            for item in included:
                if item.get("type") == "user":
                    user_data = item
                    break

            if not user_data:
                logger.warning("No user data in webhook")
                return False

            # Build member dict
            attrs = member_data.get("attributes", {})
            user_attrs = user_data.get("attributes", {})

            # Get Discord ID from social connections
            discord_id = None
            social = user_attrs.get("social_connections", {})
            if social and "discord" in social:
                discord_info = social.get("discord")
                if discord_info:
                    discord_id = discord_info.get("user_id")

            member = {
                "patreon_id": user_data.get("id"),
                "member_id": member_data.get("id"),
                "email": user_attrs.get("email"),
                "full_name": user_attrs.get("full_name"),
                "patron_status": attrs.get("patron_status"),
                "pledge_cents": attrs.get("currently_entitled_amount_cents", 0),
                "discord_id": discord_id,
                "entitled_tiers": [],  # Will use pledge amount fallback
            }

            # Set patron status based on event
            if event_type == "members:pledge:delete":
                member["patron_status"] = "former_patron"

            # Sync member
            success, message = await self.sync_member(member, db_session)

            if success and discord_id:
                # Sync Discord roles
                await self.sync_discord_roles(
                    await db_session.get(User, member["patreon_id"]),
                    settings.discord_guild_id,
                )

            logger.info(f"Handled webhook {event_type}: {message}")
            return success

        except Exception as e:
            logger.error(f"Webhook handling failed: {e}")
            return False

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


# =============================================================================
# BACKGROUND SYNC TASK
# =============================================================================

class PatreonSyncTask:
    """Background task for periodic Patreon sync."""

    def __init__(
        self,
        patreon_service: PatreonService,
        db_session_factory,
        interval_minutes: int = 60,
    ):
        self.patreon_service = patreon_service
        self.db_session_factory = db_session_factory
        self.interval = interval_minutes * 60
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the background sync task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Patreon sync task started")

    async def stop(self):
        """Stop the background sync task."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Patreon sync task stopped")

    async def _run(self):
        """Main sync loop."""
        while self._running:
            try:
                async with self.db_session_factory() as session:
                    results = await self.patreon_service.sync_all_members(session)

                    # Also sync Discord roles
                    if self.patreon_service.discord_bot:
                        await self.patreon_service.sync_all_discord_roles(
                            session,
                            settings.discord_guild_id,
                        )

                    logger.info(f"Periodic Patreon sync: {results}")

            except Exception as e:
                logger.error(f"Patreon sync task error: {e}")

            await asyncio.sleep(self.interval)
