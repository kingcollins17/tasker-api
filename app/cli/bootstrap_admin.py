import argparse
import asyncio
import sys
from sqlmodel import col, select

from app.core.database import async_session_factory
from app.core.models.admins import AdminRole, AdminUser
from app.core.utils import security
from app.features.admin.services import AdminService, AuditService


async def bootstrap_root_admin(email: str, password: str, fullname: str = "Root Admin", force: bool = False):
    """CLI function to bootstrap the initial Root Admin user."""
    async with async_session_factory() as session:
        # Check if root admin already exists
        stmt = select(AdminUser).where(col(AdminUser.role) == AdminRole.ROOT_ADMIN, col(AdminUser.is_active) == True)
        existing_roots = (await session.exec(stmt)).all()

        if existing_roots and not force:
            print(f"Error: An active Root Admin already exists ({existing_roots[0].email}). Use --force to add another.", file=sys.stderr)
            return False

        # Check if user with this email exists
        email_stmt = select(AdminUser).where(col(AdminUser.email) == email.lower())
        existing_email = (await session.exec(email_stmt)).first()
        if existing_email:
            print(f"Error: An admin user with email {email} already exists.", file=sys.stderr)
            return False

        root_admin = AdminUser(
            email=email.lower(),
            fullname=fullname,
            role=AdminRole.ROOT_ADMIN,
            parent_admin_id=None,
            created_by_id=None,
            hashed_password=security.hash_password(password),
            is_active=True,
            is_email_verified=True,
            mfa_enabled=False,
        )
        session.add(root_admin)
        await session.commit()
        await session.refresh(root_admin)

        audit_service = AuditService(session)
        await audit_service.log_audit(
            action="ROOT_ADMIN_BOOTSTRAPPED",
            resource_type="AdminUser",
            admin_id=root_admin.id,
            resource_id=root_admin.id,
            meta_data={"role": AdminRole.ROOT_ADMIN.value, "bootstrap": True},
        )

        print(f"Successfully bootstrapped Root Admin: {root_admin.email} (ID: {root_admin.id})")
        return True


def main():
    parser = argparse.ArgumentParser(description="Bootstrap the initial Root Admin user for Taska Platform.")
    parser.add_argument("--email", required=True, help="Root admin email address")
    parser.add_argument("--password", required=True, help="Root admin password")
    parser.add_argument("--fullname", default="Root Administrator", help="Full name of Root Admin")
    parser.add_argument("--force", action="store_true", help="Force creation even if active Root Admin exists")

    args = parser.parse_args()

    success = asyncio.run(bootstrap_root_admin(args.email, args.password, args.fullname, args.force))
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
