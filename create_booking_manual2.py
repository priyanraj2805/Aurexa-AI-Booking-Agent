import asyncio, uuid
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT id, org_id, state_data FROM chat_sessions WHERE channel_user_id='144367272632357'"))
        row=r.fetchone()
        sid, org_id, sd = row[0], row[1], row[2]
        print(f"session {sid}")
        r2=await db.execute(st("SELECT id FROM time_slots WHERE org_id=:org AND start_time::date='2026-09-19' AND start_time::time='05:30:00'"), {"org": str(org_id)})
        slot=r2.fetchone()
        slot_id=slot[0]
        r3=await db.execute(st("SELECT is_booked FROM time_slots WHERE id=:sid"), {"sid": str(slot_id)})
        print(f"slot booked? {r3.fetchone()}")
        r4=await db.execute(st("SELECT id FROM bookings WHERE slot_id=:sid"), {"sid": str(slot_id)})
        if r4.fetchone():
            print("already booked")
            return
        booking_id=uuid.uuid4()
        await db.execute(st("UPDATE time_slots SET is_booked=true WHERE id=:sid"), {"sid": str(slot_id)})
        await db.execute(st("INSERT INTO bookings (id, org_id, session_id, slot_id, customer_name, customer_email, customer_phone) VALUES (:id, :org, :sess, :slot, :name, :email, :phone)"), {"id": str(booking_id), "org": str(org_id), "sess": str(sid), "slot": str(slot_id), "name": sd.get('name'), "email": sd.get('email'), "phone": sd.get('phone')})
        await db.commit()
        print(f"created {booking_id}")
asyncio.run(run())
