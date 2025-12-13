import asyncio
import ipaddress
import logging
import subprocess
from pathlib import Path
from typing import Tuple, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import VPNConfig

logger = logging.getLogger("vulnlab.wireguard")


class WireGuardService:
    """Service for managing WireGuard VPN configurations."""

    def __init__(self):
        self.config_path = Path(settings.wg_config_path)
        self.interface = settings.wg_interface
        self.network = ipaddress.ip_network(settings.wg_network)
        self.server_public_key = settings.wg_server_public_key
        self.server_endpoint = settings.wg_server_endpoint
        self.dns = settings.wg_dns

    def generate_keypair(self) -> Tuple[str, str]:
        """Generate a WireGuard keypair."""
        try:
            # Generate private key
            private_key = subprocess.run(
                ["wg", "genkey"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            # Generate public key from private key
            public_key = subprocess.run(
                ["wg", "pubkey"],
                input=private_key,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            return private_key, public_key

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to generate keypair: {e}")
            raise RuntimeError("Failed to generate WireGuard keypair")
        except FileNotFoundError:
            logger.error("WireGuard tools not installed")
            raise RuntimeError("WireGuard tools (wg) not found")

    async def allocate_ip(self, session: AsyncSession) -> str:
        """Allocate an IP address from the VPN pool."""
        # Get all allocated IPs
        result = await session.execute(
            select(VPNConfig.assigned_ip)
            .where(VPNConfig.is_active == True)
            .where(VPNConfig.is_revoked == False)
        )
        allocated_ips = {row[0] for row in result.all()}

        # Find next available IP (skip network and broadcast)
        # Also skip .1 which is typically the server
        for host in self.network.hosts():
            ip_str = str(host)
            if ip_str.endswith(".1"):  # Skip server IP
                continue
            if ip_str not in allocated_ips:
                return ip_str

        raise RuntimeError("No available IP addresses in the VPN pool")

    def generate_client_config(
        self,
        private_key: str,
        address: str,
    ) -> str:
        """Generate a WireGuard client configuration file."""
        config = f"""[Interface]
PrivateKey = {private_key}
Address = {address}/32
DNS = {self.dns}

[Peer]
PublicKey = {self.server_public_key}
AllowedIPs = 10.10.0.0/16
Endpoint = {self.server_endpoint}
PersistentKeepalive = 25
"""
        return config

    async def add_peer(
        self,
        public_key: str,
        allowed_ips: str,
    ) -> None:
        """Add a peer to the WireGuard server."""
        try:
            # Use wg set to add peer dynamically
            cmd = [
                "wg", "set", self.interface,
                "peer", public_key,
                "allowed-ips", allowed_ips,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"Failed to add WireGuard peer: {stderr.decode()}")
                raise RuntimeError(f"Failed to add peer: {stderr.decode()}")

            # Save configuration to persist across restarts
            await self._save_config()

            logger.info(f"Added WireGuard peer: {public_key[:20]}...")

        except FileNotFoundError:
            logger.warning("WireGuard not available, skipping peer addition")

    async def remove_peer(self, public_key: str) -> None:
        """Remove a peer from the WireGuard server."""
        try:
            cmd = [
                "wg", "set", self.interface,
                "peer", public_key,
                "remove",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"Failed to remove WireGuard peer: {stderr.decode()}")
                raise RuntimeError(f"Failed to remove peer: {stderr.decode()}")

            # Save configuration
            await self._save_config()

            logger.info(f"Removed WireGuard peer: {public_key[:20]}...")

        except FileNotFoundError:
            logger.warning("WireGuard not available, skipping peer removal")

    async def _save_config(self) -> None:
        """Save current WireGuard configuration to file."""
        try:
            config_file = self.config_path / f"{self.interface}.conf"

            # Get current config
            process = await asyncio.create_subprocess_exec(
                "wg-quick", "save", self.interface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

        except Exception as e:
            logger.warning(f"Failed to save WireGuard config: {e}")

    async def get_peer_status(self, public_key: str) -> Optional[dict]:
        """Get status of a specific peer."""
        try:
            process = await asyncio.create_subprocess_exec(
                "wg", "show", self.interface, "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return None

            lines = stdout.decode().strip().split("\n")
            for line in lines[1:]:  # Skip header line
                parts = line.split("\t")
                if len(parts) >= 5 and parts[0] == public_key:
                    return {
                        "public_key": parts[0],
                        "preshared_key": parts[1],
                        "endpoint": parts[2],
                        "allowed_ips": parts[3],
                        "latest_handshake": int(parts[4]) if parts[4] != "0" else None,
                        "transfer_rx": int(parts[5]) if len(parts) > 5 else 0,
                        "transfer_tx": int(parts[6]) if len(parts) > 6 else 0,
                    }

            return None

        except Exception as e:
            logger.error(f"Failed to get peer status: {e}")
            return None

    async def get_all_peers(self) -> list:
        """Get all peers on the WireGuard interface."""
        try:
            process = await asyncio.create_subprocess_exec(
                "wg", "show", self.interface, "peers",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                return []

            peers = stdout.decode().strip().split("\n")
            return [p for p in peers if p]

        except Exception as e:
            logger.error(f"Failed to get peers: {e}")
            return []

    def generate_server_config(
        self,
        private_key: str,
        listen_port: int = 51820,
        address: str = "10.10.0.1/16",
    ) -> str:
        """Generate WireGuard server configuration."""
        config = f"""[Interface]
PrivateKey = {private_key}
Address = {address}
ListenPort = {listen_port}
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Peers will be added dynamically
"""
        return config
