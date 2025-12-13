"""
Job Handlers for Machine Operations

Implements the actual logic for each job type.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal, MachineInstance, ChainInstance, InstanceStatus
from config import settings
from .job_queue import JobQueue, Job, JobType
from .provider_manager import get_provider_manager, ProviderType

logger = logging.getLogger("vulnlab.job_handlers")


async def handle_machine_start(job: Job) -> dict:
    """Handle machine start job."""
    template_id = job.payload.get("template_id")
    instance_id = job.payload.get("instance_id")
    user_id = job.user_id
    provider_type_str = job.payload.get("provider", "proxmox")
    node = job.payload.get("node", settings.proxmox_node)

    provider_type = ProviderType(provider_type_str)
    manager = await get_provider_manager()

    logger.info(f"Starting machine for user {user_id}, template {template_id}")

    async with AsyncSessionLocal() as session:
        # Get instance
        result = await session.execute(
            select(MachineInstance).where(MachineInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()

        if not instance:
            raise ValueError(f"Instance {instance_id} not found")

        try:
            # Create VM
            vm_info = await manager.create_instance(
                template_id=str(template_id),
                instance_name=f"vulnlab-{user_id}-{instance_id}",
                user_id=user_id,
                provider_type=provider_type,
                node=node,
            )

            # Wait for IP
            ip = await manager.wait_for_ip(
                vm_info.instance_id,
                timeout=180,
                provider_type=provider_type,
                node=node,
            )

            # Update instance
            instance.proxmox_vmid = int(vm_info.instance_id) if vm_info.instance_id.isdigit() else None
            instance.assigned_ip = ip or vm_info.ip_address
            instance.status = InstanceStatus.RUNNING
            instance.started_at = datetime.utcnow()
            instance.expires_at = datetime.utcnow() + timedelta(hours=settings.default_machine_duration_hours)

            await session.commit()

            logger.info(f"Machine started: {vm_info.instance_id}, IP: {ip}")

            return {
                "instance_id": vm_info.instance_id,
                "ip": ip or vm_info.ip_address,
                "status": "running",
            }

        except Exception as e:
            instance.status = InstanceStatus.ERROR
            instance.error_message = str(e)
            await session.commit()
            raise


async def handle_machine_stop(job: Job) -> dict:
    """Handle machine stop job."""
    instance_id = job.payload.get("instance_id")
    vmid = job.payload.get("vmid")
    provider_type_str = job.payload.get("provider", "proxmox")
    node = job.payload.get("node", settings.proxmox_node)
    terminate = job.payload.get("terminate", True)

    provider_type = ProviderType(provider_type_str)
    manager = await get_provider_manager()

    logger.info(f"Stopping machine {vmid}")

    async with AsyncSessionLocal() as session:
        # Get instance
        result = await session.execute(
            select(MachineInstance).where(MachineInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()

        if instance:
            instance.status = InstanceStatus.STOPPING
            await session.commit()

        try:
            if terminate:
                await manager.terminate_instance(
                    str(vmid),
                    provider_type=provider_type,
                    node=node,
                )
            else:
                await manager.stop_instance(
                    str(vmid),
                    provider_type=provider_type,
                    node=node,
                )

            if instance:
                instance.status = InstanceStatus.STOPPED if not terminate else InstanceStatus.TERMINATED
                await session.commit()

            logger.info(f"Machine stopped: {vmid}")

            return {"status": "stopped" if not terminate else "terminated"}

        except Exception as e:
            if instance:
                instance.status = InstanceStatus.ERROR
                instance.error_message = str(e)
                await session.commit()
            raise


async def handle_machine_reset(job: Job) -> dict:
    """Handle machine reset job."""
    instance_id = job.payload.get("instance_id")
    vmid = job.payload.get("vmid")
    provider_type_str = job.payload.get("provider", "proxmox")
    node = job.payload.get("node", settings.proxmox_node)
    snapshot_name = job.payload.get("snapshot", "clean")

    provider_type = ProviderType(provider_type_str)
    manager = await get_provider_manager()

    logger.info(f"Resetting machine {vmid}")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MachineInstance).where(MachineInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()

        try:
            vm_info = await manager.reset_instance(
                str(vmid),
                snapshot_name=snapshot_name,
                provider_type=provider_type,
                node=node,
            )

            # Wait for new IP
            ip = await manager.wait_for_ip(
                str(vmid),
                timeout=120,
                provider_type=provider_type,
                node=node,
            )

            if instance:
                instance.assigned_ip = ip or vm_info.ip_address
                instance.status = InstanceStatus.RUNNING
                await session.commit()

            logger.info(f"Machine reset: {vmid}, new IP: {ip}")

            return {
                "status": "reset",
                "ip": ip or vm_info.ip_address,
            }

        except Exception as e:
            if instance:
                instance.status = InstanceStatus.ERROR
                instance.error_message = str(e)
                await session.commit()
            raise


async def handle_machine_extend(job: Job) -> dict:
    """Handle machine time extension job."""
    instance_id = job.payload.get("instance_id")
    extend_hours = job.payload.get("hours", 1)

    logger.info(f"Extending machine {instance_id} by {extend_hours} hours")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MachineInstance).where(MachineInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()

        if not instance:
            raise ValueError(f"Instance {instance_id} not found")

        if instance.status != InstanceStatus.RUNNING:
            raise ValueError("Can only extend running instances")

        instance.expires_at = instance.expires_at + timedelta(hours=extend_hours)
        instance.extended_count += 1
        await session.commit()

        logger.info(f"Machine extended: {instance_id}, new expiry: {instance.expires_at}")

        return {
            "new_expires_at": instance.expires_at.isoformat(),
            "extended_count": instance.extended_count,
        }


async def handle_chain_start(job: Job) -> dict:
    """Handle chain start job."""
    chain_id = job.payload.get("chain_id")
    chain_instance_id = job.payload.get("chain_instance_id")
    user_id = job.user_id
    provider_type_str = job.payload.get("provider", "proxmox")

    provider_type = ProviderType(provider_type_str)
    manager = await get_provider_manager()

    logger.info(f"Starting chain {chain_id} for user {user_id}")

    async with AsyncSessionLocal() as session:
        from db import Chain, ChainMachineInstance
        from sqlalchemy.orm import selectinload

        # Get chain with machines
        result = await session.execute(
            select(Chain)
            .where(Chain.id == chain_id)
            .options(selectinload(Chain.machines))
        )
        chain = result.scalar_one_or_none()

        if not chain:
            raise ValueError(f"Chain {chain_id} not found")

        # Get chain instance
        result = await session.execute(
            select(ChainInstance)
            .where(ChainInstance.id == chain_instance_id)
            .options(selectinload(ChainInstance.machine_instances))
        )
        chain_instance = result.scalar_one_or_none()

        if not chain_instance:
            raise ValueError(f"Chain instance {chain_instance_id} not found")

        started_machines = []

        try:
            for chain_machine in chain.machines:
                template = chain_machine.machine_template

                # Create machine instance record
                machine_instance = ChainMachineInstance(
                    chain_instance_id=chain_instance.id,
                    machine_template_id=template.id,
                    status=InstanceStatus.STARTING,
                )
                session.add(machine_instance)
                await session.flush()

                # Start via provider
                vm_info = await manager.create_instance(
                    template_id=str(template.proxmox_template_id),
                    instance_name=f"vulnlab-chain-{chain_instance.id}-{machine_instance.id}",
                    user_id=user_id,
                    provider_type=provider_type,
                    node=template.proxmox_node,
                )

                ip = await manager.wait_for_ip(
                    vm_info.instance_id,
                    timeout=180,
                    provider_type=provider_type,
                    node=template.proxmox_node,
                )

                machine_instance.proxmox_vmid = int(vm_info.instance_id) if vm_info.instance_id.isdigit() else None
                machine_instance.assigned_ip = ip or vm_info.ip_address
                machine_instance.status = InstanceStatus.RUNNING

                started_machines.append({
                    "name": template.display_name,
                    "ip": ip or vm_info.ip_address,
                })

            # Update chain instance
            chain_instance.status = InstanceStatus.RUNNING
            chain_instance.started_at = datetime.utcnow()
            chain_instance.expires_at = datetime.utcnow() + timedelta(hours=chain.estimated_time_hours)

            await session.commit()

            logger.info(f"Chain started: {chain_id}, {len(started_machines)} machines")

            return {
                "status": "running",
                "machines": started_machines,
            }

        except Exception as e:
            chain_instance.status = InstanceStatus.ERROR
            chain_instance.error_message = str(e)
            await session.commit()
            raise


async def handle_chain_stop(job: Job) -> dict:
    """Handle chain stop job."""
    chain_instance_id = job.payload.get("chain_instance_id")
    provider_type_str = job.payload.get("provider", "proxmox")

    provider_type = ProviderType(provider_type_str)
    manager = await get_provider_manager()

    logger.info(f"Stopping chain instance {chain_instance_id}")

    async with AsyncSessionLocal() as session:
        from db import ChainMachineInstance
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(ChainInstance)
            .where(ChainInstance.id == chain_instance_id)
            .options(selectinload(ChainInstance.machine_instances))
        )
        chain_instance = result.scalar_one_or_none()

        if not chain_instance:
            raise ValueError(f"Chain instance {chain_instance_id} not found")

        chain_instance.status = InstanceStatus.STOPPING
        await session.commit()

        try:
            for mi in chain_instance.machine_instances:
                if mi.proxmox_vmid:
                    try:
                        await manager.terminate_instance(
                            str(mi.proxmox_vmid),
                            provider_type=provider_type,
                        )
                        mi.status = InstanceStatus.TERMINATED
                    except Exception as e:
                        logger.warning(f"Failed to stop chain machine {mi.id}: {e}")
                        mi.status = InstanceStatus.ERROR

            chain_instance.status = InstanceStatus.STOPPED
            await session.commit()

            logger.info(f"Chain stopped: {chain_instance_id}")

            return {"status": "stopped"}

        except Exception as e:
            chain_instance.status = InstanceStatus.ERROR
            chain_instance.error_message = str(e)
            await session.commit()
            raise


async def handle_cleanup(job: Job) -> dict:
    """Handle cleanup of expired instances."""
    logger.info("Running cleanup job")

    async with AsyncSessionLocal() as session:
        from sqlalchemy import and_

        # Find expired running instances
        result = await session.execute(
            select(MachineInstance)
            .where(and_(
                MachineInstance.status == InstanceStatus.RUNNING,
                MachineInstance.expires_at < datetime.utcnow(),
            ))
        )
        expired_instances = result.scalars().all()

        stopped_count = 0
        manager = await get_provider_manager()

        for instance in expired_instances:
            try:
                if instance.proxmox_vmid:
                    await manager.terminate_instance(str(instance.proxmox_vmid))
                instance.status = InstanceStatus.TERMINATED
                stopped_count += 1
            except Exception as e:
                logger.error(f"Failed to cleanup instance {instance.id}: {e}")
                instance.status = InstanceStatus.ERROR

        await session.commit()

        logger.info(f"Cleanup completed: {stopped_count} instances stopped")

        return {"stopped_count": stopped_count}


async def handle_health_check(job: Job) -> dict:
    """Handle health check job."""
    manager = await get_provider_manager()
    return manager.get_health_status()


def register_all_handlers(queue: JobQueue):
    """Register all job handlers."""
    queue.register_handler(JobType.MACHINE_START, handle_machine_start)
    queue.register_handler(JobType.MACHINE_STOP, handle_machine_stop)
    queue.register_handler(JobType.MACHINE_RESET, handle_machine_reset)
    queue.register_handler(JobType.MACHINE_EXTEND, handle_machine_extend)
    queue.register_handler(JobType.CHAIN_START, handle_chain_start)
    queue.register_handler(JobType.CHAIN_STOP, handle_chain_stop)
    queue.register_handler(JobType.CLEANUP, handle_cleanup)
    queue.register_handler(JobType.HEALTH_CHECK, handle_health_check)

    logger.info("All job handlers registered")
