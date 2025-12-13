from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db import (
    get_db,
    User,
    MachineTemplate,
    MachineInstance,
    Chain,
    RTLab,
    Voucher,
    Subscription,
    InstanceStatus,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ============================================================================
# MODELS
# ============================================================================

class UserResponse(BaseModel):
    id: int
    discord_id: int
    discord_username: str
    patreon_id: str | None
    is_active: bool
    is_banned: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MachineTemplateCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    proxmox_template_id: int
    proxmox_node: str = "pve"
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_gb: int = 20
    difficulty: str = "medium"
    category: str = "general"
    os_type: str = "linux"


class ChainCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    difficulty: str = "hard"
    estimated_time_hours: int = 4
    machine_template_ids: List[int]


class RTLabCreate(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    max_participants: int = 10
    reset_votes_required: int = 3
    chain_id: int | None = None
    machine_template_id: int | None = None


class StatsResponse(BaseModel):
    total_users: int
    active_subscriptions: int
    active_instances: int
    total_machines: int
    unused_vouchers: int


# ============================================================================
# STATS
# ============================================================================

@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Get platform statistics."""
    # Total users
    result = await db.execute(select(func.count(User.id)))
    total_users = result.scalar() or 0

    # Active subscriptions
    result = await db.execute(
        select(func.count(Subscription.id))
        .where(Subscription.is_active == True)
        .where(Subscription.expires_at > datetime.utcnow())
    )
    active_subscriptions = result.scalar() or 0

    # Active instances
    result = await db.execute(
        select(func.count(MachineInstance.id))
        .where(MachineInstance.status == InstanceStatus.RUNNING)
    )
    active_instances = result.scalar() or 0

    # Total machines
    result = await db.execute(
        select(func.count(MachineTemplate.id))
        .where(MachineTemplate.is_active == True)
    )
    total_machines = result.scalar() or 0

    # Unused vouchers
    result = await db.execute(
        select(func.count(Voucher.id))
        .where(Voucher.is_used == False)
    )
    unused_vouchers = result.scalar() or 0

    return StatsResponse(
        total_users=total_users,
        active_subscriptions=active_subscriptions,
        active_instances=active_instances,
        total_machines=total_machines,
        unused_vouchers=unused_vouchers,
    )


# ============================================================================
# USERS
# ============================================================================

@router.get("/users", response_model=List[UserResponse])
async def list_users(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    result = await db.execute(
        select(User)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    reason: str = "No reason provided",
    db: AsyncSession = Depends(get_db),
):
    """Ban a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_banned = True
    user.ban_reason = reason
    await db.commit()

    return {"status": "banned", "user_id": user_id}


@router.post("/users/{user_id}/unban")
async def unban_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Unban a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_banned = False
    user.ban_reason = None
    await db.commit()

    return {"status": "unbanned", "user_id": user_id}


# ============================================================================
# MACHINE TEMPLATES
# ============================================================================

@router.post("/machines/templates")
async def create_machine_template(
    data: MachineTemplateCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new machine template."""
    # Check if name exists
    result = await db.execute(
        select(MachineTemplate).where(MachineTemplate.name == data.name.lower())
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Machine name already exists")

    template = MachineTemplate(
        name=data.name.lower(),
        display_name=data.display_name,
        description=data.description,
        proxmox_template_id=data.proxmox_template_id,
        proxmox_node=data.proxmox_node,
        cpu_cores=data.cpu_cores,
        memory_mb=data.memory_mb,
        disk_gb=data.disk_gb,
        difficulty=data.difficulty,
        category=data.category,
        os_type=data.os_type,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return {"status": "created", "id": template.id}


@router.delete("/machines/templates/{template_id}")
async def delete_machine_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete (deactivate) a machine template."""
    result = await db.execute(
        select(MachineTemplate).where(MachineTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.is_active = False
    await db.commit()

    return {"status": "deactivated"}


# ============================================================================
# CHAINS
# ============================================================================

@router.post("/chains")
async def create_chain(
    data: ChainCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chain."""
    from db import ChainMachine

    # Check if name exists
    result = await db.execute(
        select(Chain).where(Chain.name == data.name.lower())
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Chain name already exists")

    chain = Chain(
        name=data.name.lower(),
        display_name=data.display_name,
        description=data.description,
        difficulty=data.difficulty,
        estimated_time_hours=data.estimated_time_hours,
    )
    db.add(chain)
    await db.flush()

    # Add machines to chain
    for i, template_id in enumerate(data.machine_template_ids):
        chain_machine = ChainMachine(
            chain_id=chain.id,
            machine_template_id=template_id,
            order=i,
            is_entry_point=(i == 0),
        )
        db.add(chain_machine)

    await db.commit()
    await db.refresh(chain)

    return {"status": "created", "id": chain.id}


# ============================================================================
# RT LABS
# ============================================================================

@router.post("/rtlabs")
async def create_rtlab(
    data: RTLabCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new Red Team Lab."""
    # Check if name exists
    result = await db.execute(
        select(RTLab).where(RTLab.name == data.name.lower())
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="RTLab name already exists")

    rtlab = RTLab(
        name=data.name.lower(),
        display_name=data.display_name,
        description=data.description,
        max_participants=data.max_participants,
        reset_votes_required=data.reset_votes_required,
        chain_id=data.chain_id,
        machine_template_id=data.machine_template_id,
    )
    db.add(rtlab)
    await db.commit()
    await db.refresh(rtlab)

    return {"status": "created", "id": rtlab.id}
