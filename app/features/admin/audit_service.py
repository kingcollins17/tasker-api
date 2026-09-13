from typing import Dict, Any, Optional
from uuid import UUID
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
        parsed_admin_id = None
        if admin_id:
            if isinstance(admin_id, UUID):
                parsed_admin_id = admin_id
            else:
                try:
                    parsed_admin_id = UUID(str(admin_id))
                except (ValueError, TypeError):
                    parsed_admin_id = None

        parsed_resource_id = None
        if resource_id:
            if isinstance(resource_id, UUID):
                parsed_resource_id = resource_id
            else:
                try:
                    parsed_resource_id = UUID(str(resource_id))
                except (ValueError, TypeError):
                    parsed_resource_id = None

        audit_log = AdminAuditLog(
            admin_id=parsed_admin_id,
            action=action,
            resource_type=resource_type,
            resource_id=parsed_resource_id,
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
