import asyncio
from app.core.database import get_session
from sqlmodel import select
from app.core.models.users import UserLocation

async def main():
    async for session in get_session():
        stmt = select(UserLocation)
        result = await session.exec(stmt)
        print(result.first())
        break

asyncio.run(main())
