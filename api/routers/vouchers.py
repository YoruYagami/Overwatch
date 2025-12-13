import secrets
import string
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, Voucher, VoucherType, User, Subscription, SubscriptionTier

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


class VoucherResponse(BaseModel):
    id: int
    code: str
    voucher_type: str
    duration_days: int
    is_used: bool
    created_at: datetime
    expires_at: datetime | None

    class Config:
        from_attributes = True


class VoucherCreate(BaseModel):
    voucher_type: VoucherType
    count: int = 1
    expires_days: int | None = None
    notes: str | None = None
    created_by: str | None = None


class VoucherActivate(BaseModel):
    code: str
    user_id: int


def generate_voucher_code(length: int = 16) -> str:
    """Generate a random voucher code."""
    # Format: XXXX-XXXX-XXXX-XXXX
    chars = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(chars) for _ in range(length))
    return f"{code[:4]}-{code[4:8]}-{code[8:12]}-{code[12:16]}"


@router.get("/", response_model=List[VoucherResponse])
async def list_vouchers(
    used: Optional[bool] = None,
    voucher_type: Optional[VoucherType] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List vouchers with optional filters."""
    query = select(Voucher)

    if used is not None:
        query = query.where(Voucher.is_used == used)
    if voucher_type is not None:
        query = query.where(Voucher.voucher_type == voucher_type)

    query = query.order_by(Voucher.created_at.desc()).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/generate", response_model=List[VoucherResponse])
async def generate_vouchers(
    data: VoucherCreate,
    db: AsyncSession = Depends(get_db),
):
    """Generate new voucher codes."""
    if data.count < 1 or data.count > 100:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 100")

    duration_days = 90 if data.voucher_type == VoucherType.DAYS_90 else 365
    expires_at = None
    if data.expires_days:
        expires_at = datetime.utcnow() + timedelta(days=data.expires_days)

    vouchers = []
    for _ in range(data.count):
        # Generate unique code
        while True:
            code = generate_voucher_code()
            existing = await db.execute(
                select(Voucher).where(Voucher.code == code)
            )
            if not existing.scalar_one_or_none():
                break

        voucher = Voucher(
            code=code,
            voucher_type=data.voucher_type,
            duration_days=duration_days,
            expires_at=expires_at,
            notes=data.notes,
            created_by=data.created_by,
        )
        db.add(voucher)
        vouchers.append(voucher)

    await db.commit()

    for v in vouchers:
        await db.refresh(v)

    return vouchers


@router.post("/activate")
async def activate_voucher(
    data: VoucherActivate,
    db: AsyncSession = Depends(get_db),
):
    """Activate a voucher code for a user."""
    # Find voucher
    code = data.code.strip().upper()
    result = await db.execute(
        select(Voucher).where(Voucher.code == code)
    )
    voucher = result.scalar_one_or_none()

    if not voucher:
        raise HTTPException(status_code=404, detail="Invalid voucher code")

    if voucher.is_used:
        raise HTTPException(status_code=400, detail="Voucher already used")

    if voucher.expires_at and voucher.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Voucher expired")

    # Find user
    result = await db.execute(
        select(User).where(User.id == data.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Determine tier
    tier = (
        SubscriptionTier.BASIC
        if voucher.voucher_type == VoucherType.DAYS_90
        else SubscriptionTier.PRO
    )

    # Calculate expiration
    expires_at = datetime.utcnow() + timedelta(days=voucher.duration_days)

    # Check for existing subscription
    if user.has_active_subscription:
        active_sub = user.active_subscription
        # Extend from current expiration
        expires_at = active_sub.expires_at + timedelta(days=voucher.duration_days)
        active_sub.expires_at = expires_at
        active_sub.tier = tier
    else:
        # Create new subscription
        subscription = Subscription(
            user_id=user.id,
            tier=tier,
            source="voucher",
            voucher_id=voucher.id,
            expires_at=expires_at,
        )
        db.add(subscription)

    # Mark voucher as used
    voucher.is_used = True
    voucher.redeemed_by = user.id
    voucher.redeemed_at = datetime.utcnow()

    await db.commit()

    return {
        "status": "activated",
        "tier": tier.value,
        "expires_at": expires_at.isoformat(),
        "duration_days": voucher.duration_days,
    }


@router.get("/{code}", response_model=VoucherResponse)
async def get_voucher(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Get voucher by code."""
    result = await db.execute(
        select(Voucher).where(Voucher.code == code.upper())
    )
    voucher = result.scalar_one_or_none()

    if not voucher:
        raise HTTPException(status_code=404, detail="Voucher not found")

    return voucher


@router.delete("/{code}")
async def delete_voucher(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an unused voucher."""
    result = await db.execute(
        select(Voucher).where(Voucher.code == code.upper())
    )
    voucher = result.scalar_one_or_none()

    if not voucher:
        raise HTTPException(status_code=404, detail="Voucher not found")

    if voucher.is_used:
        raise HTTPException(status_code=400, detail="Cannot delete used voucher")

    await db.delete(voucher)
    await db.commit()

    return {"status": "deleted"}
