from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import get_db, User, VPNConfig
from api.services.wireguard import WireGuardService

router = APIRouter(prefix="/vpn", tags=["vpn"])

wg_service = WireGuardService()


class VPNConfigResponse(BaseModel):
    id: int
    assigned_ip: str
    is_active: bool
    expires_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class VPNConfigCreate(BaseModel):
    user_id: int


class VPNConfigFile(BaseModel):
    config: str
    filename: str


@router.get("/config/{user_id}", response_model=Optional[VPNConfigResponse])
async def get_vpn_config(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get current VPN configuration for a user."""
    result = await db.execute(
        select(VPNConfig)
        .where(VPNConfig.user_id == user_id)
        .where(VPNConfig.is_active == True)
        .where(VPNConfig.is_revoked == False)
        .order_by(VPNConfig.created_at.desc())
    )
    config = result.scalar_one_or_none()
    return config


@router.post("/generate", response_model=VPNConfigFile)
async def generate_vpn_config(
    data: VPNConfigCreate,
    db: AsyncSession = Depends(get_db),
):
    """Generate a new VPN configuration."""
    # Check user exists
    result = await db.execute(
        select(User).where(User.id == data.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check for existing valid config
    result = await db.execute(
        select(VPNConfig)
        .where(VPNConfig.user_id == user.id)
        .where(VPNConfig.is_active == True)
        .where(VPNConfig.is_revoked == False)
    )
    existing = result.scalar_one_or_none()

    if existing and not existing.is_expired:
        # Return existing config
        config_content = wg_service.generate_client_config(
            private_key=existing.private_key,
            address=existing.assigned_ip,
        )
        return VPNConfigFile(
            config=config_content,
            filename=f"vulnlab-{user.discord_username}.conf",
        )

    # Generate new config
    private_key, public_key = wg_service.generate_keypair()
    assigned_ip = await wg_service.allocate_ip(db)
    expires_at = datetime.utcnow() + timedelta(days=settings.vpn_cert_validity_days)

    vpn_config = VPNConfig(
        user_id=user.id,
        private_key=private_key,
        public_key=public_key,
        assigned_ip=assigned_ip,
        expires_at=expires_at,
    )
    db.add(vpn_config)

    # Add peer to WireGuard server
    await wg_service.add_peer(
        public_key=public_key,
        allowed_ips=f"{assigned_ip}/32",
    )

    await db.commit()

    # Generate config file content
    config_content = wg_service.generate_client_config(
        private_key=private_key,
        address=assigned_ip,
    )

    return VPNConfigFile(
        config=config_content,
        filename=f"vulnlab-{user.discord_username}.conf",
    )


@router.post("/revoke/{user_id}")
async def revoke_vpn_config(
    user_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Revoke a user's VPN configuration."""
    result = await db.execute(
        select(VPNConfig)
        .where(VPNConfig.user_id == user_id)
        .where(VPNConfig.is_active == True)
        .where(VPNConfig.is_revoked == False)
    )
    vpn_config = result.scalar_one_or_none()

    if not vpn_config:
        raise HTTPException(status_code=404, detail="No active VPN configuration found")

    vpn_config.is_revoked = True
    vpn_config.is_active = False

    # Remove peer from WireGuard server
    await wg_service.remove_peer(vpn_config.public_key)

    await db.commit()

    return {"status": "revoked"}


@router.get("/status/{public_key}")
async def get_peer_status(public_key: str):
    """Get WireGuard peer status."""
    status = await wg_service.get_peer_status(public_key)
    if not status:
        raise HTTPException(status_code=404, detail="Peer not found")
    return status
