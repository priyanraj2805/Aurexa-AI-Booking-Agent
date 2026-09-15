import asyncio, uuid
from datetime import datetime, timezone
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
async def run():
    async with AsyncSessionLocal() as db:
        # Get session and org
        r=await db.execute(st("SELECT id, org_id, state_data FROM chat_sessions WHERE channel_user_id='144367272632357'"))
        row=r.fetchone()
        sid, org_id, sd = row[0], row[1], row[2]
        print(f"session {sid} org {org_id} sd {sd}")
        # Find slot for 2026-09-19 11:00 AM (05:30 UTC)
        r2=await db.execute(st("SELECT id, start_time, end_time FROM time_slots WHERE org_id=:org AND start_time::date='2026-09-19' AND start_time::time='05:30:00'"), {"org": str(org_id)})
        slot=r2.fetchone()
        print(f"slot {slot}")
        if not slot:
            r2=await db.execute(st("SELECT id, start_time FROM time_slots WHERE org_id=:org AND start_time::date='2026-09-19' ORDER BY start_time"))
            for x in r2.fetchall(): print(x)
            return
        slot_id=slot[0]
        # Check if already booked
        r3=await db.execute(st("SELECT id, is_booked FROM time_slots WHERE id=:sid"), {"sid": str(slot_id)})
        print(r3.fetchone())
        # Create booking if not exists
        r4=await db.execute(st("SELECT id FROM bookings WHERE slot_id=:sid"), {"sid": str(slot_id)})
        if r4.fetchone():
            print("already booked")
            return
        # Get slot start/end
        booking_id=uuid.uuid4()
        # Mark slot booked
        await db.execute(st("UPDATE time_slots SET is_booked=true WHERE id=:sid"), {"sid": str(slot_id)})
        # Insert booking
        await db.execute(st("""
            INSERT INTO bookings (id, org_id, session_id, slot_id, customer_name, customer_email, customer_phone, created_at, updated_at)
            VALUES (:id, :org, :sess, :slot, :name, :email, :phone, NOW(), NOW())
        """), {"id": str(booking_id), "org": str(org_id), "sess": str(sid), "slot": str(slot_id), "name": sd.get('name'), "email": sd.get('email'), "phone": sd.get('phone')})
        await db.commit()
        print(f"created booking {booking_id}")
        # Mark directives as completed
        await db.execute(st("UPDATE chat_sessions SET missing_fields='[]'::jsonb WHERE id=:sid"), {"sid": str(sid)})
        await db.commit()
        print("done")
asyncio.run(run())
