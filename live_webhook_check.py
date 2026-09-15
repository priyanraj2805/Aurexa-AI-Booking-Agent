import asyncio, json, os
import httpx

BASE = "http://localhost:8005"

def payload(mid):
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
                    "contacts": [{"profile": {"name": "Live Check"}, "wa_id": "919999999978"}],
                    "messages": [{
                        "from": "919999999978", "id": mid, "timestamp": "1757930000",
                        "type": "interactive",
                        "interactive": {"type": "list_reply",
                                        "list_reply": {"id": "date_20260927",
                                                       "title": "27 September"}}}]}}]}]}

async def run():
    import uuid
    mid = f"wamid.live.{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/api/v1/whatsapp/webhook", json=payload(mid))
        print("status:", r.status_code)
        print(json.dumps(r.json(), indent=1)[:800])

asyncio.run(run())
