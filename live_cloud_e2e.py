import asyncio, json
import httpx

BASE = "http://localhost:8005"
PHONE = "919999999972"

def payload(mid, body="Hi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "4439243576313653",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "919521433306",
                                  "phone_number_id": "1288066011058334"},
                    "contacts": [],
                    "messages": [{
                        "from": PHONE, "id": mid, "timestamp": "1757930000",
                        "type": "text", "text": {"body": body}}]}}]}]}

async def run():
    import uuid
    mid = f"wamid.e2e.{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{BASE}/api/v1/whatsapp/webhook", json=payload(mid))
        print("status:", r.status_code)
        d = r.json()
        print("processed:", d.get("processed"), "events:", len(d.get("events", [])))

asyncio.run(run())
