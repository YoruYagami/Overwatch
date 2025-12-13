"""
Infrastructure Provider Manager

Orchestrates multiple infrastructure providers with:
- Automatic failover between providers
- Circuit breaker pattern for resilience
- Distributed locking for concurrency control
- Retry logic with exponential backoff
- Health monitoring
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List, Callable
from functools import wraps

from .provider_interface import (
    InfrastructureProvider,
    ProviderType,
    VMInfo,
    ProviderQuota,
    ProviderError,
    ProviderCapacityError,
)
from .proxmox import ProxmoxProvider
from .aws import AWSProvider

logger = logging.getLogger("vulnlab.provider_manager")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """Circuit breaker for provider resilience."""
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    half_open_max_calls: int = 3

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    half_open_calls: int = 0

    def record_success(self):
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_calls = 0
                logger.info("Circuit breaker closed after successful recovery")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self):
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker reopened after failure in half-open state")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")

    def can_execute(self) -> bool:
        """Check if call can be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info("Circuit breaker entering half-open state")
                    return True
            return False

        return True  # HALF_OPEN


@dataclass
class ProviderHealth:
    """Provider health status."""
    provider_type: ProviderType
    is_healthy: bool = True
    last_check: Optional[datetime] = None
    consecutive_failures: int = 0
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    quota: Optional[ProviderQuota] = None


class ProviderManager:
    """
    Manages multiple infrastructure providers with resilience patterns.

    Features:
    - Multi-provider support (Proxmox, AWS)
    - Automatic provider selection based on availability
    - Circuit breaker for failing providers
    - Distributed locking via Redis (optional)
    - Health monitoring
    - Retry with exponential backoff
    """

    def __init__(
        self,
        redis_client=None,
        max_retries: int = 3,
        base_retry_delay: float = 1.0,
        health_check_interval: int = 30,
    ):
        self.providers: Dict[ProviderType, InfrastructureProvider] = {}
        self.health: Dict[ProviderType, ProviderHealth] = {}
        self.redis = redis_client
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.health_check_interval = health_check_interval

        self._lock = asyncio.Lock()
        self._health_task: Optional[asyncio.Task] = None
        self._instance_locks: Dict[str, asyncio.Lock] = {}

    async def initialize(self, providers: List[ProviderType] = None):
        """Initialize providers."""
        if providers is None:
            providers = [ProviderType.PROXMOX]

        for provider_type in providers:
            await self.add_provider(provider_type)

        # Start health monitoring
        self._health_task = asyncio.create_task(self._health_monitor())

    async def add_provider(self, provider_type: ProviderType):
        """Add a provider to the manager."""
        try:
            if provider_type == ProviderType.PROXMOX:
                provider = ProxmoxProvider()
            elif provider_type == ProviderType.AWS:
                provider = AWSProvider()
            else:
                raise ValueError(f"Unknown provider type: {provider_type}")

            # Test connectivity
            is_healthy = await provider.health_check()

            self.providers[provider_type] = provider
            self.health[provider_type] = ProviderHealth(
                provider_type=provider_type,
                is_healthy=is_healthy,
                last_check=datetime.utcnow(),
            )

            if is_healthy:
                # Get initial quota
                try:
                    self.health[provider_type].quota = await provider.get_quota()
                except Exception as e:
                    logger.warning(f"Could not get quota for {provider_type}: {e}")

            logger.info(f"Added provider {provider_type.value}, healthy: {is_healthy}")

        except Exception as e:
            logger.error(f"Failed to add provider {provider_type}: {e}")
            raise

    async def shutdown(self):
        """Shutdown the provider manager."""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

    async def _health_monitor(self):
        """Background task to monitor provider health."""
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                for provider_type, provider in self.providers.items():
                    try:
                        is_healthy = await provider.health_check()
                        health = self.health[provider_type]
                        health.is_healthy = is_healthy
                        health.last_check = datetime.utcnow()

                        if is_healthy:
                            health.consecutive_failures = 0
                            health.circuit_breaker.record_success()
                            # Update quota
                            try:
                                health.quota = await provider.get_quota()
                            except Exception:
                                pass
                        else:
                            health.consecutive_failures += 1
                            health.circuit_breaker.record_failure()

                    except Exception as e:
                        logger.error(f"Health check failed for {provider_type}: {e}")
                        self.health[provider_type].is_healthy = False
                        self.health[provider_type].consecutive_failures += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

    def _get_available_provider(self, preferred: ProviderType = None) -> Optional[InfrastructureProvider]:
        """Get an available provider, respecting circuit breaker."""
        # Try preferred provider first
        if preferred and preferred in self.providers:
            health = self.health.get(preferred)
            if health and health.is_healthy and health.circuit_breaker.can_execute():
                if health.quota is None or health.quota.has_capacity:
                    return self.providers[preferred]

        # Try other providers
        for provider_type, provider in self.providers.items():
            if provider_type == preferred:
                continue

            health = self.health.get(provider_type)
            if health and health.is_healthy and health.circuit_breaker.can_execute():
                if health.quota is None or health.quota.has_capacity:
                    return provider

        return None

    async def _acquire_lock(self, key: str, timeout: int = 30) -> bool:
        """Acquire a distributed lock."""
        if self.redis:
            # Use Redis for distributed locking
            lock_key = f"vulnlab:lock:{key}"
            acquired = await self.redis.set(
                lock_key,
                "locked",
                nx=True,
                ex=timeout,
            )
            return bool(acquired)
        else:
            # Fallback to local locks
            if key not in self._instance_locks:
                self._instance_locks[key] = asyncio.Lock()

            try:
                await asyncio.wait_for(
                    self._instance_locks[key].acquire(),
                    timeout=timeout,
                )
                return True
            except asyncio.TimeoutError:
                return False

    async def _release_lock(self, key: str):
        """Release a distributed lock."""
        if self.redis:
            lock_key = f"vulnlab:lock:{key}"
            await self.redis.delete(lock_key)
        else:
            if key in self._instance_locks:
                try:
                    self._instance_locks[key].release()
                except RuntimeError:
                    pass  # Not locked

    async def _with_retry(
        self,
        operation: Callable,
        *args,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> Any:
        """Execute operation with retry logic and circuit breaker."""
        last_error = None

        for attempt in range(self.max_retries):
            provider = self._get_available_provider(provider_type)

            if not provider:
                raise ProviderCapacityError(provider_type or ProviderType.PROXMOX)

            health = self.health[provider.provider_type]

            try:
                result = await operation(provider, *args, **kwargs)
                health.circuit_breaker.record_success()
                return result

            except ProviderError as e:
                last_error = e
                health.circuit_breaker.record_failure()

                if not e.retriable:
                    raise

                # Exponential backoff
                delay = self.base_retry_delay * (2 ** attempt)
                logger.warning(
                    f"Provider operation failed (attempt {attempt + 1}/{self.max_retries}), "
                    f"retrying in {delay}s: {e}"
                )
                await asyncio.sleep(delay)

            except Exception as e:
                last_error = e
                health.circuit_breaker.record_failure()
                logger.error(f"Unexpected error in provider operation: {e}")
                raise ProviderError(str(e), provider.provider_type)

        raise last_error or ProviderError("Max retries exceeded", ProviderType.PROXMOX)

    # Public API methods

    async def create_instance(
        self,
        template_id: str,
        instance_name: str,
        user_id: int,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> VMInfo:
        """
        Create a new instance with concurrency control.

        Uses distributed locking to prevent race conditions when
        multiple users start instances simultaneously.
        """
        lock_key = f"create:{user_id}:{template_id}"

        if not await self._acquire_lock(lock_key):
            raise ProviderError("Another instance creation is in progress", ProviderType.PROXMOX)

        try:
            async def _create(provider: InfrastructureProvider, template_id: str, instance_name: str, **kwargs):
                return await provider.create_instance(template_id, instance_name, **kwargs)

            return await self._with_retry(
                _create,
                template_id,
                instance_name,
                provider_type=provider_type,
                **kwargs,
            )

        finally:
            await self._release_lock(lock_key)

    async def start_instance(
        self,
        instance_id: str,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> VMInfo:
        """Start an instance with retry logic."""
        lock_key = f"start:{instance_id}"

        if not await self._acquire_lock(lock_key):
            raise ProviderError("Instance operation in progress", provider_type or ProviderType.PROXMOX)

        try:
            async def _start(provider: InfrastructureProvider, instance_id: str, **kwargs):
                return await provider.start_instance(instance_id, **kwargs)

            return await self._with_retry(
                _start,
                instance_id,
                provider_type=provider_type,
                **kwargs,
            )

        finally:
            await self._release_lock(lock_key)

    async def stop_instance(
        self,
        instance_id: str,
        force: bool = False,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> VMInfo:
        """Stop an instance with retry logic."""
        lock_key = f"stop:{instance_id}"

        if not await self._acquire_lock(lock_key):
            raise ProviderError("Instance operation in progress", provider_type or ProviderType.PROXMOX)

        try:
            async def _stop(provider: InfrastructureProvider, instance_id: str, force: bool, **kwargs):
                return await provider.stop_instance(instance_id, force, **kwargs)

            return await self._with_retry(
                _stop,
                instance_id,
                force,
                provider_type=provider_type,
                **kwargs,
            )

        finally:
            await self._release_lock(lock_key)

    async def terminate_instance(
        self,
        instance_id: str,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> bool:
        """Terminate an instance with retry logic."""
        lock_key = f"terminate:{instance_id}"

        if not await self._acquire_lock(lock_key):
            raise ProviderError("Instance operation in progress", provider_type or ProviderType.PROXMOX)

        try:
            async def _terminate(provider: InfrastructureProvider, instance_id: str, **kwargs):
                return await provider.terminate_instance(instance_id, **kwargs)

            return await self._with_retry(
                _terminate,
                instance_id,
                provider_type=provider_type,
                **kwargs,
            )

        finally:
            await self._release_lock(lock_key)

    async def reset_instance(
        self,
        instance_id: str,
        snapshot_name: str = "clean",
        provider_type: ProviderType = None,
        **kwargs,
    ) -> VMInfo:
        """Reset an instance with retry logic."""
        lock_key = f"reset:{instance_id}"

        if not await self._acquire_lock(lock_key, timeout=120):
            raise ProviderError("Instance operation in progress", provider_type or ProviderType.PROXMOX)

        try:
            async def _reset(provider: InfrastructureProvider, instance_id: str, snapshot_name: str, **kwargs):
                return await provider.reset_instance(instance_id, snapshot_name, **kwargs)

            return await self._with_retry(
                _reset,
                instance_id,
                snapshot_name,
                provider_type=provider_type,
                **kwargs,
            )

        finally:
            await self._release_lock(lock_key)

    async def get_instance(
        self,
        instance_id: str,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> Optional[VMInfo]:
        """Get instance details."""
        provider = self._get_available_provider(provider_type)
        if not provider:
            return None

        return await provider.get_instance(instance_id, **kwargs)

    async def wait_for_ip(
        self,
        instance_id: str,
        timeout: int = 300,
        provider_type: ProviderType = None,
        **kwargs,
    ) -> Optional[str]:
        """Wait for instance to get an IP."""
        provider = self._get_available_provider(provider_type)
        if not provider:
            return None

        return await provider.wait_for_ip(instance_id, timeout, **kwargs)

    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of all providers."""
        return {
            provider_type.value: {
                "is_healthy": health.is_healthy,
                "last_check": health.last_check.isoformat() if health.last_check else None,
                "consecutive_failures": health.consecutive_failures,
                "circuit_state": health.circuit_breaker.state.value,
                "quota": {
                    "max_instances": health.quota.max_instances,
                    "current_instances": health.quota.current_instances,
                    "available": health.quota.available_instances,
                } if health.quota else None,
            }
            for provider_type, health in self.health.items()
        }


# Global instance
_provider_manager: Optional[ProviderManager] = None


async def get_provider_manager(redis_client=None) -> ProviderManager:
    """Get or create the global provider manager."""
    global _provider_manager

    if _provider_manager is None:
        _provider_manager = ProviderManager(redis_client=redis_client)
        # Initialize with configured providers
        from config import settings
        providers = []

        if hasattr(settings, 'proxmox_host') and settings.proxmox_host:
            providers.append(ProviderType.PROXMOX)

        if hasattr(settings, 'aws_access_key_id') and settings.aws_access_key_id:
            providers.append(ProviderType.AWS)

        if not providers:
            providers = [ProviderType.PROXMOX]  # Default

        await _provider_manager.initialize(providers)

    return _provider_manager
