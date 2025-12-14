"""
WireGuard Service via wg-easy API

Manages WireGuard peers using wg-easy container REST API.
https://github.com/wg-easy/wg-easy
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import VPNConfig

logger = logging.getLogger("vulnlab.wireguard")


class WireGuardService:
    """
    WireGuard management service via wg-easy API.

    wg-easy provides a REST API for managing WireGuard peers,
    including automatic config generation and expiration.
    """

    def __init__(self):
        self.api_url = getattr(settings, 'wg_easy_api_url', 'http://localhost:51821')
        self.password = getattr(settings, 'wg_easy_password', '')
        self.server_endpoint = settings.wg_server_endpoint
        self.network = settings.wg_network

        self._session: Optional[aiohttp.ClientSession] = None
        self._session_cookie: Optional[str] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _authenticate(self) -> bool:
        """Authenticate with wg-easy and get session cookie."""
        try:
            session = await self._get_session()

            async with session.post(
                f"{self.api_url}/api/session",
                json={"password": self.password},
            ) as resp:
                if resp.status == 200:
                    # Get session cookie
                    self._session_cookie = resp.cookies.get('connect.sid')
                    logger.debug("Authenticated with wg-easy")
                    return True
                else:
                    logger.error(f"wg-easy auth failed: {resp.status}")
                    return False

        except Exception as e:
            logger.error(f"wg-easy auth error: {e}")
            return False

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        json: Dict = None,
        retry: bool = True,
    ) -> Optional[Dict]:
        """Make authenticated API request to wg-easy."""
        if not self._session_cookie:
            if not await self._authenticate():
                raise RuntimeError("Failed to authenticate with wg-easy")

        try:
            session = await self._get_session()
            cookies = {'connect.sid': self._session_cookie} if self._session_cookie else {}

            async with session.request(
                method,
                f"{self.api_url}{endpoint}",
                json=json,
                cookies=cookies,
            ) as resp:
                if resp.status == 401 and retry:
                    # Session expired, re-authenticate
                    self._session_cookie = None
                    return await self._api_request(method, endpoint, json, retry=False)

                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(f"wg-easy API error: {resp.status} - {text}")
                    return None

                if resp.content_type == 'application/json':
                    return await resp.json()
                else:
                    return {"text": await resp.text()}

        except Exception as e:
            logger.error(f"wg-easy API request failed: {e}")
            return None

    async def create_client(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Create a new WireGuard client/peer.

        Returns:
            Dict with client info including id, name, publicKey, etc.
        """
        result = await self._api_request(
            "POST",
            "/api/wireguard/client",
            json={"name": name},
        )

        if result:
            logger.info(f"Created WireGuard client: {name}")

        return result

    async def delete_client(self, client_id: str) -> bool:
        """Delete a WireGuard client."""
        result = await self._api_request(
            "DELETE",
            f"/api/wireguard/client/{client_id}",
        )

        if result is not None:
            logger.info(f"Deleted WireGuard client: {client_id}")
            return True

        return False

    async def get_client_config(self, client_id: str) -> Optional[str]:
        """Get WireGuard configuration file for a client."""
        result = await self._api_request(
            "GET",
            f"/api/wireguard/client/{client_id}/configuration",
        )

        if result and "text" in result:
            return result["text"]

        return None

    async def enable_client(self, client_id: str) -> bool:
        """Enable a WireGuard client."""
        result = await self._api_request(
            "POST",
            f"/api/wireguard/client/{client_id}/enable",
        )
        return result is not None

    async def disable_client(self, client_id: str) -> bool:
        """Disable a WireGuard client."""
        result = await self._api_request(
            "POST",
            f"/api/wireguard/client/{client_id}/disable",
        )
        return result is not None

    async def get_clients(self) -> List[Dict[str, Any]]:
        """Get all WireGuard clients."""
        result = await self._api_request("GET", "/api/wireguard/client")
        return result if isinstance(result, list) else []

    async def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific client by ID."""
        clients = await self.get_clients()
        for client in clients:
            if client.get("id") == client_id:
                return client
        return None

    # =========================================================================
    # High-level methods for VulnLab integration
    # =========================================================================

    async def generate_vpn_config(
        self,
        user_id: int,
        username: str,
        session: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Generate VPN configuration for a user.

        Creates client in wg-easy, stores in database, returns config.
        """
        # Check for existing active config
        result = await session.execute(
            select(VPNConfig)
            .where(VPNConfig.user_id == user_id)
            .where(VPNConfig.is_active == True)
            .where(VPNConfig.is_revoked == False)
        )
        existing = result.scalar_one_or_none()

        if existing and not existing.is_expired:
            # Return existing config
            config = await self.get_client_config(existing.public_key)
            return {
                "config": config,
                "client_id": existing.public_key,
                "expires_at": existing.expires_at,
                "is_new": False,
            }

        # Create new client in wg-easy
        client_name = f"vulnlab-{user_id}-{username}"
        client = await self.create_client(client_name)

        if not client:
            raise RuntimeError("Failed to create WireGuard client")

        client_id = client.get("id")
        public_key = client.get("publicKey")
        address = client.get("address")

        # Get configuration
        config = await self.get_client_config(client_id)

        if not config:
            raise RuntimeError("Failed to get WireGuard configuration")

        # Calculate expiration
        expires_at = datetime.utcnow() + timedelta(days=settings.vpn_cert_validity_days)

        # Store in database
        vpn_config = VPNConfig(
            user_id=user_id,
            private_key=client_id,  # Store client ID for reference
            public_key=client_id,   # Use client ID as identifier
            assigned_ip=address.split("/")[0] if address else "",
            expires_at=expires_at,
        )
        session.add(vpn_config)
        await session.commit()

        logger.info(f"Generated VPN config for user {user_id}, client: {client_id}")

        return {
            "config": config,
            "client_id": client_id,
            "address": address,
            "expires_at": expires_at,
            "is_new": True,
        }

    async def revoke_vpn_config(
        self,
        user_id: int,
        session: AsyncSession,
    ) -> bool:
        """Revoke a user's VPN configuration."""
        result = await session.execute(
            select(VPNConfig)
            .where(VPNConfig.user_id == user_id)
            .where(VPNConfig.is_active == True)
            .where(VPNConfig.is_revoked == False)
        )
        vpn_config = result.scalar_one_or_none()

        if not vpn_config:
            return False

        # Delete from wg-easy
        client_id = vpn_config.public_key
        await self.delete_client(client_id)

        # Mark as revoked in database
        vpn_config.is_revoked = True
        vpn_config.is_active = False
        await session.commit()

        logger.info(f"Revoked VPN config for user {user_id}")
        return True

    async def cleanup_expired(self, session: AsyncSession) -> int:
        """Clean up expired VPN configurations."""
        result = await session.execute(
            select(VPNConfig)
            .where(VPNConfig.is_active == True)
            .where(VPNConfig.is_revoked == False)
            .where(VPNConfig.expires_at < datetime.utcnow())
        )
        expired = result.scalars().all()

        count = 0
        for vpn_config in expired:
            try:
                await self.delete_client(vpn_config.public_key)
                vpn_config.is_active = False
                vpn_config.is_revoked = True
                count += 1
            except Exception as e:
                logger.error(f"Failed to cleanup VPN {vpn_config.id}: {e}")

        await session.commit()
        logger.info(f"Cleaned up {count} expired VPN configs")
        return count

    async def get_server_status(self) -> Optional[Dict[str, Any]]:
        """Get wg-easy server status."""
        clients = await self.get_clients()

        if clients is None:
            return None

        enabled = sum(1 for c in clients if c.get("enabled", True))
        connected = sum(1 for c in clients if c.get("latestHandshakeAt"))

        return {
            "total_clients": len(clients),
            "enabled_clients": enabled,
            "connected_clients": connected,
            "endpoint": self.server_endpoint,
        }

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
