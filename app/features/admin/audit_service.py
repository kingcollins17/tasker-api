from typing import Dict, Any, Optional
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.models.admins import AdminAuditLog
from app.core.repository import Repository
from app.core.utils.datetime_helper import lagos_now


class AuditService:
    """Service dedicated to recording and querying immutable administrative audit logs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit_repo = Repository(AdminAuditLog, session)

    async def log_audit(
        self,
        action: str,
        resource_type: str,
        admin_id: Optional[Any] = None,
        resource_id: Optional[Any] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminAuditLog:
        """Logs an administrative action to the immutable audit log table."""
        str_admin_id = str(admin_id) if admin_id is not None else None
        str_resource_id = str(resource_id) if resource_id is not None else None

        audit_log = AdminAuditLog(
            admin_id=str_admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=str_resource_id,
            meta_data=meta_data,
            reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=lagos_now(),
        )
        return await self.audit_repo.add(audit_log)


def get_audit_service(session: AsyncSession = Depends(get_session)) -> AuditService:
    """FastAPI dependency function to inject AuditService."""
    return AuditService(session)
