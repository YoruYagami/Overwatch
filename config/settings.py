from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Discord
    discord_token: str
    discord_guild_id: int

    # Database
    database_url: str = "postgresql+asyncpg://vulnlab:password@localhost:5432/vulnlab"

    # Proxmox
    proxmox_host: str
    proxmox_user: str = "root@pam"
    proxmox_password: str
    proxmox_node: str = "pve"
    proxmox_verify_ssl: bool = False

    # WireGuard
    wg_server_public_key: str
    wg_server_endpoint: str
    wg_server_private_key: str
    wg_interface: str = "wg0"
    wg_config_path: str = "/etc/wireguard"
    wg_network: str = "10.10.0.0/16"
    wg_dns: str = "10.10.0.1"

    # Patreon
    patreon_client_id: Optional[str] = None
    patreon_client_secret: Optional[str] = None
    patreon_creator_access_token: Optional[str] = None
    patreon_campaign_id: Optional[str] = None

    # Machine Settings
    default_machine_duration_hours: int = 2
    max_extend_hours: int = 4
    auto_shutdown_minutes: int = 120

    # VPN
    vpn_cert_validity_days: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
