"""
Infrastructure Provider Interface

Abstract interface for hypervisor/cloud providers (Proxmox, AWS, etc.)
Ensures consistent API across different infrastructure backends.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime


class ProviderType(str, Enum):
    PROXMOX = "proxmox"
    AWS = "aws"
    # Future: AZURE = "azure", GCP = "gcp", HETZNER = "hetzner"


@dataclass
class VMInfo:
    """Standardized VM information across providers."""
    provider: ProviderType
    instance_id: str  # Provider-specific ID (VMID for Proxmox, instance-id for AWS)
    name: str
    status: str  # running, stopped, pending, terminated
    ip_address: Optional[str] = None
    private_ip: Optional[str] = None
    public_ip: Optional[str] = None
    created_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider.value,
            "instance_id": self.instance_id,
            "name": self.name,
            "status": self.status,
            "ip_address": self.ip_address or self.private_ip,
            "private_ip": self.private_ip,
            "public_ip": self.public_ip,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": self.metadata,
        }


@dataclass
class ProviderQuota:
    """Resource quota/limits for a provider."""
    max_instances: int
    current_instances: int
    max_vcpus: int
    current_vcpus: int
    max_memory_gb: int
    current_memory_gb: int

    @property
    def available_instances(self) -> int:
        return self.max_instances - self.current_instances

    @property
    def has_capacity(self) -> bool:
        return self.available_instances > 0


class InfrastructureProvider(ABC):
    """
    Abstract base class for infrastructure providers.

    All providers must implement these methods to ensure
    consistent behavior across Proxmox, AWS, and future providers.
    """

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the provider is healthy and accessible.

        Returns:
            True if provider is accessible, False otherwise.
        """
        pass

    @abstractmethod
    async def get_quota(self) -> ProviderQuota:
        """
        Get current resource quota/usage.

        Returns:
            ProviderQuota with current usage and limits.
        """
        pass

    @abstractmethod
    async def create_instance(
        self,
        template_id: str,
        instance_name: str,
        **kwargs,
    ) -> VMInfo:
        """
        Create a new VM instance from a template.

        Args:
            template_id: Template/AMI ID to clone from
            instance_name: Name for the new instance
            **kwargs: Provider-specific options

        Returns:
            VMInfo with created instance details.

        Raises:
            ProviderError: If instance creation fails.
        """
        pass

    @abstractmethod
    async def start_instance(self, instance_id: str) -> VMInfo:
        """
        Start a stopped instance.

        Args:
            instance_id: Provider-specific instance ID

        Returns:
            VMInfo with updated status.
        """
        pass

    @abstractmethod
    async def stop_instance(self, instance_id: str, force: bool = False) -> VMInfo:
        """
        Stop a running instance.

        Args:
            instance_id: Provider-specific instance ID
            force: Force stop without graceful shutdown

        Returns:
            VMInfo with updated status.
        """
        pass

    @abstractmethod
    async def terminate_instance(self, instance_id: str) -> bool:
        """
        Permanently delete an instance.

        Args:
            instance_id: Provider-specific instance ID

        Returns:
            True if terminated successfully.
        """
        pass

    @abstractmethod
    async def get_instance(self, instance_id: str) -> Optional[VMInfo]:
        """
        Get information about a specific instance.

        Args:
            instance_id: Provider-specific instance ID

        Returns:
            VMInfo if found, None otherwise.
        """
        pass

    @abstractmethod
    async def list_instances(self, filters: Optional[Dict[str, Any]] = None) -> List[VMInfo]:
        """
        List all instances, optionally filtered.

        Args:
            filters: Provider-specific filters

        Returns:
            List of VMInfo objects.
        """
        pass

    @abstractmethod
    async def reset_instance(self, instance_id: str, snapshot_name: str = "clean") -> VMInfo:
        """
        Reset instance to a clean state (snapshot/AMI restore).

        Args:
            instance_id: Provider-specific instance ID
            snapshot_name: Snapshot to restore from

        Returns:
            VMInfo with updated status.
        """
        pass

    @abstractmethod
    async def wait_for_ip(self, instance_id: str, timeout: int = 300) -> Optional[str]:
        """
        Wait for instance to get an IP address.

        Args:
            instance_id: Provider-specific instance ID
            timeout: Maximum wait time in seconds

        Returns:
            IP address if obtained, None on timeout.
        """
        pass

    @abstractmethod
    async def list_templates(self) -> List[Dict[str, Any]]:
        """
        List available templates/AMIs.

        Returns:
            List of template information dicts.
        """
        pass


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, provider: ProviderType, retriable: bool = True):
        self.message = message
        self.provider = provider
        self.retriable = retriable
        super().__init__(f"[{provider.value}] {message}")


class ProviderCapacityError(ProviderError):
    """Raised when provider has no capacity."""

    def __init__(self, provider: ProviderType):
        super().__init__("No capacity available", provider, retriable=True)


class ProviderTimeoutError(ProviderError):
    """Raised when provider operation times out."""

    def __init__(self, provider: ProviderType, operation: str):
        super().__init__(f"Operation '{operation}' timed out", provider, retriable=True)


class ProviderNotFoundError(ProviderError):
    """Raised when resource is not found."""

    def __init__(self, provider: ProviderType, resource_type: str, resource_id: str):
        super().__init__(
            f"{resource_type} '{resource_id}' not found",
            provider,
            retriable=False,
        )
