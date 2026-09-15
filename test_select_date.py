import asyncio, os, httpx, uuid, time
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

PHONE='919999999978'
CONN='8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854'

async def setup_session():
    """Pre-fill a session with name, email, phone so we can test SELECT_DATE directly."""
    key=os.getenv('WA_GATEWAY_SERVICE_KEY','')
    base='http://localhost:8005/api/v1/internal/whatsapp-qr/inbound'
    jid=f'{PHONE}@s.whatsapp.net'
    
    async with AsyncSessionLocal() as db:
        # Clean
        await db.execute(st("DELETE FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id=:p"), {"p": PHONE})
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id=:p"), {"p": PHONE})
        await db.execute(st("DELETE FROM bookings WHERE customer_email='directive2@test.com'"))
        await db.commit()
        print("Cleaned")
        
        # Get org
        from app.models.organization import Organization
        org_res = await db.execute(st("SELECT id FROM organizations LIMIT 1"))
        org_id = org_res.scalar()
        
        # Create session with pre-filled collected_data
        from app.models.chat_session import ChatSession
        import uuid as uuid_lib
        session_id = uuid_lib.uuid4()
        session = ChatSession(
            id=session_id,
            org_id=org_id,
            channel='whatsapp',
            channel_user_id=PHONE,
            collected_data={
                'name': 'Directive Test User',
                'email': 'directive2@test.com',
                'phone': '9876543219',
                'field_definitions_snapshot': []  # will be filled by engine
            },
            missing_fields=['date', 'time'],
            state='ACTIVE'
        )
        db.add(session)
        await db.commit()
        print(f"Created session {session_id} with pre-filled data")
        return str(session_id)

async def send(text, optionId=None):
    key=os.getenv('WA_GATEWAY_SERVICE_KEY','')
    base='http://localhost:8005/api/v1/internal/whatsapp-qr/inbound'
    jid=f'{PHONE}@s.whatsapp.net'
    payload={'connectionId':CONN,'messageId':str(uuid.uuid4()),'from':PHONE,'chatJid':jid,'text':text,'timestamp':int(time.time())}
    if optionId:
        payload['optionId']=optionId
    async with httpx.AsyncClient(timeout=60) as c:
        r=await c.post(base, headers={'Content-Type':'application/json','X-Gateway-Key':key}, json=payload)
        j=r.json()
        reply=j.get('reply',{})
        body=str(reply.get('body',''))
        typ=reply.get('type')
        print(f"\n>>> SENT '{text}' opt={optionId} -> {typ} body={body[:200].replace(chr(10),' ')}")
        if typ=='interactive_list':
            rows=reply.get('sections',[{}])[0].get('rows',[])
            print(f"    LIST ids {[x.get('id') for x in rows[:3]]} labels {[x.get('title') for x in rows[:3]]}")
        elif typ=='interactive_buttons':
            print(f"    BUTTONS {[ (b.get('id'), b.get('title')) for b in reply.get('buttons',[]) ]}")
        return reply

async def run():
    session_id = await setup_session()
    
    print("\n=== Sending 'Hi' to trigger REQUIRE_DATE_PICKER ===")
    r = await send('Hi, I want to book.')
    
    print("\n=== BEFORE DATE TAP: Verify SELECT_DATE ===")
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT id, session_id, customer_id, type, status, options, meta, expires_at, created_at FROM booking_directives WHERE customer_id=:p ORDER BY created_at DESC LIMIT 3"), {"p": PHONE})
        rows=res.fetchall()
        for row in rows:
            print(f"Directive {str(row[0])[:8]} type={row[3]} status={row[4]} cust={row[2]} sess={str(row[1])[:8]} opts_count={len(row[5]) if row[5] else 0}")
            if row[3]=='SELECT_DATE' and row[4]=='PENDING':
                for o in row[5][:5]:
                    print(f"  OPT id={o.get('id')} label={o.get('label')} value={o.get('value')}")
                has_target = any(o.get('id')=='date_20260914' and o.get('value')=='2026-09-14' for o in row[5])
                print(f"  HAS date_20260914 with value 2026-09-14: {has_target}")
        
        res2=await db.execute(st("SELECT id, state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id=:p ORDER BY updated_at DESC LIMIT 1"), {"p": PHONE})
        srow=res2.fetchone()
        if srow:
            sd=srow[1] or {}
            print(f"Session {str(srow[0])[:8]} active_directive_id={str(sd.get('active_directive_id'))[:8] if sd.get('active_directive_id') else None} missing={srow[2]}")
            active = sd.get('active_directive_id')
            if active:
                res3=await db.execute(st("SELECT type, status FROM booking_directives WHERE id=:id"), {"id": active})
                arow=res3.fetchone()
                print(f"Active directive {str(active)[:8]} type={arow[0] if arow else None} status={arow[1] if arow else None}")
        
        print(f"Last reply type={r.get('type')} should be interactive_list: ok={r.get('type')=='interactive_list'}")
        if r.get('type')=='interactive_list':
            rows=r.get('sections',[{}])[0].get('rows',[])
            has_row = any(r.get('id')=='date_20260914' for r in rows)
            print(f"Outgoing WhatsApp list contains rowId date_20260914: {has_row}")
            # Pick first date to tap
            target = rows[0]
            print(f"\n--- Tapping {target.get('title')} with optionId={target.get('id')} ---")
            r_tap = await send(target.get('title'), optionId=target.get('id'))
            print(f"After date tap: type={r_tap.get('type')} body={str(r_tap.get('body',''))[:200]}")
            
            # Verify SELECT_DATE lifecycle
            res2=await db.execute(st("SELECT type, status, selected_option, completed_at FROM booking_directives WHERE id=:id"), {"id": active})
            drow=res2.fetchone()
            print(f"SELECT_DATE after tap: type={drow[0]} status={drow[1]} selected={drow[2]} completed_at={drow[3]}")
            
            # Check new SELECT_SLOT
            res3=await db.execute(st("SELECT id, type, status, options FROM booking_directives WHERE customer_id=:p AND type='SELECT_SLOT' ORDER BY created_at DESC LIMIT 1"), {"p": PHONE})
            slot_row=res3.fetchone()
            if slot_row:
                print(f"New SELECT_SLOT: id={str(slot_row[0])[:8]} type={slot_row[1]} status={slot_row[2]} opts_count={len(slot_row[3]) if slot_row[3] else 0}")
                if slot_row[3]:
                    for o in slot_row[3][:5]:
                        print(f"  SLOT id={o.get('id')} label={o.get('label')} value={o.get('value')}")
                    # Tap first slot
                    target_slot = slot_row[3][0]
                    print(f"\n--- Tapping slot {target_slot.get('label')} ---")
                    r_slot = await send(target_slot.get('label'), optionId=target_slot.get('id'))
                    print(f"After slot tap: type={r_slot.get('type')} body={str(r_slot.get('body',''))[:200]}")

import asyncio
asyncio.run(run())