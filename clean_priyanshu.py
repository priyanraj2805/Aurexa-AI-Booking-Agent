import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        # Delete Priyanshu session and its directives
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='144367272632357'"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel_user_id='144367272632357'"))
        await db.execute(st("DELETE FROM otp_records WHERE session_id IN (SELECT id FROM chat_sessions WHERE channel_user_id='144367272632357')"))
        await db.commit()
        print("Cleaned Priyanshu session 144367272632357")
        # Also clean any other pending with same phone fragment
        await db.execute(st("DELETE FROM otp_records WHERE session_id IN (SELECT id FROM chat_sessions WHERE channel='whatsapp' AND state_data->>'phone' LIKE '%6350063338%')"))
        await db.commit()
        print("Done")
asyncio.run(run())
