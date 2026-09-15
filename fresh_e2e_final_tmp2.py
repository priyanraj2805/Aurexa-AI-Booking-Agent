import asyncio, os, httpx, uuid, time
PHONE='919999999978'
CONN='8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854'
async def run():
    key=os.getenv('WA_GATEWAY_SERVICE_KEY','')
    base='http://localhost:8005/api/v1/internal/whatsapp-qr/inbound'
    jid=f'{PHONE}@s.whatsapp.net'
    from app.db.database import AsyncSessionLocal
    from sqlalchemy import text as st
    # Clean
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id=:p"), {"p": PHONE})
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id=:p"), {"p": PHONE})
        # Also clean any stale bookings for this test email to ensure count=1 at end
        await db.execute(st("DELETE FROM bookings WHERE customer_email='directive2@test.com'"))
        await db.commit()
        print(f"Cleaned {PHONE}")
        # Verify clean
        res=await db.execute(st("SELECT count(*) FROM chat_sessions WHERE channel_user_id=:p"), {"p": PHONE})
        print(f"Sessions after clean: {res.scalar()}")
        res2=await db.execute(st("SELECT count(*) FROM booking_directives WHERE customer_id=:p"), {"p": PHONE})
        print(f"Directives after clean: {res2.scalar()}")
        res3=await db.execute(st("SELECT count(*) FROM bookings WHERE customer_email='directive2@test.com'"))
        print(f"Bookings after clean for directive2@test.com: {res3.scalar()}")

    async def send(text, optionId=None):
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

    print("\n=== STEP 1-4: Hi, Name, Email, Phone ===")
    await send('Hi, I want to book.')
    await send('Directive Test User')
    await send('directive2@test.com')
    r_phone = await send('9876543219')
    
    print("\n=== BEFORE DATE TAP: Verify SELECT_DATE ===")
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT id, session_id, customer_id, type, status, options, meta, expires_at, created_at FROM booking_directives WHERE customer_id=:p ORDER BY created_at DESC LIMIT 3"), {"p": PHONE})
        rows=res.fetchall()
        for row in rows:
            print(f"Directive {str(row[0])[:8]} type={row[4]} status={row[5]} cust={row[3]} sess={str(row[1])[:8]} opts_count={len(row[6]) if row[6] else 0}")
            if row[4]=='SELECT_DATE' and row[5]=='PENDING':
                for o in row[6][:5]:
                    print(f"  OPT id={o.get('id')} label={o.get('label')} value={o.get('value')} id_ok={o.get('id')=='date_20260927'} value_ok={o.get('value')=='2026-09-27'}")
                # Check that date_20260927 exists
                has_target = any(o.get('id')=='date_20260927' and o.get('value')=='2026-09-27' for o in row[6])
                print(f"  HAS date_20260927 with value 2026-09-27: {has_target}")
        # Check ChatSession
        res2=await db.execute(st("SELECT id, state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id=:p ORDER BY updated_at DESC LIMIT 1"), {"p": PHONE})
        srow=res2.fetchone()
        if srow:
            sd=srow[1] or {}
            print(f"Session {str(srow[0])[:8]} active_directive_id={str(sd.get('active_directive_id'))[:8] if sd.get('active_directive_id') else None} missing={srow[2]} state_data_keys={list(sd.keys())[:8] if isinstance(sd, dict) else 'not dict'}")
            # Verify active_directive_id points to SELECT_DATE
            active = sd.get('active_directive_id')
            if active:
                res3=await db.execute(st("SELECT type, status FROM booking_directives WHERE id=:id"), {"id": active})
                arow=res3.fetchone()
                print(f"Active directive {str(active)[:8]} type={arow[0] if arow else None} status={arow[1] if arow else None} should be SELECT_DATE PENDING: ok={arow and arow[0]=='SELECT_DATE' and arow[1]=='PENDING'}")
        # Also capture outgoing payload - the last reply after phone should have been the SELECT_DATE list
        # We already printed it above as r_phone, but let's check its type
        print(f"Last reply after phone was type={r_phone.get('type')} should be interactive_list from WhatsAppDirectiveRenderer: ok={r_phone.get('type')=='interactive_list'}")
        # Verify the list contains date_20260927
        if r_phone.get('type')=='interactive_list':
            rows=r_phone.get('sections',[{}])[0].get('rows',[])
            has_row = any(r.get('id')=='date_20260927' for r in rows)
            print(f"Outgoing WhatsApp list contains rowId date_20260927: {has_row} (must be True)")

    # Find the target option to tap
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT id, options FROM booking_directives WHERE customer_id=:p AND type='SELECT_DATE' ORDER BY created_at DESC LIMIT 1"), {"p": PHONE})
        row=res.fetchone()
        if not row:
            print("ERROR: No SELECT_DATE directive found before tap!")
            return
        did, opts = row[0], row[1]
        target=None
        for o in opts:
            if o.get('id')=='date_20260927':
                target=o
                break
        if not target:
            print(f"ERROR: date_20260927 not in options: {[o.get('id') for o in opts[:5]]}")
            return
        print(f"\n--- Tapping 27 September with optionId={target.get('id')} label={target.get('label')} value={target.get('value')} ---")
        r_tap = await send(target.get('label'), optionId=target.get('id'))
        print(f"After date tap: type={r_tap.get('type')} body={str(r_tap.get('body',''))[:200]}")
        # Verify SELECT_DATE lifecycle
        res2=await db.execute(st("SELECT type, status, selected_option, completed_at FROM booking_directives WHERE id=:id"), {"id": did})
        drow=res2.fetchone()
        print(f"SELECT_DATE after tap: type={drow[0]} status={drow[1]} selected={drow[2]} completed_at={drow[3]}")
        print(f"  Expected: status COMPLETED, selected_option id should be date_20260927: match={drow[2] and drow[2].get('id')=='date_20260927'}")
        # Check new SELECT_SLOT
        res3=await db.execute(st("SELECT id, type, status, options FROM booking_directives WHERE customer_id=:p AND type='SELECT_SLOT' ORDER BY created_at DESC LIMIT 1"), {"p": PHONE})
        slot_row=res3.fetchone()
        if slot_row:
            print(f"New SELECT_SLOT: id={str(slot_row[0])[:8]} type={slot_row[1]} status={slot_row[2]} opts_count={len(slot_row[3]) if slot_row[3] else 0}")
            # Check session active
            res4=await db.execute(st("SELECT state_data FROM chat_sessions WHERE channel_user_id=:p ORDER BY updated_at DESC LIMIT 1"), {"p": PHONE})
            srow2=res4.fetchone()
            if srow2 and srow2[0]:
                active=srow2[0].get('active_directive_id')
                print(f"Session active_directive_id={str(active)[:8] if active else None} should be {str(slot_row[0])[:8]}: match={str(active)==str(slot_row[0])}")
            # Verify renderer - the reply after date tap should be SELECT_SLOT list
            print(f"Next WhatsApp should be SELECT_SLOT list with stable slot IDs, has list? {r_tap.get('type')=='interactive_list'}")
            if r_tap.get('type')=='interactive_list':
                rows2=r_tap.get('sections',[{}])[0].get('rows',[])
                print(f"  Slot rows sample: {[(x.get('id'), x.get('title')) for x in rows2[:3]]}")
                # Find 4pm slot
                target_slot=None
                for o in slot_row[3]:
                    if '4:00' in o.get('label','') or '1600' in o.get('id',''):
                        target_slot=o
                        break
                if not target_slot and slot_row[3]:
                    target_slot=slot_row[3][0]
                if target_slot:
                    print(f"Tapping slot: {target_slot}")
                    r_slot=await send(target_slot.get('label'), optionId=target_slot.get('id'))
                    print(f"After slot tap: type={r_slot.get('type')} body={str(r_slot.get('body',''))[:200]}")
                    res5=await db.execute(st("SELECT status, selected_option FROM booking_directives WHERE id=:id"), {"id": slot_row[0]})
                    srow3=res5.fetchone()
                    print(f"SELECT_SLOT after tap: status={srow3[0]} selected={srow3[1]}")
                    # Check CONFIRM
                    res6=await db.execute(st("SELECT id, type, status FROM booking_directives WHERE customer_id=:p AND type='CONFIRM_BOOKING' ORDER BY created_at DESC LIMIT 1"), {"p": PHONE})
                    conf_row=res6.fetchone()
                    print(f"CONFIRM directive: id={str(conf_row[0])[:8] if conf_row else None} type={conf_row[1] if conf_row else None} status={conf_row[2] if conf_row else None}")
                    if conf_row:
                        r_conf=await send('CONFIRM', optionId='confirm_booking')
                        print(f"After CONFIRM tap: type={r_conf.get('type')} body={str(r_conf.get('body',''))[:300]}")
                        res7=await db.execute(st("SELECT status, selected_option FROM booking_directives WHERE id=:id"), {"id": conf_row[0]})
                        print(f"CONFIRM after tap: {res7.fetchone()}")
                        res8=await db.execute(st("SELECT id, customer_name, customer_email, created_at FROM bookings WHERE customer_email='directive2@test.com' ORDER BY created_at DESC LIMIT 2"))
                        for b in res8.fetchall():
                            print(f"BOOKING {b}")
                        res9=await db.execute(st("SELECT count(*) FROM bookings WHERE customer_email='directive2@test.com'"))
                        print(f"Booking count for this email: {res9.scalar()} (should be 1)")
                        res10=await db.execute(st("SELECT type, status, selected_option, created_at, completed_at FROM booking_directives WHERE customer_id=:p ORDER BY created_at"), {"p": PHONE})
                        print("\n=== FINAL LIFECYCLE ===")
                        for r in res10.fetchall():
                            print(f"{r[0]:15} {r[1]:10} sel={str(r[2])[:50] if r[2] else None} created={str(r[3])[:19]} completed={str(r[4])[:19] if r[4] else None}")
        else:
            print("ERROR: No SELECT_SLOT found after date tap")

import asyncio
asyncio.run(run())
