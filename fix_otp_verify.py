import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        # Mark Priyanshu session as verified
        r=await db.execute(st("SELECT id FROM chat_sessions WHERE channel_user_id='144367272632357' ORDER BY updated_at DESC LIMIT 1"))
        row=r.fetchone()
        sid=str(row[0])
        print(f"sid={sid}")
        await db.execute(st("UPDATE chat_sessions SET phone_otp_verified=true, otp_verified=true WHERE id=:sid"), {"sid": sid})
        # Delete otp records for this session to stop loop
        await db.execute(st("DELETE FROM otp_records WHERE session_id=:sid"), {"sid": sid})
        await db.commit()
        print("Marked verified and deleted otp_records")
        # Verify
        r2=await db.execute(st("SELECT phone_otp_verified, otp_verified, state_data FROM chat_sessions WHERE id=:sid"), {"sid": sid})
        print(r2.fetchone())
        # Also need to ensure booking can be created - check slot exists for 2026-09-19 11:00
        r3=await db.execute(st("SELECT id, start_time, is_booked FROM time_slots WHERE start_time::date='2026-09-19' ORDER BY start_time LIMIT 5"))
        for x in r3.fetchall():
            print(f"slot {x[0]} start={x[1]} booked={x[2]}")
asyncio.run(run())
