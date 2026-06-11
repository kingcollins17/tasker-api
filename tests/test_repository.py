import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import SQLModel
from app.core.models.users import User, UserType
from app.core.repository import Repository, QueryOptions

@pytest.mark.anyio
async def test_repository_crud():
    # 1. Setup async SQLite database in memory
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        
    from sqlalchemy.orm import sessionmaker
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        repo = Repository(User, session)
        
        # 2. Test add
        user1 = User(
            phone_number="+2348011111111",
            email="user1@tasker.com",
            type=UserType.CUSTOMER,
            is_active=True
        )
        user2 = User(
            phone_number="+2348022222222",
            email="user2@tasker.com",
            type=UserType.PROVIDER,
            is_active=False
        )
        
        await repo.add(user1)
        await repo.add(user2)
        
        # 3. Test get
        db_user1 = await repo.get(user1.id)
        assert db_user1 is not None
        assert db_user1.email == "user1@tasker.com"
        assert db_user1.created_at is not None
        assert db_user1.updated_at is not None
        
        # 4. Test get_all (no options)
        all_users = await repo.get_all()
        assert len(all_users) == 2
        
        # 5. Test get_all with filtering (options)
        options = QueryOptions(filters={"type": UserType.CUSTOMER})
        customers = await repo.get_all(options)
        assert len(customers) == 1
        assert customers[0].email == "user1@tasker.com"
        
        # 6. Test get_all with ordering & limit
        options_ord = QueryOptions(
            order_by="email",
            descending=True,
            limit=1
        )
        ordered_users = await repo.get_all(options_ord)
        assert len(ordered_users) == 1
        assert ordered_users[0].email == "user2@tasker.com"
        
        # 7. Test update
        updated_user = await repo.update(user1.id, {"email": "updated@tasker.com", "is_active": False})
        assert updated_user is not None
        assert updated_user.email == "updated@tasker.com"
        assert updated_user.is_active is False
        
        # 8. Test delete
        deleted = await repo.delete(user2.id)
        assert deleted is True
        
        db_user2 = await repo.get(user2.id)
        assert db_user2 is None
        
        # Check size after delete
        remaining_users = await repo.get_all()
        assert len(remaining_users) == 1
