import asyncio
import logging
from typing import Optional, Dict, Any

from proxmoxer import ProxmoxAPI

from config import settings

logger = logging.getLogger("vulnlab.proxmox")


class ProxmoxService:
    """Service for interacting with Proxmox VE API."""

    def __init__(self):
        self._client: Optional[ProxmoxAPI] = None
        self._vmid_counter = 1000  # Starting VMID for clones

    @property
    def client(self) -> ProxmoxAPI:
        """Lazy initialization of Proxmox client."""
        if self._client is None:
            self._client = ProxmoxAPI(
                settings.proxmox_host,
                user=settings.proxmox_user,
                password=settings.proxmox_password,
                verify_ssl=settings.proxmox_verify_ssl,
            )
        return self._client

    async def _run_sync(self, func, *args, **kwargs):
        """Run synchronous Proxmox API call in executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def get_next_vmid(self, node: str) -> int:
        """Get next available VMID."""
        try:
            # Get list of existing VMs
            vms = await self._run_sync(self.client.nodes(node).qemu.get)
            existing_vmids = {vm["vmid"] for vm in vms}

            # Find next available VMID starting from 1000
            vmid = 1000
            while vmid in existing_vmids:
                vmid += 1

            return vmid
        except Exception as e:
            logger.error(f"Failed to get next VMID: {e}")
            raise

    async def clone_vm(
        self,
        template_vmid: int,
        new_vmid: int,
        node: str,
        name: str,
        full_clone: bool = True,
    ) -> Dict[str, Any]:
        """Clone a VM template."""
        try:
            clone_params = {
                "newid": new_vmid,
                "name": name,
                "full": 1 if full_clone else 0,
            }

            result = await self._run_sync(
                self.client.nodes(node).qemu(template_vmid).clone.post,
                **clone_params,
            )

            logger.info(f"Cloned VM {template_vmid} to {new_vmid} on node {node}")
            return {"vmid": new_vmid, "task": result}

        except Exception as e:
            logger.error(f"Failed to clone VM: {e}")
            raise

    async def start_vm(self, vmid: int, node: str) -> str:
        """Start a VM."""
        try:
            result = await self._run_sync(
                self.client.nodes(node).qemu(vmid).status.start.post
            )
            logger.info(f"Started VM {vmid} on node {node}")
            return result
        except Exception as e:
            logger.error(f"Failed to start VM {vmid}: {e}")
            raise

    async def stop_vm(self, vmid: int, node: str, force: bool = False) -> str:
        """Stop a VM."""
        try:
            if force:
                result = await self._run_sync(
                    self.client.nodes(node).qemu(vmid).status.stop.post
                )
            else:
                result = await self._run_sync(
                    self.client.nodes(node).qemu(vmid).status.shutdown.post
                )
            logger.info(f"Stopped VM {vmid} on node {node}")
            return result
        except Exception as e:
            logger.error(f"Failed to stop VM {vmid}: {e}")
            raise

    async def delete_vm(self, vmid: int, node: str) -> str:
        """Delete a VM."""
        try:
            # First stop if running
            status = await self.get_vm_status(vmid, node)
            if status.get("status") == "running":
                await self.stop_vm(vmid, node, force=True)
                await asyncio.sleep(5)  # Wait for VM to stop

            result = await self._run_sync(
                self.client.nodes(node).qemu(vmid).delete
            )
            logger.info(f"Deleted VM {vmid} on node {node}")
            return result
        except Exception as e:
            logger.error(f"Failed to delete VM {vmid}: {e}")
            raise

    async def get_vm_status(self, vmid: int, node: str) -> Dict[str, Any]:
        """Get VM status."""
        try:
            result = await self._run_sync(
                self.client.nodes(node).qemu(vmid).status.current.get
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get VM status {vmid}: {e}")
            raise

    async def get_vm_ip(self, vmid: int, node: str, timeout: int = 120) -> Optional[str]:
        """Get VM IP address using QEMU guest agent."""
        try:
            # Wait for guest agent to be available
            for _ in range(timeout // 5):
                try:
                    result = await self._run_sync(
                        self.client.nodes(node).qemu(vmid).agent("network-get-interfaces").get
                    )

                    # Parse interfaces for IP
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

                await asyncio.sleep(5)

            logger.warning(f"Could not get IP for VM {vmid} within timeout")
            return None

        except Exception as e:
            logger.error(f"Failed to get VM IP {vmid}: {e}")
            return None

    async def rollback_snapshot(self, vmid: int, node: str, snapshot_name: str = "clean") -> str:
        """Rollback VM to a snapshot."""
        try:
            result = await self._run_sync(
                self.client.nodes(node).qemu(vmid).snapshot(snapshot_name).rollback.post
            )
            logger.info(f"Rolled back VM {vmid} to snapshot {snapshot_name}")
            return result
        except Exception as e:
            logger.error(f"Failed to rollback VM {vmid}: {e}")
            raise

    async def start_machine(
        self,
        template_id: int,
        instance_id: int,
        node: str,
    ) -> Dict[str, Any]:
        """
        Start a machine instance by cloning template and starting.

        Args:
            template_id: Proxmox VMID of the template
            instance_id: Internal instance ID for naming
            node: Proxmox node name

        Returns:
            Dict with vmid and ip
        """
        try:
            # Get next available VMID
            new_vmid = await self.get_next_vmid(node)

            # Clone the template
            await self.clone_vm(
                template_vmid=template_id,
                new_vmid=new_vmid,
                node=node,
                name=f"vulnlab-instance-{instance_id}",
                full_clone=True,
            )

            # Wait for clone to complete
            await asyncio.sleep(10)

            # Start the VM
            await self.start_vm(new_vmid, node)

            # Wait for VM to boot and get IP
            await asyncio.sleep(30)
            ip = await self.get_vm_ip(new_vmid, node)

            if not ip:
                # Fallback: assign IP based on VMID
                ip = f"10.10.{(new_vmid // 256) % 256}.{new_vmid % 256}"
                logger.warning(f"Could not get IP from guest agent, using fallback: {ip}")

            return {
                "vmid": new_vmid,
                "ip": ip,
            }

        except Exception as e:
            logger.error(f"Failed to start machine: {e}")
            raise

    async def stop_machine(self, vmid: int, node: str) -> None:
        """Stop and optionally delete a machine instance."""
        try:
            await self.stop_vm(vmid, node, force=True)
            # Optionally delete the clone
            await asyncio.sleep(5)
            await self.delete_vm(vmid, node)
        except Exception as e:
            logger.error(f"Failed to stop machine: {e}")
            raise

    async def reset_machine(self, vmid: int, node: str) -> None:
        """Reset a machine to its initial state."""
        try:
            # Stop the VM
            await self.stop_vm(vmid, node, force=True)
            await asyncio.sleep(5)

            # Rollback to clean snapshot
            await self.rollback_snapshot(vmid, node, "clean")
            await asyncio.sleep(5)

            # Start the VM again
            await self.start_vm(vmid, node)

        except Exception as e:
            logger.error(f"Failed to reset machine: {e}")
            raise

    async def list_templates(self, node: str) -> list:
        """List all VM templates on a node."""
        try:
            vms = await self._run_sync(self.client.nodes(node).qemu.get)
            templates = [vm for vm in vms if vm.get("template") == 1]
            return templates
        except Exception as e:
            logger.error(f"Failed to list templates: {e}")
            raise

    async def get_node_status(self, node: str) -> Dict[str, Any]:
        """Get node status including resources."""
        try:
            result = await self._run_sync(
                self.client.nodes(node).status.get
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get node status: {e}")
            raise
