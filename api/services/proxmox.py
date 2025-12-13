"""
Proxmox VE Infrastructure Provider

Implements the InfrastructureProvider interface for Proxmox VE.
Handles VM lifecycle management with retry logic and proper error handling.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from proxmoxer import ProxmoxAPI

from config import settings
from .provider_interface import (
    InfrastructureProvider,
    ProviderType,
    VMInfo,
    ProviderQuota,
    ProviderError,
    ProviderTimeoutError,
)

logger = logging.getLogger("vulnlab.proxmox")


class ProxmoxProvider(InfrastructureProvider):
    """
    Proxmox VE infrastructure provider.

    Manages VM lifecycle on Proxmox clusters with support for:
    - Template cloning
    - Snapshot-based resets
    - Guest agent IP detection
    - Resource quota tracking
    """

    def __init__(
        self,
        host: str = None,
        user: str = None,
        password: str = None,
        node: str = None,
        verify_ssl: bool = None,
    ):
        self.host = host or settings.proxmox_host
        self.user = user or settings.proxmox_user
        self.password = password or settings.proxmox_password
        self.default_node = node or settings.proxmox_node
        self.verify_ssl = verify_ssl if verify_ssl is not None else settings.proxmox_verify_ssl

        self._client: Optional[ProxmoxAPI] = None
        self._lock = asyncio.Lock()

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.PROXMOX

    @property
    def client(self) -> ProxmoxAPI:
        """Lazy initialization of Proxmox client with connection pooling."""
        if self._client is None:
            self._client = ProxmoxAPI(
                self.host,
                user=self.user,
                password=self.password,
                verify_ssl=self.verify_ssl,
                timeout=30,
            )
        return self._client

    async def _run_sync(self, func, *args, **kwargs):
        """Run synchronous Proxmox API call in executor with timeout."""
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                timeout=60,
            )
        except asyncio.TimeoutError:
            raise ProviderTimeoutError(self.provider_type, func.__name__)

    async def health_check(self) -> bool:
        """Check Proxmox API accessibility."""
        try:
            await self._run_sync(self.client.version.get)
            return True
        except Exception as e:
            logger.error(f"Proxmox health check failed: {e}")
            return False

    async def get_quota(self) -> ProviderQuota:
        """Get Proxmox node resource usage."""
        try:
            node_status = await self._run_sync(
                self.client.nodes(self.default_node).status.get
            )
            vms = await self._run_sync(
                self.client.nodes(self.default_node).qemu.get
            )

            # Count running VMs (excluding templates)
            running_vms = [vm for vm in vms if not vm.get("template") and vm.get("status") == "running"]

            # Get node resources
            max_memory_gb = node_status.get("memory", {}).get("total", 0) // (1024 ** 3)
            used_memory_gb = node_status.get("memory", {}).get("used", 0) // (1024 ** 3)
            max_cpus = node_status.get("cpuinfo", {}).get("cpus", 0)

            return ProviderQuota(
                max_instances=100,  # Configurable limit
                current_instances=len(running_vms),
                max_vcpus=max_cpus * 2,  # Overcommit factor
                current_vcpus=sum(vm.get("cpus", 1) for vm in running_vms),
                max_memory_gb=max_memory_gb,
                current_memory_gb=used_memory_gb,
            )
        except Exception as e:
            logger.error(f"Failed to get Proxmox quota: {e}")
            raise ProviderError(str(e), self.provider_type)

    async def _get_next_vmid(self, node: str) -> int:
        """Get next available VMID atomically."""
        async with self._lock:
            vms = await self._run_sync(self.client.nodes(node).qemu.get)
            existing_vmids = {vm["vmid"] for vm in vms}

            # Start from 1000, find first available
            vmid = 1000
            while vmid in existing_vmids:
                vmid += 1

            return vmid

    async def create_instance(
        self,
        template_id: str,
        instance_name: str,
        node: str = None,
        full_clone: bool = True,
        **kwargs,
    ) -> VMInfo:
        """Create a new VM by cloning a template."""
        node = node or self.default_node
        template_vmid = int(template_id)

        try:
            # Get next available VMID
            new_vmid = await self._get_next_vmid(node)

            logger.info(f"Cloning template {template_vmid} to VMID {new_vmid} on node {node}")

            # Clone the template
            task = await self._run_sync(
                self.client.nodes(node).qemu(template_vmid).clone.post,
                newid=new_vmid,
                name=instance_name,
                full=1 if full_clone else 0,
            )

            # Wait for clone task to complete
            await self._wait_for_task(node, task)

            # Start the VM
            await self._run_sync(
                self.client.nodes(node).qemu(new_vmid).status.start.post
            )

            logger.info(f"VM {new_vmid} created and started")

            return VMInfo(
                provider=self.provider_type,
                instance_id=str(new_vmid),
                name=instance_name,
                status="starting",
                created_at=datetime.utcnow(),
                metadata={"node": node, "template_id": template_vmid},
            )

        except Exception as e:
            logger.error(f"Failed to create instance: {e}")
            raise ProviderError(f"Clone failed: {e}", self.provider_type)

    async def _wait_for_task(self, node: str, task_id: str, timeout: int = 300):
        """Wait for a Proxmox task to complete."""
        start_time = asyncio.get_event_loop().time()

        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise ProviderTimeoutError(self.provider_type, f"task:{task_id}")

            try:
                status = await self._run_sync(
                    self.client.nodes(node).tasks(task_id).status.get
                )

                if status.get("status") == "stopped":
                    if status.get("exitstatus") != "OK":
                        raise ProviderError(
                            f"Task failed: {status.get('exitstatus')}",
                            self.provider_type,
                        )
                    return

            except ProviderError:
                raise
            except Exception:
                pass

            await asyncio.sleep(2)

    async def start_instance(self, instance_id: str, node: str = None) -> VMInfo:
        """Start a stopped VM."""
        node = node or self.default_node
        vmid = int(instance_id)

        try:
            await self._run_sync(
                self.client.nodes(node).qemu(vmid).status.start.post
            )

            logger.info(f"Started VM {vmid}")
            return await self.get_instance(instance_id)

        except Exception as e:
            logger.error(f"Failed to start VM {vmid}: {e}")
            raise ProviderError(f"Start failed: {e}", self.provider_type)

    async def stop_instance(self, instance_id: str, force: bool = False, node: str = None) -> VMInfo:
        """Stop a running VM."""
        node = node or self.default_node
        vmid = int(instance_id)

        try:
            if force:
                await self._run_sync(
                    self.client.nodes(node).qemu(vmid).status.stop.post
                )
            else:
                await self._run_sync(
                    self.client.nodes(node).qemu(vmid).status.shutdown.post
                )

            logger.info(f"Stopped VM {vmid}")
            return await self.get_instance(instance_id)

        except Exception as e:
            logger.error(f"Failed to stop VM {vmid}: {e}")
            raise ProviderError(f"Stop failed: {e}", self.provider_type)

    async def terminate_instance(self, instance_id: str, node: str = None) -> bool:
        """Delete a VM permanently."""
        node = node or self.default_node
        vmid = int(instance_id)

        try:
            # Stop first if running
            vm = await self.get_instance(instance_id)
            if vm and vm.status == "running":
                await self.stop_instance(instance_id, force=True, node=node)
                await asyncio.sleep(5)

            # Delete the VM
            await self._run_sync(
                self.client.nodes(node).qemu(vmid).delete
            )

            logger.info(f"Terminated VM {vmid}")
            return True

        except Exception as e:
            logger.error(f"Failed to terminate VM {vmid}: {e}")
            raise ProviderError(f"Terminate failed: {e}", self.provider_type)

    async def get_instance(self, instance_id: str, node: str = None) -> Optional[VMInfo]:
        """Get VM status and info."""
        node = node or self.default_node
        vmid = int(instance_id)

        try:
            status = await self._run_sync(
                self.client.nodes(node).qemu(vmid).status.current.get
            )

            config = await self._run_sync(
                self.client.nodes(node).qemu(vmid).config.get
            )

            # Try to get IP from guest agent
            ip_address = None
            if status.get("status") == "running":
                ip_address = await self._get_guest_ip(node, vmid)

            return VMInfo(
                provider=self.provider_type,
                instance_id=str(vmid),
                name=config.get("name", f"vm-{vmid}"),
                status=status.get("status", "unknown"),
                ip_address=ip_address,
                private_ip=ip_address,
                metadata={
                    "node": node,
                    "cpus": config.get("cores", 1),
                    "memory_mb": config.get("memory", 512),
                    "uptime": status.get("uptime", 0),
                },
            )

        except Exception as e:
            logger.error(f"Failed to get VM {vmid}: {e}")
            return None

    async def _get_guest_ip(self, node: str, vmid: int) -> Optional[str]:
        """Get IP address from QEMU guest agent."""
        try:
            result = await self._run_sync(
                self.client.nodes(node).qemu(vmid).agent("network-get-interfaces").get
            )

            for iface in result.get("result", []):
                if iface.get("name") == "lo":
                    continue
                for ip_info in iface.get("ip-addresses", []):
                    if ip_info.get("ip-address-type") == "ipv4":
                        ip = ip_info.get("ip-address")
                        if ip and not ip.startswith("127."):
                            return ip
        except Exception:
            pass

        return None

    async def list_instances(self, filters: Optional[Dict[str, Any]] = None) -> List[VMInfo]:
        """List all VMs on the node."""
        node = filters.get("node", self.default_node) if filters else self.default_node

        try:
            vms = await self._run_sync(
                self.client.nodes(node).qemu.get
            )

            result = []
            for vm in vms:
                if vm.get("template"):
                    continue

                result.append(VMInfo(
                    provider=self.provider_type,
                    instance_id=str(vm["vmid"]),
                    name=vm.get("name", f"vm-{vm['vmid']}"),
                    status=vm.get("status", "unknown"),
                    metadata={
                        "node": node,
                        "cpus": vm.get("cpus", 1),
                        "memory_mb": vm.get("maxmem", 0) // (1024 * 1024),
                    },
                ))

            return result

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            raise ProviderError(f"List failed: {e}", self.provider_type)

    async def reset_instance(self, instance_id: str, snapshot_name: str = "clean", node: str = None) -> VMInfo:
        """Reset VM to a snapshot."""
        node = node or self.default_node
        vmid = int(instance_id)

        try:
            # Stop the VM
            await self.stop_instance(instance_id, force=True, node=node)
            await asyncio.sleep(5)

            # Rollback to snapshot
            await self._run_sync(
                self.client.nodes(node).qemu(vmid).snapshot(snapshot_name).rollback.post
            )

            await asyncio.sleep(3)

            # Start the VM
            await self.start_instance(instance_id, node=node)

            logger.info(f"Reset VM {vmid} to snapshot {snapshot_name}")
            return await self.get_instance(instance_id)

        except Exception as e:
            logger.error(f"Failed to reset VM {vmid}: {e}")
            raise ProviderError(f"Reset failed: {e}", self.provider_type)

    async def wait_for_ip(self, instance_id: str, timeout: int = 300, node: str = None) -> Optional[str]:
        """Wait for VM to get an IP address."""
        node = node or self.default_node
        vmid = int(instance_id)
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < timeout:
            ip = await self._get_guest_ip(node, vmid)
            if ip:
                return ip
            await asyncio.sleep(5)

        logger.warning(f"Timeout waiting for IP on VM {vmid}")
        return None

    async def list_templates(self, node: str = None) -> List[Dict[str, Any]]:
        """List available VM templates."""
        node = node or self.default_node

        try:
            vms = await self._run_sync(
                self.client.nodes(node).qemu.get
            )

            templates = []
            for vm in vms:
                if vm.get("template"):
                    templates.append({
                        "template_id": str(vm["vmid"]),
                        "name": vm.get("name", f"template-{vm['vmid']}"),
                        "node": node,
                    })

            return templates

        except Exception as e:
            logger.error(f"Failed to list templates: {e}")
            raise ProviderError(f"List templates failed: {e}", self.provider_type)

    # Legacy compatibility methods
    async def start_machine(self, template_id: int, instance_id: int, node: str) -> Dict[str, Any]:
        """Legacy method for backward compatibility."""
        vm_info = await self.create_instance(
            template_id=str(template_id),
            instance_name=f"vulnlab-instance-{instance_id}",
            node=node,
        )
        ip = await self.wait_for_ip(vm_info.instance_id, timeout=120, node=node)
        return {"vmid": int(vm_info.instance_id), "ip": ip}

    async def stop_machine(self, vmid: int, node: str) -> None:
        """Legacy method for backward compatibility."""
        await self.terminate_instance(str(vmid), node=node)

    async def reset_machine(self, vmid: int, node: str) -> None:
        """Legacy method for backward compatibility."""
        await self.reset_instance(str(vmid), node=node)


# Backward compatibility alias
ProxmoxService = ProxmoxProvider
