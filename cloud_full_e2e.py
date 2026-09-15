import asyncio, json, uuid
import httpx
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st

BASE = "http://localhost:8005"
PHONE = "919999999973"
PNID = "1288066011058334"

def meta_text(mid, body):
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "4439243576313653", "changes": [{
            "field": "messages",
            "value": {"messaging_product": "whatsapp",
                      "metadata": {"display_phone_number": "919521433306",
                                    "phone_number_id": PNID},
                      "contacts": [],
                      "messages": [{"from": PHONE, "id": mid, "timestamp": "1757930000",
                                    "type": "text", "text": {"body": body}}]}}]}]}

def meta_reply(mid, oid, title):
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "4439243576313653", "changes": [{
            "field": "messages",
            "value": {"messaging_product": "whatsapp",
                      "metadata": {"display_phone_number": "919521433306",
                                    "phone_number_id": PNID},
                      "contacts": [],
                      "messages": [{"from": PHONE, "id": mid, "timestamp": "1757930000",
                                    "type": "interactive",
                                    "interactive": {"type": "list_reply",
                                                    "list_reply": {"id": oid, "title": title}}}]}}]}]}

async def post(payload):
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{BASE}/api/v1/whatsapp/webhook", json=payload)
        return r.status_code, r.json()

async def dbstate(label):
    async with AsyncSessionLocal() as db:
        s = await db.execute(st(
            "SELECT missing_fields, state_data FROM chat_sessions "
            "WHERE channel='whatsapp_cloud' AND channel_user_id='919999999973' "
            "ORDER BY updated_at DESC LIMIT 1"))
        row = s.fetchone()
        missing = row[0] if row else None
        sd = row[1] or {} if row else {}
        got = {k: sd.get(k) for k in ("name", "email", "phone", "date", "time")}
        d = await db.execute(st(
            "SELECT type, status FROM booking_directives "
            "WHERE customer_id='919999999973' ORDER BY created_at"))
        dirs = [(x[0], x[1]) for x in d.fetchall()]
        print(f"[{label}] missing={missing} collected={got}")
        print(f"[{label}] directives={dirs}")
        return missing, sd, dirs

async def active_options(dtype):
    async with AsyncSessionLocal() as db:
        r = await db.execute(st(
            "SELECT id, options FROM booking_directives WHERE customer_id='919999999973' "
            f"AND type='{dtype}' AND status='PENDING' ORDER BY created_at DESC LIMIT 1"))
        row = r.fetchone()
        return (row[0], row[1]) if row else (None, None)

async def run():
    async with AsyncSessionLocal() as db:
        await db.execute(st("DELETE FROM booking_directives WHERE customer_id='919999999973'"))
        await db.execute(st("DELETE FROM chat_sessions WHERE channel='whatsapp_cloud' AND channel_user_id='919999999973'"))
        await db.commit()
    print("cleaned, starting Cloud E2E")

    async def step(label, payload):
        sc, body = await post(payload)
        ok = sc == 200 and body.get("processed") == 1
        print(f"STEP {label}: http={sc} processed={body.get('processed')} events={len(body.get('events', []))} -> {'OK' if ok else 'FAIL'}")
        return ok

    results = {}
    results["1-hi"] = await step("1-hi", meta_text(f"wamid.t.{uuid.uuid4().hex[:8]}", "Hi"))
    await dbstate("after-hi")
    results["2-name"] = await step("2-name", meta_text(f"wamid.t.{uuid.uuid4().hex[:8]}", "Aarav Sharma"))
    results["3-email"] = await step("3-email", meta_text(f"wamid.t.{uuid.uuid4().hex[:8]}", "cloudtest@example.com"))
    results["4-phone"] = await step("4-phone", meta_text(f"wamid.t.{uuid.uuid4().hex[:8]}", "9876543210"))
    missing, sd, dirs = await dbstate("after-phone")
    results["4-date-directive"] = any(t == "SELECT_DATE" and s == "PENDING" for t, s in dirs)

    _, opts = await active_options("SELECT_DATE")
    # tap tomorrow (index 1) — today is same-day blocked by policy
    pick = opts[1] if opts and len(opts) > 1 else None
    if pick:
        print(f"tapping date {pick}")
        results["5-date-tap"] = await step("5-date-tap", meta_reply(
            f"wamid.t.{uuid.uuid4().hex[:8]}", pick["id"], pick["label"]))
    else:
        results["5-date-tap"] = False
        print("NO DATE OPTIONS - FAIL")
    missing, sd, dirs = await dbstate("after-date")
    results["5-slot-directive"] = any(t == "SELECT_SLOT" and s == "PENDING" for t, s in dirs)
    results["5-date-completed"] = any(t == "SELECT_DATE" and s == "COMPLETED" for t, s in dirs)

    _, sopts = await active_options("SELECT_SLOT")
    if sopts:
        spick = sopts[0]
        print(f"tapping slot {spick}")
        results["6-slot-tap"] = await step("6-slot-tap", meta_reply(
            f"wamid.t.{uuid.uuid4().hex[:8]}", spick["id"], spick["label"]))
    else:
        results["6-slot-tap"] = False
        print("NO SLOT OPTIONS - FAIL")
    missing, sd, dirs = await dbstate("after-slot")
    results["6-confirm-directive"] = any(t == "CONFIRM_BOOKING" and s == "PENDING" for t, s in dirs)
    results["6-slot-completed"] = any(t == "SELECT_SLOT" and s == "COMPLETED" for t, s in dirs)

    _, copts = await active_options("CONFIRM_BOOKING")
    if copts:
        cpick = [o for o in copts if "confirm" in o["id"]][0]
        print(f"tapping {cpick}")
        results["7-confirm-tap"] = await step("7-confirm-tap", meta_reply(
            f"wamid.t.{uuid.uuid4().hex[:8]}", cpick["id"], cpick["label"]))
    else:
        results["7-confirm-tap"] = False
    missing, sd, dirs = await dbstate("after-confirm")
    async with AsyncSessionLocal() as db:
        r = await db.execute(st(
            "SELECT count(*) FROM otp_records WHERE session_id IN "
            "(SELECT id FROM chat_sessions WHERE channel_user_id='919999999973')"))
        n_otp = r.scalar()
        results["7-otp-dispatched"] = (n_otp or 0) > 0
        print(f"otp_records for test session: {n_otp}")

    print("\n=== RESULTS ===")
    allok = True
    for k, v in results.items():
        print(f"{k}: {'PASS' if v else 'FAIL'}")
        allok = allok and bool(v)
    print("OVERALL:", "ALL PASS" if allok else "FAILURES PRESENT")

asyncio.run(run())
