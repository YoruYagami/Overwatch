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
    # WIREGUARD VPN (via wg-easy container)
    # =========================================================================
    wg_easy_api_url: str = "http://localhost:51821"  # wg-easy API endpoint
    wg_easy_password: Optional[str] = None  # wg-easy admin password
    wg_server_endpoint: Optional[str] = None  # Public endpoint (e.g., vpn.domain.com:51820)
    wg_network: str = "10.10.0.0/16"

    # =========================================================================
    # PATREON INTEGRATION
    # =========================================================================
    patreon_client_id: Optional[str] = None
    patreon_client_secret: Optional[str] = None
    patreon_creator_access_token: Optional[str] = None
    patreon_campaign_id: Optional[str] = None
    patreon_sync_interval_minutes: int = 60  # How often to sync with Patreon

    # Patreon tier IDs (from your campaign)
    patreon_tier1_id: Optional[str] = None  # e.g., $5/month
    patreon_tier2_id: Optional[str] = None  # e.g., $15/month
    patreon_tier3_id: Optional[str] = None  # e.g., $30/month

    # Discord role IDs for each tier
    discord_role_tier1: Optional[int] = None
    discord_role_tier2: Optional[int] = None
    discord_role_tier3: Optional[int] = None

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
