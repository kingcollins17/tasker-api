import enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from uuid import uuid4
from sqlalchemy import Column, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from app.core.utils.datetime_helper import lagos_now

class AdminRole(str, enum.Enum):
    """Administrative role defining authority tier and operational permissions."""
    ROOT_ADMIN = "ROOT_ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"
    OPERATIONS = "OPERATIONS"
    SUPPORT = "SUPPORT"
    SUPPORT_AGENT = "SUPPORT"  # Alias for backward compatibility
    FINANCE = "FINANCE"

class AdminInvitationStatus(str, enum.Enum):
    """Lifecycle state of an administrator invitation."""
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"

class AdminUser(SQLModel, table=True):
    """Administrator identity table containing credentials, hierarchy pointers, and status."""
    __tablename__ = "admins"  # type: ignore
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for the administrator account")
    email: str = Field(unique=True, index=True, description="Unique email address used for administrator authentication")
    hashed_password: Optional[str] = Field(default=None, description="Argon2 password hash string")
    fullname: Optional[str] = Field(default=None, description="Full legal or display name of the administrator")
    role: AdminRole = Field(description="Assigned administrative role in the authority hierarchy")
    parent_admin_id: Optional[str] = Field(default=None, foreign_key="admins.id", nullable=True, index=True, description="ID of the parent administrator in the hierarchy tree")
    created_by_id: Optional[str] = Field(default=None, foreign_key="admins.id", nullable=True, index=True, description="ID of the administrator who directly invited or created this account")
    is_active: bool = Field(default=False, description="Flag indicating whether the administrator account is currently active")
    is_email_verified: bool = Field(default=False, description="Flag indicating if the administrator's email address has been verified")
    last_login_at: Optional[datetime] = Field(default=None, description="Timestamp of the administrator's most recent successful login")
    region_id: Optional[str] = Field(default=None, foreign_key="regions.id", nullable=True, index=True, description="Optional foreign key referencing a specific regional operational zone")
    created_at: datetime = Field(default_factory=lagos_now, description="Timestamp when the administrator account was created")
    updated_at: datetime = Field(default_factory=lagos_now, description="Timestamp when the administrator account was last updated")

class AdminInvitation(SQLModel, table=True):
    """Invitation record issued to prospective administrators prior to account activation."""
    __tablename__ = "admin_invitations"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique identifier for the administrator invitation")
    email: str = Field(index=True, description="Target email address receiving the administrator invitation")
    role: AdminRole = Field(description="Initial administrative role assigned to the invitee upon acceptance")
    invited_by_id: str = Field(foreign_key="admins.id", index=True, description="ID of the administrator who generated the invitation")
    token_hash: str = Field(index=True, description="SHA-256 hash of the secure invitation token")
    status: AdminInvitationStatus = Field(default=AdminInvitationStatus.PENDING, index=True, description="Current lifecycle status of the invitation")
    expires_at: datetime = Field(index=True, description="Timestamp when the invitation token expires")
    accepted_at: Optional[datetime] = Field(default=None, description="Timestamp when the invitation was accepted")
    revoked_at: Optional[datetime] = Field(default=None, description="Timestamp when the invitation was revoked")
    created_at: datetime = Field(default_factory=lagos_now, description="Timestamp when the invitation was issued")
    updated_at: datetime = Field(default_factory=lagos_now, description="Timestamp when the invitation record was last modified")

class AdminAuditLog(SQLModel, table=True):
    """Immutable audit log recording actions, state changes, and context for administrative operations."""
    __tablename__ = "admin_audit_logs"  # type: ignore

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, description="Unique primary identifier for the audit log entry")

    # Who performed the action
    admin_id: Optional[str] = Field(
        default=None,
        foreign_key="admins.id",
        index=True,
        nullable=True,
        description="ID of the administrator who performed the action"
    )

    # What happened
    action: str = Field(index=True, description="Action identifier or operation name performed")

    # What type of resource was affected
    resource_type: str = Field(index=True, description="Type of resource or entity affected by the action")

    # ID of the affected resource
    resource_id: Optional[str] = Field(
        default=None,
        index=True,
        description="ID of the affected resource or entity"
    )

    # Metadata parameters and state context
    meta_data: Optional[dict] = Field(default=None, sa_column=Column(JSON().with_variant(JSONB, "postgresql")), description="Metadata dictionary containing event details, state changes, or contextual parameters")

    # Optional explanation supplied by the admin
    reason: Optional[str] = Field(default=None, description="Optional explanation or rationale supplied by the administrator")

    # Request/context information
    ip_address: Optional[str] = Field(default=None, description="Client IP address from which the request originated")
    user_agent: Optional[str] = Field(default=None, description="HTTP User-Agent string from the client request header")

    created_at: datetime = Field(
        default_factory=lagos_now,
        index=True,
        description="Immutable timestamp when the audit log was recorded"
    )

