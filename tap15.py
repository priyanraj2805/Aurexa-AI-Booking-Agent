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
        j=r.json()
        rp=j.get('reply',{})
        print(f"reply type={rp.get('type')} body={str(rp.get('body',''))[:250]}")
        if rp.get('type')=='interactive_list':
            print([(x.get('id'),x.get('title')) for x in rp.get('sections',[{}])[0].get('rows',[])[:4]])
        if rp.get('type')=='interactive_buttons':
            print([(b.get('id'),b.get('title')) for b in rp.get('buttons',[])])
        return rp
async def run():
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id='919999999978'"))
        row=res.fetchone()
        print('before active', (row[0] or {}).get('active_directive_id'), 'missing', row[1])
        res2=await db.execute(st("SELECT id,type,status FROM booking_directives WHERE customer_id='919999999978' ORDER BY created_at DESC LIMIT 5"))
        for x in res2.fetchall(): print('dir',x)
    # Since same-day blocked, the session is now stuck with active COMPLETED old id. Need to clear active and retry.
    # The engine should have created a new SELECT_DATE for tomorrow after the BUSINESS HOURS text. Let's just send '15 September' as text
    # First try tapping date_20260915 - but old directive is COMPLETED, so this will be handled as new message, not tap
    # Let's send a free text date
    r=await send('2026-09-15')
    print('after 2026-09-15 free text')
    async with AsyncSessionLocal() as db:
        res=await db.execute(st("SELECT id,type,status,options,selected_option FROM booking_directives WHERE customer_id='919999999978' ORDER BY created_at DESC LIMIT 5"))
        for x in res.fetchall(): 
            print(f"dir id={str(x[0])[:8]} type={x[1]} status={x[2]} opts={len(x[3]) if x[3] else 0} sel={x[4]}")
        res2=await db.execute(st("SELECT state_data, missing_fields FROM chat_sessions WHERE channel='whatsapp' AND channel_user_id='919999999978'"))
        row=res2.fetchone()
        print('after missing', row[1], 'active', (row[0] or {}).get('active_directive_id'), 'collected date', (row[0] or {}).get('date'))
asyncio.run(run())
