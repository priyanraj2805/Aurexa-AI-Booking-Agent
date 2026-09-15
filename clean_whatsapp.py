import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        # Show before
        r1=await db.execute(st("SELECT channel, channel_user_id, state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp'"))
        rows=r1.fetchall()
        print(f"Before: {len(rows)} whatsapp sessions")
        for r in rows[:10]:
            print(f"  {r[1]} state_keys={list((r[2] or {}).keys())[:4]} missing={r[3]}")
        r2=await db.execute(st("SELECT count(*) FROM booking_directives"))
        print(f"Directives before: {r2.scalar()}")
        r3=await db.execute(st("SELECT count(*) FROM bookings"))
        print(f"Bookings before: {r3.scalar()}")
        # Clean directives first (FK)
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id IN (SELECT channel_user_id FROM chat_sessions WHERE channel='whatsapp')"))
        # Also clean directives for test phone that may have no session left
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id LIKE '919%'"))
        # Clean sessions
        await db.execute(st("DELETE FROM chat_sessions WHERE channel='whatsapp'"))
        await db.commit()
        print("Deleted all whatsapp chat_sessions + related directives")
        r4=await db.execute(st("SELECT count(*) FROM chat_sessions WHERE channel='whatsapp'"))
        print(f"After sessions: {r4.scalar()}")
        r5=await db.execute(st("SELECT count(*) FROM booking_directives"))
        print(f"After directives: {r5.scalar()}")
        # Keep bookings (real data) - but show
        r6=await db.execute(st("SELECT count(*) FROM bookings"))
        print(f"Bookings kept: {r6.scalar()} (not deleted - tell me if you want bookings wiped too)")
asyncio.run(run())
