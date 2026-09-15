import asyncio, os, httpx, uuid, time
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
PHONE='919999999978'
CONN='8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854'
key=os.getenv('WA_GATEWAY_SERVICE_KEY','')
base='http://localhost:8005/api/v1/internal/whatsapp-qr/inbound'
jid=f'{PHONE}@s.whatsapp.net'
async def send(txt, oid=None):
    p={'connectionId':CONN,'messageId':str(uuid.uuid4()),'from':PHONE,'chatJid':jid,'text':txt,'timestamp':int(time.time())}
    if oid: p['optionId']=oid
    async with httpx.AsyncClient(timeout=60) as c:
        r=await c.post(base, headers={'Content-Type':'application/json','X-Gateway-Key':key}, json=p)
        j=r.json(); rp=j.get('reply',{})
        print(f"SENT {txt} oid={oid} -> {rp.get('type')} body={str(rp.get('body',''))[:200]}")
        if rp.get('type')=='interactive_list':
            rows=rp.get('sections',[{}])[0].get('rows',[])
            print(' rows',[(x.get('id'),x.get('title')) for x in rows[:4]])
        if rp.get('type')=='interactive_buttons':
            print(' btns',[(b.get('id'),b.get('title')) for b in rp.get('buttons',[])])
        return rp
async def run():
    # tap date_20260914
    r=await send('Mon, Sep 14', 'date_20260914')
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT type,status,selected_option,completed_at FROM booking_directives WHERE customer_id='919999999978' AND type='SELECT_DATE' ORDER BY created_at DESC LIMIT 1"))
        row=res.fetchone()
        print(f"SELECT_DATE after tap: type={row[0]} status={row[1]} selected={row[2]} completed={row[3]} LLM_bypass={row[1]=='COMPLETED' and row[2].get('id')=='date_20260914'}")
        # check missing and active
        res2=await db.execute(st("SELECT state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id='919999999978'"))
        s=res2.fetchone()
        print(f"session after date tap: missing={s[1]} state_keys={list((s[0] or {}).keys())[:6]} active={ (s[0] or {}).get('active_directive_id') }")
        # SELECT_SLOT
        res3=await db.execute(st("SELECT id,type,status,options FROM booking_directives WHERE customer_id='919999999978' AND type='SELECT_SLOT' ORDER BY created_at DESC LIMIT 1"))
        slot=res3.fetchone()
        if slot:
            print(f"SELECT_SLOT: id={str(slot[0])[:8]} type={slot[1]} status={slot[2]} opts={len(slot[3]) if slot[3] else 0}")
            for o in (slot[3] or [])[:4]:
                print(f"  SLOT {o}")
            print(f"Reply type after date tap should be SELECT_SLOT interactive_list: {r.get('type')}")
            # tap first slot
            if slot[3]:
                ts=slot[3][0]
                r2=await send(ts.get('label'), ts.get('id'))
                async with AsyncSessionLocal() as db2:
                    res4=await db2.execute(st(f"SELECT status,selected_option FROM booking_directives WHERE id='{slot[0]}'"))
                    rr=res4.fetchone()
                    print(f"SELECT_SLOT after tap: status={rr[0]} selected={rr[1]}")
                    res5=await db2.execute(st("SELECT id,type,status FROM booking_directives WHERE customer_id='919999999978' AND type='CONFIRM_BOOKING' ORDER BY created_at DESC LIMIT 1"))
                    cr=res5.fetchone()
                    print(f"CONFIRM: {cr}")
        else:
            print("No SELECT_SLOT found - check logs for slot generation")
asyncio.run(run())
