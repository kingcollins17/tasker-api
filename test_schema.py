import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT column_name, data_type, udt_name FROM information_schema.columns WHERE table_name = 'provider_availabilities';"))
        for row in res:
            print(row)

if __name__ == "__main__":
    asyncio.run(main())
