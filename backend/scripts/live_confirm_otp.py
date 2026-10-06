"""Continue existing live session: confirm -> OTP capture -> verify."""
import subprocess
import time
import uuid

import httpx

URL = "https://api.dotstark.tech/api/v1/internal/whatsapp-qr/inbound"
KEY = "GstjfJmAObM61sJtdy8hmiTUzS46i203W5D3fPV7jVoMrmEQt_i5Y00FxwUOiy-z"
CONN = "8667fc9f-7338-4a6e-ba1b-1b4b1ebf4854"
FROM = "+919999343434"
JID = "919999343434@s.whatsapp.net"
HEADERS = {"Content-Type": "application/json", "X-Gateway-Key": KEY}


def send(text: str):
    body = {
        "connectionId": CONN,
        "messageId": f"live-{uuid.uuid4().hex[:10]}",
        "from": FROM,
        "chatJid": JID,
        "text": text,
        "timestamp": int(time.time()),
    }
    r = httpx.post(URL, json=body, headers=HEADERS, timeout=180)
    print(f">> '{text}' -> {r.status_code}")
    return r


r = send("yes, confirm")
print(r.text[:200])
print("waiting for OTP log...")
code = None
for _ in range(15):
    time.sleep(2)
    out = subprocess.run(
        "docker logs booking_agent-backend-1 --since 60s 2>&1 | findstr WAOTPCODE | findstr 343434",
        shell=True, capture_output=True, text=True,
    )
    for line in out.stdout.splitlines():
        if "code=" in line:
            code = line.split("code=")[-1].strip()
    if code:
        break
print(f"OTP: {code}")
if code:
    r = send(code)
    print(r.text[:300])
time.sleep(2)
subprocess.run(
    "docker exec booking_agent-postgres-1 psql -U postgres -d dotstark_v2 -c \"select customer_name, org_slot_start_at, created_at from bookings where org_id='9516bfcf-437f-4eda-842e-067194a64dcb' order by created_at desc limit 2;\"",
    shell=True,
)
