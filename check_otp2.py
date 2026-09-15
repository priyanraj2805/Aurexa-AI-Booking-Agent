import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT id, channel_user_id, otp_verified, phone_otp_verified, state_data FROM chat_sessions WHERE channel_user_id='144367272632357' ORDER BY updated_at DESC LIMIT 1"))
        row=r.fetchone()
        if row:
            print(f"id={row[0]} phone={row[1]} otp_verified={row[2]} phone_otp_verified={row[3]}")
            print(f"state_data={row[4]}")
        r2=await db.execute(st("SELECT session_id, channel, code_hash, expires_at, failed_attempts, created_at FROM otp_records WHERE session_id=:sid ORDER BY created_at DESC"), {"sid": str(row[0])})
        for x in r2.fetchall():
            print(f"otp session={str(x[0])[:8]} chan={x[1]} hash={x[2][:10]}... expires={x[3]} failed={x[4]} created={x[5]}")
        r3=await db.execute(st("SELECT id, customer_email, status, start_time, end_time FROM bookings WHERE customer_email='priyanthakur3020@gmail.com' ORDER BY created_at DESC LIMIT 5"))
        for x in r3.fetchall():
            print(f"booking {x[0]} email={x[1]} status={x[2]} start={x[3]} end={x[4]}")
asyncio.run(run())
