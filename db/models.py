from datetime import datetime, timedelta
from enum import Enum as PyEnum
from typing import Optional, List
from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    Enum,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ============================================================================
# ENUMS
# ============================================================================

class SubscriptionTier(str, PyEnum):
    FREE = "free"
    BASIC = "basic"          # 90 days voucher
    PRO = "pro"              # 365 days voucher
    PATREON_TIER1 = "patreon_tier1"
    PATREON_TIER2 = "patreon_tier2"
    PATREON_TIER3 = "patreon_tier3"


class VoucherType(str, PyEnum):
    DAYS_90 = "90_days"
    DAYS_365 = "365_days"


class MachineStatus(str, PyEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class InstanceStatus(str, PyEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    EXTENDING = "extending"
    STOPPING = "stopping"
    STOPPED = "stopped"
    TERMINATED = "terminated"
    ERROR = "error"


class RTLabStatus(str, PyEnum):
    AVAILABLE = "available"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# ============================================================================
# USER & AUTHENTICATION
# ============================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    discord_username: Mapped[str] = mapped_column(String(100), nullable=False)
    discord_discriminator: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Patreon linking
    patreon_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True)
    patreon_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    patreon_tier: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    ban_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    vpn_configs: Mapped[List["VPNConfig"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    machine_instances: Mapped[List["MachineInstance"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    chain_instances: Mapped[List["ChainInstance"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    rtlab_sessions: Mapped[List["RTLabSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    redeemed_vouchers: Mapped[List["Voucher"]] = relationship(back_populates="redeemed_by_user")

    @property
    def active_subscription(self) -> Optional["Subscription"]:
        """Get the user's currently active subscription."""
        now = datetime.utcnow()
        for sub in self.subscriptions:
            if sub.is_active and sub.expires_at > now:
                return sub
        return None

    @property
    def has_active_subscription(self) -> bool:
        return self.active_subscription is not None


# ============================================================================
# VOUCHER & SUBSCRIPTION SYSTEM
# ============================================================================

class Voucher(Base):
    __tablename__ = "vouchers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    voucher_type: Mapped[VoucherType] = mapped_column(Enum(VoucherType), nullable=False)

    # Duration in days
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # Usage
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    redeemed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Metadata
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Admin who created
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # Voucher code expiration

    # Relationships
    redeemed_by_user: Mapped[Optional["User"]] = relationship(back_populates="redeemed_vouchers")

    __table_args__ = (
        Index("ix_vouchers_code_unused", "code", postgresql_where="is_used = false"),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    tier: Mapped[SubscriptionTier] = mapped_column(Enum(SubscriptionTier), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Source of subscription
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # "voucher", "patreon", "admin"
    voucher_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vouchers.id"), nullable=True)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="subscriptions")

    __table_args__ = (
        Index("ix_subscriptions_user_active", "user_id", postgresql_where="is_active = true"),
    )


# ============================================================================
# VPN CONFIGURATION
# ============================================================================

class VPNConfig(Base):
    __tablename__ = "vpn_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # WireGuard keys
    private_key: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # Network
    assigned_ip: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_handshake: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="vpn_configs")

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_revoked and not self.is_expired


# ============================================================================
# MACHINE TEMPLATES & INSTANCES
# ============================================================================

class MachineTemplate(Base):
    """Template for a vulnerable machine (stored in Proxmox as template)."""
    __tablename__ = "machine_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Proxmox template
    proxmox_template_id: Mapped[int] = mapped_column(Integer, nullable=False)  # VMID of template
    proxmox_node: Mapped[str] = mapped_column(String(50), nullable=False)

    # Resources
    cpu_cores: Mapped[int] = mapped_column(Integer, default=2)
    memory_mb: Mapped[int] = mapped_column(Integer, default=2048)
    disk_gb: Mapped[int] = mapped_column(Integer, default=20)

    # Difficulty & category
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")  # easy, medium, hard, insane
    category: Mapped[str] = mapped_column(String(50), default="general")  # web, ad, privesc, etc.
    os_type: Mapped[str] = mapped_column(String(20), default="linux")  # linux, windows

    # Flags (for validation)
    user_flag_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    root_flag_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    instances: Mapped[List["MachineInstance"]] = relationship(back_populates="template", cascade="all, delete-orphan")
    chain_machines: Mapped[List["ChainMachine"]] = relationship(back_populates="machine_template")


class MachineInstance(Base):
    """A running instance of a machine for a specific user."""
    __tablename__ = "machine_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    template_id: Mapped[int] = mapped_column(ForeignKey("machine_templates.id"), nullable=False)

    # Proxmox instance
    proxmox_vmid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Cloned VM ID
    proxmox_node: Mapped[str] = mapped_column(String(50), nullable=False)

    # Network
    assigned_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Status
    status: Mapped[InstanceStatus] = mapped_column(Enum(InstanceStatus), default=InstanceStatus.PENDING)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extended_count: Mapped[int] = mapped_column(Integer, default=0)

    # Progress tracking
    user_flag_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    root_flag_submitted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="machine_instances")
    template: Mapped["MachineTemplate"] = relationship(back_populates="instances")

    __table_args__ = (
        UniqueConstraint("user_id", "template_id", name="uq_user_machine_instance"),
        Index("ix_machine_instances_status", "status"),
    )

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def time_remaining(self) -> Optional[timedelta]:
        if not self.expires_at:
            return None
        remaining = self.expires_at - datetime.utcnow()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)


# ============================================================================
# CHAINS (Multiple machines scenarios)
# ============================================================================

class Chain(Base):
    """A chain is a scenario with multiple interconnected machines."""
    __tablename__ = "chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Difficulty
    difficulty: Mapped[str] = mapped_column(String(20), default="hard")
    estimated_time_hours: Mapped[int] = mapped_column(Integer, default=4)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    machines: Mapped[List["ChainMachine"]] = relationship(back_populates="chain", cascade="all, delete-orphan")
    instances: Mapped[List["ChainInstance"]] = relationship(back_populates="chain", cascade="all, delete-orphan")


class ChainMachine(Base):
    """Association between chains and machine templates with order."""
    __tablename__ = "chain_machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    machine_template_id: Mapped[int] = mapped_column(ForeignKey("machine_templates.id"), nullable=False)

    order: Mapped[int] = mapped_column(Integer, default=0)  # Order in the chain
    is_entry_point: Mapped[bool] = mapped_column(Boolean, default=False)  # First machine to attack

    # Relationships
    chain: Mapped["Chain"] = relationship(back_populates="machines")
    machine_template: Mapped["MachineTemplate"] = relationship(back_populates="chain_machines")

    __table_args__ = (
        UniqueConstraint("chain_id", "machine_template_id", name="uq_chain_machine"),
    )


class ChainInstance(Base):
    """A running chain instance for a user."""
    __tablename__ = "chain_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)

    # Status
    status: Mapped[InstanceStatus] = mapped_column(Enum(InstanceStatus), default=InstanceStatus.PENDING)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    extended_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="chain_instances")
    chain: Mapped["Chain"] = relationship(back_populates="instances")
    machine_instances: Mapped[List["ChainMachineInstance"]] = relationship(back_populates="chain_instance", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "chain_id", name="uq_user_chain_instance"),
    )


class ChainMachineInstance(Base):
    """Individual machine instances within a chain instance."""
    __tablename__ = "chain_machine_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_instance_id: Mapped[int] = mapped_column(ForeignKey("chain_instances.id"), nullable=False)
    machine_template_id: Mapped[int] = mapped_column(ForeignKey("machine_templates.id"), nullable=False)

    # Proxmox
    proxmox_vmid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assigned_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Status
    status: Mapped[InstanceStatus] = mapped_column(Enum(InstanceStatus), default=InstanceStatus.PENDING)

    # Relationships
    chain_instance: Mapped["ChainInstance"] = relationship(back_populates="machine_instances")


# ============================================================================
# RED TEAM LABS (Shared instances with voting)
# ============================================================================

class RTLab(Base):
    """Red Team Lab - shared environment for multiple users."""
    __tablename__ = "rtlabs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Configuration
    max_participants: Mapped[int] = mapped_column(Integer, default=10)
    reset_votes_required: Mapped[int] = mapped_column(Integer, default=3)  # Votes needed to reset

    # Chain or single machine
    chain_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chains.id"), nullable=True)
    machine_template_id: Mapped[Optional[int]] = mapped_column(ForeignKey("machine_templates.id"), nullable=True)

    # Status
    status: Mapped[RTLabStatus] = mapped_column(Enum(RTLabStatus), default=RTLabStatus.AVAILABLE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Current run
    current_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sessions: Mapped[List["RTLabSession"]] = relationship(back_populates="rtlab", cascade="all, delete-orphan")


class RTLabSession(Base):
    """A session/participation in an RT Lab."""
    __tablename__ = "rtlab_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rtlab_id: Mapped[int] = mapped_column(ForeignKey("rtlabs.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Session info
    session_number: Mapped[int] = mapped_column(Integer, nullable=False)  # Which reset iteration

    # Voting
    has_voted_reset: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    rtlab: Mapped["RTLab"] = relationship(back_populates="sessions")
    user: Mapped["User"] = relationship(back_populates="rtlab_sessions")

    __table_args__ = (
        UniqueConstraint("rtlab_id", "user_id", "session_number", name="uq_rtlab_user_session"),
    )
