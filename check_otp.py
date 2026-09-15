import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT channel_user_id, state_data, missing_fields, otp_verified, phone_otp_verified FROM chat_sessions WHERE channel='whatsapp' ORDER BY updated_at DESC LIMIT 3"))
        for row in r.fetchall():
            print(f"phone={row[0]} missing={row[2]} otp_verified={row[3]} phone_otp={row[4]}")
            sd=row[1] or {}
            print(f"  state_data: name={sd.get('name')} email={sd.get('email')} phone={sd.get('phone')} date={sd.get('date')} time={sd.get('time')} otp={sd.get('otp')} pending_cancel={sd.get('pending_cancel')}")
            print(f"  keys={list(sd.keys())[:15]}")
        print("--- otps ---")
        r2=await db.execute(st("SELECT phone, code, verified, expires_at, created_at FROM otps ORDER BY created_at DESC LIMIT 10"))
        for row in r2.fetchall():
            print(f"otp phone={row[0]} code={row[1]} verified={row[2]} expires={row[3]} created={row[4]}")
        print("--- directives recent ---")
        r3=await db.execute(st("SELECT customer_id, type, status, selected_option, created_at FROM booking_directives ORDER BY created_at DESC LIMIT 10"))
        for row in r3.fetchall():
            print(f"dir {row[0]} {row[1]} {row[2]} sel={row[3]}")
        print("--- bookings recent ---")
        r4=await db.execute(st("SELECT customer_email, status, created_at FROM bookings ORDER BY created_at DESC LIMIT 5"))
        for row in r4.fetchall():
            print(f"booking {row[0]} {row[1]} {row[2]}")
asyncio.run(run())
