import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from fastapi import Depends, HTTPException, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.error_handler import AppErrorHandler
from app.core.models.admins import (
    AdminInvitation,
    AdminInvitationStatus,
    AdminRole,
    AdminUser,
)
from app.core.repository import Repository
from app.core.utils import mfa, security
from app.core.utils.datetime_helper import lagos_now
from app.features.admin.audit_service import AuditService, get_audit_service


class AdminService:
    def __init__(self, session: AsyncSession, audit_service: Optional[AuditService] = None):
        self.session = session
        self.admin_repo = Repository(AdminUser, session)
        self.invitation_repo = Repository(AdminInvitation, session)
        self.audit_service = audit_service or AuditService(session)

    async def is_descendant(self, ancestor_id: str, target_id: str) -> bool:
        """Traverses parent pointers up the hierarchy tree to determine if target_id is below ancestor_id."""
        if not ancestor_id or not target_id or ancestor_id == target_id:
            return False

        curr_id = target_id
        visited = set()

        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            admin = await self.admin_repo.get(curr_id)
            if not admin or not admin.parent_admin_id:
                break
            if admin.parent_admin_id == ancestor_id:
                return True
            curr_id = admin.parent_admin_id

        return False

    async def can_manage_admin(self, requester: AdminUser, target: AdminUser) -> bool:
        """Determines if requester has hierarchy authority over target admin."""
        if requester.id == target.id:
            return False

        if target.role == AdminRole.ROOT_ADMIN:
            return False

        if requester.role == AdminRole.ROOT_ADMIN:
            return True

        if target.parent_admin_id == requester.id:
            return True

        return await self.is_descendant(requester.id, target.id)

    async def can_demote_admin(self, requester: AdminUser, target: AdminUser) -> bool:
        """Checks demotion rules: Root Admin can demote anyone; Super Admin can demote Super Admin only if directly created by them."""
        if requester.id == target.id:
            return False

        if target.role == AdminRole.ROOT_ADMIN:
            return False

        if requester.role == AdminRole.ROOT_ADMIN:
            return True

        if target.role == AdminRole.SUPER_ADMIN:
            if requester.role == AdminRole.SUPER_ADMIN and (target.created_by_id == requester.id or target.parent_admin_id == requester.id):
                return True
            return False

        return await self.can_manage_admin(requester, target)

    def can_invite_role(self, requester_role: AdminRole, target_role: AdminRole) -> bool:
        """Validates role creation permissions."""
        if target_role == AdminRole.ROOT_ADMIN:
            return False
        if requester_role == AdminRole.ROOT_ADMIN:
            return True
        if requester_role == AdminRole.SUPER_ADMIN:
            return target_role in [
                AdminRole.SUPER_ADMIN,
                AdminRole.OPERATIONS,
                AdminRole.SUPPORT,
                AdminRole.FINANCE,
            ]
        return False

    async def create_invitation(
        self,
        requester: AdminUser,
        email: str,
        role: AdminRole,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[AdminInvitation, str]:
        """Creates a secure admin invitation."""
        if role == AdminRole.ROOT_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot invite an administrator as ROOT_ADMIN. Only one Root Admin can exist.",
            )

        if not self.can_invite_role(requester.role, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Admin with role {requester.role.value} is not authorized to invite role {role.value}",
            )

        # Check if active admin exists with this email
        stmt = select(AdminUser).where(col(AdminUser.email) == email.lower())
        existing = (await self.session.exec(stmt)).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An administrator with this email already exists",
            )

        # Invalidate/Revoke previous pending invitations for this email
        inv_stmt = select(AdminInvitation).where(
            col(AdminInvitation.email) == email.lower(),
            col(AdminInvitation.status) == AdminInvitationStatus.PENDING,
        )
        pending_invs = (await self.session.exec(inv_stmt)).all()
        for inv in pending_invs:
            inv.status = AdminInvitationStatus.REVOKED
            inv.revoked_at = lagos_now()
            self.session.add(inv)

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = lagos_now() + timedelta(hours=48)

        invitation = AdminInvitation(
            email=email.lower(),
            role=role,
            invited_by_id=requester.id,
            token_hash=token_hash,
            status=AdminInvitationStatus.PENDING,
            expires_at=expires_at,
        )
        invitation = await self.invitation_repo.add(invitation)

        await self.audit_service.log_audit(
            action="INVITATION_CREATED",
            resource_type="AdminInvitation",
            admin_id=requester.id,
            resource_id=invitation.id,
            meta_data={"email": email, "role": role.value},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return invitation, raw_token

    async def resend_invitation(
        self,
        requester: AdminUser,
        invitation_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[AdminInvitation, str]:
        """Resends an invitation by revoking the old one and issuing a new token."""
        old_inv = await self.invitation_repo.get(invitation_id)
        if not old_inv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

        if old_inv.invited_by_id != requester.id and requester.role != AdminRole.ROOT_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to resend this invitation")

        old_inv.status = AdminInvitationStatus.REVOKED
        old_inv.revoked_at = lagos_now()
        await self.invitation_repo.add(old_inv)

        new_inv, raw_token = await self.create_invitation(
            requester=requester,
            email=old_inv.email,
            role=old_inv.role,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        await self.audit_service.log_audit(
            action="INVITATION_RESENT",
            resource_type="AdminInvitation",
            admin_id=requester.id,
            resource_id=new_inv.id,
            meta_data={"previous_invitation_id": invitation_id, "new_invitation_id": new_inv.id, "email": old_inv.email, "role": old_inv.role.value},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return new_inv, raw_token

    async def revoke_invitation(
        self,
        requester: AdminUser,
        invitation_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminInvitation:
        """Revokes an active pending invitation."""
        inv = await self.invitation_repo.get(invitation_id)
        if not inv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

        if inv.invited_by_id != requester.id and requester.role != AdminRole.ROOT_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to revoke this invitation")

        if inv.status != AdminInvitationStatus.PENDING:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invitation is already {inv.status.value}")

        inv.status = AdminInvitationStatus.REVOKED
        inv.revoked_at = lagos_now()
        inv = await self.invitation_repo.add(inv)

        await self.audit_service.log_audit(
            action="INVITATION_REVOKED",
            resource_type="AdminInvitation",
            admin_id=requester.id,
            resource_id=inv.id,
            meta_data={"old_status": "PENDING", "new_status": "REVOKED"},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return inv

    async def accept_invitation(
        self,
        token: str,
        password: str,
        fullname: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminUser:
        """Consumes a valid invitation token and creates the administrator account."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        stmt = select(AdminInvitation).where(col(AdminInvitation.token_hash) == token_hash)
        invitation = (await self.session.exec(stmt)).first()

        if not invitation:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid invitation token")

        if invitation.status != AdminInvitationStatus.PENDING:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invitation token is {invitation.status.value}")

        if invitation.expires_at < lagos_now():
            invitation.status = AdminInvitationStatus.EXPIRED
            await self.invitation_repo.add(invitation)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation token has expired")

        # Check existing user email
        existing = (await self.session.exec(select(AdminUser).where(col(AdminUser.email) == invitation.email))).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An administrator account with this email already exists")

        invitation.status = AdminInvitationStatus.ACCEPTED
        invitation.accepted_at = lagos_now()
        await self.invitation_repo.add(invitation)

        admin = AdminUser(
            email=invitation.email,
            fullname=fullname,
            role=invitation.role,
            parent_admin_id=invitation.invited_by_id,
            created_by_id=invitation.invited_by_id,
            hashed_password=security.hash_password(password),
            is_active=True,
            is_email_verified=True,
        )
        admin = await self.admin_repo.add(admin)

        await self.audit_service.log_audit(
            action="INVITATION_ACCEPTED",
            resource_type="AdminUser",
            admin_id=admin.id,
            resource_id=admin.id,
            meta_data={"email": admin.email, "role": admin.role.value, "parent_admin_id": admin.parent_admin_id},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return admin

    async def login(
        self,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[AdminUser, Dict[str, Any]]:
        """Authenticates admin credentials via email and password, returning tokens."""
        stmt = select(AdminUser).where(col(AdminUser.email) == email.lower())
        admin = (await self.session.exec(stmt)).first()

        if not admin or not admin.hashed_password or not security.verify_password(password, admin.hashed_password):
            await self.audit_service.log_audit(
                action="LOGIN_FAILED",
                resource_type="AdminUser",
                reason="Invalid credentials",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

        if not admin.is_active:
            await self.audit_service.log_audit(
                action="LOGIN_FAILED",
                resource_type="AdminUser",
                admin_id=admin.id,
                resource_id=admin.id,
                reason="Account deactivated",
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator account is deactivated")

        admin.last_login_at = lagos_now()
        await self.admin_repo.add(admin)

        payload = {"id": admin.id, "type": "admin", "role": admin.role.value}
        access_token = security.create_access_token(payload)
        refresh_token = security.create_access_token(
            {"id": admin.id, "type": "admin_refresh"},
            expires_delta=timedelta(days=7),
        )

        await self.audit_service.log_audit(
            action="LOGIN_SUCCESS",
            resource_type="AdminUser",
            admin_id=admin.id,
            resource_id=admin.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return admin, {"access_token": access_token, "refresh_token": refresh_token}

    async def change_admin_role(
        self,
        requester: AdminUser,
        target_admin_id: str,
        new_role: AdminRole,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminUser:
        """Promotes or demotes an admin role respecting hierarchy safety rules."""
        target = await self.admin_repo.get(target_admin_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target administrator not found")

        if not await self.can_manage_admin(requester, target):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to manage this administrator")

        if target.role == AdminRole.SUPER_ADMIN and new_role != AdminRole.SUPER_ADMIN and new_role != AdminRole.ROOT_ADMIN:
            if not await self.can_demote_admin(requester, target):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to demote this Super Admin")

        if new_role == AdminRole.ROOT_ADMIN:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot assign ROOT_ADMIN role. Only one Root Admin can exist.")

        prev_role = target.role.value
        target.role = new_role
        target = await self.admin_repo.add(target)

        action = "ROLE_PROMOTED" if new_role in [AdminRole.SUPER_ADMIN, AdminRole.ROOT_ADMIN] else "ROLE_DEMOTED"
        await self.audit_service.log_audit(
            action=action,
            resource_type="AdminUser",
            admin_id=requester.id,
            resource_id=target.id,
            meta_data={"old_role": prev_role, "new_role": new_role.value},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return target

    async def deactivate_admin(
        self,
        requester: AdminUser,
        target_admin_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminUser:
        """Deactivates an administrator account while preserving history."""
        target = await self.admin_repo.get(target_admin_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target administrator not found")

        if not await self.can_manage_admin(requester, target):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to manage this administrator")

        if target.role == AdminRole.ROOT_ADMIN:
            stmt = select(AdminUser).where(col(AdminUser.role) == AdminRole.ROOT_ADMIN, col(AdminUser.is_active) == True)
            active_roots = (await self.session.exec(stmt)).all()
            if len(active_roots) <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate the final active Root Admin")

        target.is_active = False
        target = await self.admin_repo.add(target)

        await self.audit_service.log_audit(
            action="ADMIN_DEACTIVATED",
            resource_type="AdminUser",
            admin_id=requester.id,
            resource_id=target.id,
            meta_data={"old_is_active": True, "new_is_active": False},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return target

    async def reactivate_admin(
        self,
        requester: AdminUser,
        target_admin_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminUser:
        """Reactivates a deactivated administrator account."""
        target = await self.admin_repo.get(target_admin_id)
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target administrator not found")

        if not await self.can_manage_admin(requester, target):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to manage this administrator")

        target.is_active = True
        target = await self.admin_repo.add(target)

        await self.audit_service.log_audit(
            action="ADMIN_REACTIVATED",
            resource_type="AdminUser",
            admin_id=requester.id,
            resource_id=target.id,
            meta_data={"old_is_active": False, "new_is_active": True},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return target


def get_admin_service(
    session: AsyncSession = Depends(get_session),
    audit_service: AuditService = Depends(get_audit_service),
) -> AdminService:
    """FastAPI dependency function to inject AdminService."""
    return AdminService(session, audit_service=audit_service)
