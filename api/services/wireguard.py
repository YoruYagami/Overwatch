"""
WireGuard Service via Proxmox

Manages WireGuard peers on a VM running on Proxmox.
Commands are executed via QEMU Guest Agent, so the bot
doesn't need direct access to the WireGuard server.
"""

import asyncio
import base64
import ipaddress
import logging
import secrets
from typing import Tuple, Optional, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from config import settings
from db import VPNConfig

logger = logging.getLogger("vulnlab.wireguard")


class WireGuardService:
    """
    WireGuard management service via Proxmox.

    Executes WireGuard commands on a VM through Proxmox QEMU Guest Agent.
    No local WireGuard installation required on the bot server.
    """

    def __init__(self, proxmox_client=None):
        self.interface = settings.wg_interface
        self.network = ipaddress.ip_network(settings.wg_network)
        self.server_public_key = settings.wg_server_public_key
        self.server_endpoint = settings.wg_server_endpoint
        self.dns = settings.wg_dns

        # Proxmox settings for WireGuard VM
        self.wg_vm_id = getattr(settings, 'wg_proxmox_vmid', None)
        self.proxmox_node = settings.proxmox_node

        self._proxmox = proxmox_client
        self._lock = asyncio.Lock()

    def _get_proxmox(self):
        """Get or create Proxmox client."""
        if self._proxmox is None:
            from proxmoxer import ProxmoxAPI
            self._proxmox = ProxmoxAPI(
                settings.proxmox_host,
                user=settings.proxmox_user,
                password=settings.proxmox_password,
                verify_ssl=settings.proxmox_verify_ssl,
                timeout=30,
            )
        return self._proxmox

    async def _run_on_wg_server(self, command: str) -> Tuple[int, str, str]:
        """
        Execute a command on the WireGuard VM via QEMU Guest Agent.

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self.wg_vm_id:
            logger.warning("WG_PROXMOX_VMID not configured, skipping command")
            return (0, "", "")

        try:
            proxmox = self._get_proxmox()

            # Execute command via guest agent
            loop = asyncio.get_event_loop()

            def _exec():
                # Start the command
                result = proxmox.nodes(self.proxmox_node).qemu(self.wg_vm_id).agent.exec.post(
                    command=command,
                )
                return result

            result = await loop.run_in_executor(None, _exec)
            pid = result.get('pid')

            # Wait for command to complete and get output
            await asyncio.sleep(1)

            def _get_status():
                return proxmox.nodes(self.proxmox_node).qemu(self.wg_vm_id).agent('exec-status').get(
                    pid=pid,
                )

            # Poll for completion
            for _ in range(30):
                status = await loop.run_in_executor(None, _get_status)
                if status.get('exited'):
                    stdout = base64.b64decode(status.get('out-data', '')).decode() if status.get('out-data') else ''
                    stderr = base64.b64decode(status.get('err-data', '')).decode() if status.get('err-data') else ''
                    exitcode = status.get('exitcode', 0)
                    return (exitcode, stdout, stderr)
                await asyncio.sleep(0.5)

            return (1, "", "Command timed out")

        except Exception as e:
            logger.error(f"Failed to execute command on WG server: {e}")
            return (1, "", str(e))

    def generate_keypair(self) -> Tuple[str, str]:
        """
        Generate a WireGuard keypair using Python cryptography.
        No local wg tools required.
        """
        # Generate X25519 private key
        private_key = X25519PrivateKey.generate()

        # Get raw private key bytes
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )

        # Get public key bytes
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

        # Base64 encode for WireGuard format
        private_key_b64 = base64.b64encode(private_bytes).decode('ascii')
        public_key_b64 = base64.b64encode(public_bytes).decode('ascii')

        return private_key_b64, public_key_b64

    async def allocate_ip(self, session: AsyncSession) -> str:
        """Allocate an IP address from the VPN pool."""
        result = await session.execute(
            select(VPNConfig.assigned_ip)
            .where(VPNConfig.is_active == True)
            .where(VPNConfig.is_revoked == False)
        )
        allocated_ips = {row[0] for row in result.all()}

        # Find next available IP (skip .0, .1, .255)
        for host in self.network.hosts():
            ip_str = str(host)
            # Skip server IP (.1) and common reserved
            if ip_str.endswith(".0") or ip_str.endswith(".1") or ip_str.endswith(".255"):
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
        """Add a peer to the WireGuard server via Proxmox."""
        async with self._lock:
            command = f"wg set {self.interface} peer {public_key} allowed-ips {allowed_ips}"

            exitcode, stdout, stderr = await self._run_on_wg_server(command)

            if exitcode != 0:
                logger.error(f"Failed to add WireGuard peer: {stderr}")
                raise RuntimeError(f"Failed to add peer: {stderr}")

            # Save configuration
            await self._save_config()

            logger.info(f"Added WireGuard peer: {public_key[:20]}...")

    async def remove_peer(self, public_key: str) -> None:
        """Remove a peer from the WireGuard server via Proxmox."""
        async with self._lock:
            command = f"wg set {self.interface} peer {public_key} remove"

            exitcode, stdout, stderr = await self._run_on_wg_server(command)

            if exitcode != 0:
                logger.error(f"Failed to remove WireGuard peer: {stderr}")
                raise RuntimeError(f"Failed to remove peer: {stderr}")

            # Save configuration
            await self._save_config()

            logger.info(f"Removed WireGuard peer: {public_key[:20]}...")

    async def _save_config(self) -> None:
        """Save current WireGuard configuration on the server."""
        command = f"wg-quick save {self.interface}"
        exitcode, stdout, stderr = await self._run_on_wg_server(command)

        if exitcode != 0:
            logger.warning(f"Failed to save WireGuard config: {stderr}")

    async def get_peer_status(self, public_key: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific peer."""
        command = f"wg show {self.interface} dump"
        exitcode, stdout, stderr = await self._run_on_wg_server(command)

        if exitcode != 0:
            return None

        lines = stdout.strip().split("\n")
        for line in lines[1:]:  # Skip header
            parts = line.split("\t")
            if len(parts) >= 5 and parts[0] == public_key:
                return {
                    "public_key": parts[0],
                    "preshared_key": parts[1] if parts[1] != "(none)" else None,
                    "endpoint": parts[2] if parts[2] != "(none)" else None,
                    "allowed_ips": parts[3],
                    "latest_handshake": int(parts[4]) if parts[4] != "0" else None,
                    "transfer_rx": int(parts[5]) if len(parts) > 5 else 0,
                    "transfer_tx": int(parts[6]) if len(parts) > 6 else 0,
                }

        return None

    async def get_all_peers(self) -> list:
        """Get all peers on the WireGuard interface."""
        command = f"wg show {self.interface} peers"
        exitcode, stdout, stderr = await self._run_on_wg_server(command)

        if exitcode != 0:
            return []

        peers = stdout.strip().split("\n")
        return [p for p in peers if p]

    async def get_server_status(self) -> Optional[Dict[str, Any]]:
        """Get WireGuard server status."""
        command = f"wg show {self.interface}"
        exitcode, stdout, stderr = await self._run_on_wg_server(command)

        if exitcode != 0:
            return None

        return {
            "interface": self.interface,
            "output": stdout,
            "peer_count": len(await self.get_all_peers()),
        }

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
