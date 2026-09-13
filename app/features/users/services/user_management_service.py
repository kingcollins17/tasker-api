from typing import Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.users import User
from app.core.utils.datetime_helper import lagos_now
from app.features.admin.services import AuditService, get_audit_service


class UserManagementService:
    """Service handling administrative write operations for user activation and status updates."""

    def __init__(
        self,
        session: AsyncSession,
        audit_service: Optional[AuditService] = None,
    ):
        self.session = session
        self.audit_service = audit_service or AuditService(session)

    async def set_user_active_status(
        self,
        user_id: str,
        is_active: bool,
        admin_id: str,
        reason: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> User:
        """Activate or deactivate platform user account, updating meta_data and logging audit trail."""
        stmt = select(User).where(col(User.id) == user_id)
        user = (await self.session.exec(stmt)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        user.is_active = is_active
        user.updated_at = lagos_now()

        current_meta = dict(user.meta_data or {})
        action_name = "activated" if is_active else "deactivated"

        status_history = list(current_meta.get("status_history", []))
        history_entry: Dict[str, Any] = {
            "action": action_name,
            "admin_id": admin_id,
            "reason": reason,
            "timestamp": lagos_now().isoformat(),
        }
        if meta_data:
            history_entry["extra_meta"] = meta_data

        status_history.append(history_entry)
        current_meta["status_history"] = status_history
        current_meta[f"last_{action_name}_reason"] = reason
        current_meta[f"last_{action_name}_at"] = lagos_now().isoformat()
        current_meta[f"last_{action_name}_by"] = admin_id

        if meta_data:
            current_meta.update(meta_data)

        user.meta_data = current_meta
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        audit_action = "USER_ACTIVATED" if is_active else "USER_DEACTIVATED"
        await self.audit_service.log_audit(
            action=audit_action,
            resource_type="User",
            admin_id=admin_id,
            resource_id=user.id,
            meta_data={
                "user_email": user.email,
                "reason": reason,
                "extra_meta": meta_data or {},
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return user


def get_user_management_service(
    session: AsyncSession = Depends(get_session),
    audit_service: AuditService = Depends(get_audit_service),
) -> UserManagementService:
    """Dependency provider injecting UserManagementService."""
    return UserManagementService(session, audit_service=audit_service)
