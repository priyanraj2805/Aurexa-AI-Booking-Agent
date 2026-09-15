import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        # Find by phone fragment 6350063338
        r=await db.execute(st("SELECT channel_user_id, state_data, missing_fields, created_at FROM chat_sessions WHERE channel_user_id LIKE '%6350063338%' ORDER BY created_at DESC LIMIT 5"))
        for row in r.fetchall():
            print(f"session phone={row[0]} missing={row[2]} state={row[1]} created={row[3]}")
            sd=row[1] or {}
            if sd.get('active_directive_id'):
                print(f"  active={sd.get('active_directive_id')}")
        r2=await db.execute(st("SELECT customer_id, type, status, title, options, created_at FROM booking_directives WHERE customer_id LIKE '%6350063338%' ORDER BY created_at DESC LIMIT 5"))
        for row in r2.fetchall():
            print(f"dir cust={row[0]} type={row[1]} status={row[2]} title={row[3]} opts={len(row[4]) if row[4] else 0} created={row[5]}")
            if row[4]:
                for o in row[4][:3]:
                    print(f"  opt {o}")
        # Also check latest whatsapp sessions overall
        r3=await db.execute(st("SELECT channel_user_id, missing_fields FROM chat_sessions WHERE channel='whatsapp' ORDER BY created_at DESC LIMIT 10"))
        print("latest whatsapp sessions:")
        for row in r3.fetchall():
            print(f"  {row[0]} missing={row[1]}")
asyncio.run(run())
