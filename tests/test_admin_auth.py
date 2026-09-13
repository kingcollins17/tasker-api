import pytest
import pytest_asyncio
import hashlib
from datetime import datetime, timedelta
from fastapi import status
from httpx import AsyncClient, ASGITransport
from sqlmodel import SQLModel, select, col
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import get_session
from app.core.models.admins import (
    AdminRole,
    AdminUser,
    AdminInvitation,
    AdminInvitationStatus,
    AdminAuditLog,
)
from app.core.utils import security
from app.features.admin.services import AdminService

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine):
    async_session = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession):
    async def _get_test_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Hierarchy & Hierarchy Safety Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_hierarchy_permissions(db_session: AsyncSession):
    service = AdminService(db_session)

    root = AdminUser(
        email="root@tasker.com",
        role=AdminRole.ROOT_ADMIN,
        hashed_password=security.hash_password("RootPass123!"),
        is_active=True,
    )
    db_session.add(root)
    await db_session.commit()
    await db_session.refresh(root)

    alice = AdminUser(
        email="alice@tasker.com",
        role=AdminRole.SUPER_ADMIN,
        parent_admin_id=root.id,
        created_by_id=root.id,
        hashed_password=security.hash_password("AlicePass123!"),
        is_active=True,
    )
    sarah = AdminUser(
        email="sarah@tasker.com",
        role=AdminRole.SUPER_ADMIN,
        parent_admin_id=root.id,
        created_by_id=root.id,
        hashed_password=security.hash_password("SarahPass123!"),
        is_active=True,
    )
    db_session.add_all([alice, sarah])
    await db_session.commit()
    await db_session.refresh(alice)
    await db_session.refresh(sarah)

    bob = AdminUser(
        email="bob@tasker.com",
        role=AdminRole.SUPER_ADMIN,
        parent_admin_id=alice.id,
        created_by_id=alice.id,
        hashed_password=security.hash_password("BobPass123!"),
        is_active=True,
    )
    support1 = AdminUser(
        email="support1@tasker.com",
        role=AdminRole.SUPPORT,
        parent_admin_id=alice.id,
        created_by_id=alice.id,
        hashed_password=security.hash_password("SuppPass123!"),
        is_active=True,
    )
    david = AdminUser(
        email="david@tasker.com",
        role=AdminRole.OPERATIONS,
        parent_admin_id=sarah.id,
        created_by_id=sarah.id,
        hashed_password=security.hash_password("DavidPass123!"),
        is_active=True,
    )
    db_session.add_all([bob, support1, david])
    await db_session.commit()

    # Hierarchy Traversal Checks
    assert await service.is_descendant(root.id, alice.id) is True
    assert await service.is_descendant(root.id, bob.id) is True
    assert await service.is_descendant(alice.id, bob.id) is True
    assert await service.is_descendant(alice.id, support1.id) is True
    assert await service.is_descendant(alice.id, david.id) is False
    assert await service.is_descendant(sarah.id, bob.id) is False

    # Management Authority Checks
    assert await service.can_manage_admin(root, alice) is True
    assert await service.can_manage_admin(root, bob) is True
    assert await service.can_manage_admin(alice, bob) is True
    assert await service.can_manage_admin(alice, sarah) is False
    assert await service.can_manage_admin(alice, david) is False

    # Demotion Rule Checks
    assert await service.can_demote_admin(alice, bob) is True
    assert await service.can_demote_admin(bob, alice) is False
    assert await service.can_demote_admin(sarah, bob) is False
    assert await service.can_demote_admin(root, alice) is True
    assert await service.can_demote_admin(root, bob) is True
    assert await service.can_demote_admin(alice, root) is False


# ---------------------------------------------------------------------------
# 2. Invitation Lifecycle & Acceptance Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invitation_lifecycle(db_session: AsyncSession):
    service = AdminService(db_session)

    root = AdminUser(
        email="root_inv@tasker.com",
        role=AdminRole.ROOT_ADMIN,
        hashed_password=security.hash_password("Pass123!"),
        is_active=True,
    )
    db_session.add(root)
    await db_session.commit()

    # 1. Create invitation
    inv, token = await service.create_invitation(
        requester=root, email="new_admin@tasker.com", role=AdminRole.SUPER_ADMIN
    )
    assert inv.status == AdminInvitationStatus.PENDING
    assert inv.token_hash == hashlib.sha256(token.encode()).hexdigest()

    # 2. Resend invitation (invalidates previous)
    new_inv, new_token = await service.resend_invitation(root, inv.id)
    await db_session.refresh(inv)
    assert inv.status == AdminInvitationStatus.REVOKED
    assert new_inv.status == AdminInvitationStatus.PENDING
    assert token != new_token

    # 3. Accept invitation
    new_user = await service.accept_invitation(
        token=new_token, password="NewSuperPassword123!", fullname="New Super Admin"
    )
    assert new_user.email == "new_admin@tasker.com"
    assert new_user.role == AdminRole.SUPER_ADMIN
    assert new_user.parent_admin_id == root.id
    assert new_user.is_active is True

    # Check invitation status updated
    await db_session.refresh(new_inv)
    assert new_inv.status == AdminInvitationStatus.ACCEPTED


# ---------------------------------------------------------------------------
# 3. Auth & Endpoints Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_auth_endpoints(async_client: AsyncClient, db_session: AsyncSession):
    # Create Root Admin
    root = AdminUser(
        email="admin_endpoint@tasker.com",
        role=AdminRole.ROOT_ADMIN,
        hashed_password=security.hash_password("SecretPass123!"),
        is_active=True,
    )
    db_session.add(root)
    await db_session.commit()

    # Test Simple Password Login
    login_resp = await async_client.post(
        "/api/v1/admin/auth/login",
        json={"email": "admin_endpoint@tasker.com", "password": "SecretPass123!"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    # Invite an Admin via Endpoint
    invite_resp = await async_client.post(
        "/api/v1/admin/users/invite",
        json={"email": "invited_ops@tasker.com", "role": "OPERATIONS"},
        headers=headers,
    )
    assert invite_resp.status_code == 200
    invite_data = invite_resp.json()
    assert invite_data["email"] == "invited_ops@tasker.com"
    assert "invitation_token" in invite_data
    inv_token = invite_data["invitation_token"]

    # Accept Invitation via Endpoint
    accept_resp = await async_client.post(
        "/api/v1/admin/auth/accept-invitation",
        json={
            "token": inv_token,
            "password": "OpsPassword123!",
            "fullname": "Ops User",
        },
    )
    assert accept_resp.status_code == 200
    ops_user_data = accept_resp.json()
    assert ops_user_data["role"] == "OPERATIONS"

    # List Audit Logs
    audit_resp = await async_client.get("/api/v1/admin/audit/logs", headers=headers)
    assert audit_resp.status_code == 200
    logs = audit_resp.json()
    assert len(logs) >= 2
    actions = [log["action"] for log in logs]
    assert "INVITATION_CREATED" in actions
    assert "INVITATION_ACCEPTED" in actions
