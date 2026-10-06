"""Drive a live booking through the PUBLIC webhook endpoint (like a real customer)."""
import subprocess
import sys
import time
import uuid

import httpx

BASE = "https://api.dotstark.tech"
URL = f"{BASE}/api/v1/internal/whatsapp-qr/inbound"
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
        "pushName": "Live Tester",
    }
    r = httpx.post(URL, json=body, headers=HEADERS, timeout=180)
    try:
        print(f">> POST '{text}' -> {r.status_code} {r.text[:120]}")
    except UnicodeEncodeError:
        print(f">> POST '{text}' -> {r.status_code} (non-ascii reply body)")
    return r


def show_transcript():
    cmd = (
        "docker exec booking_agent-postgres-1 psql -U postgres -d dotstark_v2 -c "
        "\"select m.role, left(m.content, 300) from chat_messages m "
        "join chat_sessions s on s.id=m.session_id "
        f"where s.channel_user_id='{FROM.lstrip('+')}' order by m.created_at desc limit 20;\""
    )
    subprocess.run(subprocess.list2cmdline(cmd.split()) if False else cmd, shell=True)


def get_otp_from_logs():
    out = subprocess.run(
        "docker logs booking_agent-backend-1 --since 120s 2>&1 | findstr WAOTPCODE | findstr 888001",
        shell=True, capture_output=True, text=True,
    )
    for line in out.stdout.splitlines():
        if "code=" in line:
            return line.split("code=")[-1].strip()
    return None


if __name__ == "__main__":
    send("Hi")
    time.sleep(2)
    send("Rahul Live")
    time.sleep(1)
    send("rahul.live@example.com")
    time.sleep(1)
    send(FROM)
    time.sleep(1)
    send("tomorrow")
    time.sleep(1)
    send("12:00 PM")
    time.sleep(1)
    send("yes, confirm")
    print("waiting for OTP to hit backend logs...")
    code = None
    for _ in range(15):
        time.sleep(2)
        code = get_otp_from_logs()
        if code:
            break
    print(f"captured OTP: {code}")
    if code:
        send(code)
    time.sleep(3)
    show_transcript()
