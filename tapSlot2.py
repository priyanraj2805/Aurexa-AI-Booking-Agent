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
        print(f"reply type={rp.get('type')} body={str(rp.get('body',''))[:250]}")
        if rp.get('type')=='interactive_buttons':
            print([(b.get('id'),b.get('title')) for b in rp.get('buttons',[])])
        if rp.get('type')=='interactive_list':
            print([(x.get('id'),x.get('title')) for x in rp.get('sections',[{}])[0].get('rows',[])[:3]])
        return rp
async def run():
    r=await send('9:00 AM','slot_20260915_900AM')
    print('after slot tap')
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT id,type,status,selected_option FROM booking_directives WHERE customer_id='919999999978' ORDER BY created_at DESC LIMIT 5"))
        for x in res.fetchall(): print(f"dir {str(x[0])[:8]} {x[1]} {x[2]} sel={x[3]}")
        res2=await db.execute(st("SELECT state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id='919999999978'"))
        row=res2.fetchone()
        print(f"missing {row[1]} collected date/time { {k:row[0].get(k) for k in ['date','time'] if row[0]} } active {(row[0] or {}).get('active_directive_id')}")
        res3=await db.execute(st("SELECT id,type,status,options FROM booking_directives WHERE customer_id='919999999978' AND type='CONFIRM_BOOKING' ORDER BY created_at DESC LIMIT 1"))
        cr=res3.fetchone()
        print(f"CONFIRM dir {cr[0] if cr else None} {cr[1] if cr else None} {cr[2] if cr else None} opts={cr[3] if cr else None}")
    if cr and cr[2]=='PENDING':
        r2=await send('CONFIRM','confirm_booking')
        print('after confirm')
        async with AsyncSessionLocal() as db2:
            res4=await db2.execute(st("SELECT id,status FROM bookings WHERE customer_email='directive2@test.com' ORDER BY created_at DESC LIMIT 2"))
            for b in res4.fetchall(): print(f"booking {b[0]} status={b[1]}")
            res5=await db2.execute(st("SELECT type,status,selected_option FROM booking_directives WHERE customer_id='919999999978' ORDER BY created_at"))
            for x in res5.fetchall(): print(f"final {x[0]:15} {x[1]:10} {x[2]}")
asyncio.run(run())
