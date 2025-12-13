from pydantic_settings import BaseSettings
from typing import Optional, List


class Settings(BaseSettings):
    # Discord
    discord_token: str
    discord_guild_id: int

    # Database
    database_url: str = "postgresql+asyncpg://vulnlab:password@localhost:5432/vulnlab"

    # Redis (for distributed locking and caching)
    redis_url: Optional[str] = None  # e.g., redis://localhost:6379/0

    # =========================================================================
    # INFRASTRUCTURE PROVIDERS
    # =========================================================================

    # Active providers (comma-separated: proxmox,aws)
    active_providers: str = "proxmox"

    # Proxmox
    proxmox_host: Optional[str] = None
    proxmox_user: str = "root@pam"
    proxmox_password: Optional[str] = None
    proxmox_node: str = "pve"
    proxmox_verify_ssl: bool = False

    # AWS
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: str = "eu-west-1"
    aws_vpc_id: Optional[str] = None
    aws_subnet_id: Optional[str] = None
    aws_security_group_id: Optional[str] = None
    aws_default_instance_type: str = "t3.small"
    aws_key_name: Optional[str] = None  # SSH key pair name

    # =========================================================================
    # WIREGUARD VPN
    # =========================================================================
    wg_server_public_key: Optional[str] = None
    wg_server_endpoint: Optional[str] = None
    wg_server_private_key: Optional[str] = None
    wg_interface: str = "wg0"
    wg_config_path: str = "/etc/wireguard"
    wg_network: str = "10.10.0.0/16"
    wg_dns: str = "10.10.0.1"

    # =========================================================================
    # PATREON INTEGRATION
    # =========================================================================
    patreon_client_id: Optional[str] = None
    patreon_client_secret: Optional[str] = None
    patreon_creator_access_token: Optional[str] = None
    patreon_campaign_id: Optional[str] = None

    # =========================================================================
    # MACHINE SETTINGS
    # =========================================================================
    default_machine_duration_hours: int = 2
    max_extend_hours: int = 4
    auto_shutdown_minutes: int = 120
    max_concurrent_instances_per_user: int = 3

    # =========================================================================
    # VPN SETTINGS
    # =========================================================================
    vpn_cert_validity_days: int = 30

    # =========================================================================
    # JOB QUEUE SETTINGS
    # =========================================================================
    job_queue_workers: int = 5
    job_queue_max_size: int = 1000
    job_timeout_seconds: int = 600

    # =========================================================================
    # RESILIENCE SETTINGS
    # =========================================================================
    provider_max_retries: int = 3
    provider_retry_delay: float = 1.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 60
    health_check_interval: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def get_active_provider_list(self) -> List[str]:
        """Get list of active providers."""
        return [p.strip().lower() for p in self.active_providers.split(",") if p.strip()]


settings = Settings()
