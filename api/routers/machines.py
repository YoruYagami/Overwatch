from typing import List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, MachineTemplate, MachineInstance, InstanceStatus

router = APIRouter(prefix="/machines", tags=["machines"])


class MachineTemplateResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    difficulty: str
    category: str
    os_type: str
    is_active: bool

    class Config:
        from_attributes = True


class MachineInstanceResponse(BaseModel):
    id: int
    template_id: int
    status: str
    assigned_ip: str | None
    started_at: datetime | None
    expires_at: datetime | None

    class Config:
        from_attributes = True


@router.get("/templates", response_model=List[MachineTemplateResponse])
async def list_templates(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """List all machine templates."""
    query = select(MachineTemplate)
    if active_only:
        query = query.where(MachineTemplate.is_active == True)
    query = query.order_by(MachineTemplate.difficulty, MachineTemplate.name)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/templates/{name}", response_model=MachineTemplateResponse)
async def get_template(
    name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific machine template."""
    result = await db.execute(
        select(MachineTemplate).where(MachineTemplate.name == name.lower())
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Machine template not found")

    return template


@router.get("/instances", response_model=List[MachineInstanceResponse])
async def list_instances(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List all machine instances for a user."""
    result = await db.execute(
        select(MachineInstance)
        .where(MachineInstance.user_id == user_id)
        .where(MachineInstance.status.not_in([InstanceStatus.TERMINATED]))
        .order_by(MachineInstance.created_at.desc())
    )
    return result.scalars().all()


@router.post("/instances/{template_name}/start")
async def start_instance(
    template_name: str,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Start a machine instance."""
    # Get template
    result = await db.execute(
        select(MachineTemplate).where(MachineTemplate.name == template_name.lower())
    )
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Machine template not found")

    # Check for existing active instance
    result = await db.execute(
        select(MachineInstance)
        .where(MachineInstance.user_id == user_id)
        .where(MachineInstance.template_id == template.id)
        .where(MachineInstance.status.not_in([InstanceStatus.TERMINATED, InstanceStatus.STOPPED]))
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Instance already exists")

    # Create instance (actual start handled by bot/background task)
    instance = MachineInstance(
        user_id=user_id,
        template_id=template.id,
        proxmox_node=template.proxmox_node,
        status=InstanceStatus.PENDING,
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)

    return {"status": "pending", "instance_id": instance.id}


@router.post("/instances/{instance_id}/stop")
async def stop_instance(
    instance_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Stop a machine instance."""
    result = await db.execute(
        select(MachineInstance)
        .where(MachineInstance.id == instance_id)
        .where(MachineInstance.user_id == user_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    if instance.status != InstanceStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Instance is not running")

    instance.status = InstanceStatus.STOPPING
    await db.commit()

    return {"status": "stopping"}
