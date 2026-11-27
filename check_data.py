import asyncpg
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "app"))
from config import get_settings


async def main():
    settings = get_settings()
    conn = await asyncpg.connect(settings.supabase_database_url)

    # Check kyc_records
    rows = await conn.fetch('SELECT user_id FROM kyc_records')
    print("=== kyc_records ===")
    for r in rows:
        print(r)

    # Check auth_otp_challenges
    rows2 = await conn.fetch('SELECT id, email FROM auth_otp_challenges')
    print("\n=== auth_otp_challenges ===")
    for r in rows2:
        print(r)

    # Check app_users
    rows3 = await conn.fetch('SELECT id, external_subject FROM app_users')
    print("\n=== app_users ===")
    for r in rows3:
        print(r)

    # Check risk_states
    rows4 = await conn.fetch('SELECT id, user_id FROM risk_states')
    print("\n=== risk_states ===")
    for r in rows4:
        print(r)

    # Check broker_vaults
    rows5 = await conn.fetch('SELECT id, user_id FROM broker_vaults')
    print("\n=== broker_vaults ===")
    for r in rows5:
        print(r)

    # Check paper_positions
    rows6 = await conn.fetch('SELECT id, user_id FROM paper_positions')
    print("\n=== paper_positions ===")
    for r in rows6:
        print(r)

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())