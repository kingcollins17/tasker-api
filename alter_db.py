import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        for stmt in [
            "ALTER TABLE dispatch_sessions ADD COLUMN IF NOT EXISTS search_radius_km FLOAT DEFAULT 10.0;",
            "ALTER TABLE dispatch_sessions ADD COLUMN IF NOT EXISTS max_search_radius_km FLOAT DEFAULT 30.0;",
            "ALTER TABLE dispatch_sessions ADD COLUMN IF NOT EXISTS auto_expand_radius BOOLEAN DEFAULT TRUE;",
            "ALTER TABLE dispatch_sessions ADD COLUMN IF NOT EXISTS lock_version INT DEFAULT 1;",
            "ALTER TABLE dispatch_sessions DROP COLUMN IF EXISTS search_offset;",
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='dispatch_sessions' AND column_name='current_batch') THEN UPDATE dispatch_sessions SET lock_version = current_batch; ALTER TABLE dispatch_sessions DROP COLUMN current_batch; END IF; END $$;",
            "CREATE INDEX IF NOT EXISTS idx_user_locations_spatial ON user_locations USING GIST (last_known_location);",
            "CREATE INDEX IF NOT EXISTS idx_task_locations_spatial ON task_locations USING GIST (geography_point);",
            "ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CUSTOMER_PAID';",
            "ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'TRANSFER_INITIATED';",
            "ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'CASH_PAID';",
            "ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'PAYMENT_REQUESTED';",
            "ALTER TYPE payoutstatus ADD VALUE IF NOT EXISTS 'TRANSFER_INITIATED';",
            "ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'PAYMENT_REQUESTED';",
        ]:
            try:
                await conn.execute(text(stmt))
                print(f"Executed: {stmt}")
            except Exception as e:
                print(f"Error executing {stmt}: {e}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())

