import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE task_dispatch_attempts ADD COLUMN dispatch_session_id VARCHAR REFERENCES dispatch_sessions(id) ON DELETE CASCADE;"))
            print("Column added successfully!")
        except Exception as e:
            print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
