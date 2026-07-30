import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT unnest(enum_range(NULL::dayofweek));"))
        for row in res:
            print(row)

if __name__ == "__main__":
    asyncio.run(main())
