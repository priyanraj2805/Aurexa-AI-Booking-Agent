# 🚀 GCP Live Deploy — DotStark Booking Agent

**Commit to deploy:** `1293913` (branch `Aurexa-production`)

---

## ⚠️ Pehle ye samajh lo (sabse important)

Tumhara public URL **NAHI badlega**:

| | Abhi (laptop) | GCP ke baad |
|---|---|---|
| Tenant dashboard | `https://dotstark.tech` | **same** ✅ |
| API base | `https://api.dotstark.tech/api/v1` | **same** ✅ |
| Super admin | `https://super.dotstark.tech` | **same** ✅ |
| Cashfree webhook | `https://api.dotstark.tech/api/v1/widget/payment/cashfree/webhook` | **same** ✅ |

**Kyun?** Tum Cloudflare ka **named tunnel** use kar rahe ho (`tunnel: bf0e9445-…`).
Named tunnel ka DNS + ingress config `cloudflared/config.yml` me hai — sirf **connector**
(agent) ki jagah badalti hai, DNS same rehta hai.

Isliye: **Cashfree/Meta domain whitelisting dobara karne ki zaroorat NAHI hai**, aur
Google/Microsoft OAuth redirect URI bhi same rehne chahiye (warna login toot jayega).

**Sirf 3 cheezein migrate karni hain:**
1. **Postgres database** (48 orgs, 90 bookings, 21 departments, 302 providers)
2. **Secrets files** (`.env`, `backend/.env.docker`, `backend-super/.env.docker`, `cloudflared/credentials.json`) — ye gitignored hain, git se nahi aayenge
3. **WhatsApp session** (`booking_agent_wa_auth` volume — Baileys QR login)

---

## PHASE 0 — Laptop pe (abhi)

### 0.1 gcloud install + login
```powershell
# Install: https://cloud.google.com/sdk/docs/install  (Windows: winget install Google.CloudSDK)
gcloud init
gcloud auth list          # apna account dikhna chahiye
gcloud config set project <YOUR_PROJECT_ID>
```

### 0.2 DB dump (Windows-safe — script ke andar `docker cp` use hota hai, `>` se dump corrupt hota hai)
```powershell
cd C:\Users\priyanshuraj\OneDrive\Desktop\dotstark\booking_agent
bash scripts/db-migrate.sh dump
```
> Output: `db-dump-2026xxxx-xxxxxx.dump` (~6 MB)

### 0.3 WhatsApp session pack karo
```powershell
docker run --rm -v booking_agent_wa_auth:/data -v "${PWD}:/out" alpine tar czf /out/wa-auth.tar.gz -C /data .
```
> Output: `wa-auth.tar.gz` (~536 KB)

### 0.4 Secrets + tunnel credentials pack karo
```powershell
New-Item -ItemType Directory -Force -Path deploy-secrets | Out-Null
Copy-Item .env                       deploy-secrets/.env
Copy-Item backend/.env.docker        deploy-secrets/backend.env.docker
Copy-Item backend-super/.env.docker  deploy-secrets/backend-super.env.docker
Copy-Item cloudflared/credentials.json deploy-secrets/credentials.json
```
> `cloudflared/config.yml` **git me hai** — wo VM pe clone se aa jayega. Sirf `credentials.json` copy karna hai.

### 0.5 VM banao
```powershell
gcloud compute instances create dotstark-prod `
  --zone=asia-south1-a `
  --machine-type=e2-standard-2 `
  --boot-disk-size=60GB `
  --tags=dotstark-prod `
  --image-family=ubuntu-2204-lts `
  --image-project=ubuntu-os-cloud `
  --create-disk=auto-delete
```
> **e2-standard-2 (8GB) lo** — e2-medium pe Next.js build OOM ho jaata hai. Budget bache to `e2-standard-2` hi best hai.
> Region `asia-south1` (Mumbai) — tenants India me hain.

### 0.6 GCP firewall (ek baar)
```powershell
gcloud compute firewall-rules create allow-dotstark `
  --network=default `
  --allow=tcp:22,tcp:80,tcp:443,tcp:8080,tcp:8005,tcp:3002 `
  --source-ranges=0.0.0.0/0 `
  --target-tags=dotstark-prod
```

---

## PHASE 1 — VM pe jaake setup

### 1.1 Bootstrap (Docker + Compose + firewall + swap)
```powershell
gcloud compute scp --recurse scripts/gcp-vm-bootstrap.sh dotstark-prod:/tmp/ --zone=asia-south1-a
gcloud compute ssh dotstark-prod --zone=asia-south1-a --command="sudo bash /tmp/gcp-vm-bootstrap.sh"
```

### 1.2 Docker group activate (logout + login zaroori)
```powershell
gcloud compute ssh dotstark-prod --zone=asia-south1-a --command="newgrp docker"
```
> ya VM se logout karke wapas aao. Verify: `docker ps` bina `sudo` chale.

### 1.3 Code pull karo
```powershell
gcloud compute ssh dotstark-prod --zone=asia-south1-a
```
VM pe:
```bash
cd ~
git clone https://github.com/ManojDS17/BookingChatBot.git
cd BookingChatBot
git checkout Aurexa-production
git log --oneline -1      # 1293913 dikhna chahiye
```

### 1.4 Secrets copy karo (VM pe)
```powershell
gcloud compute scp deploy-secrets/.env                        dotstark-prod:/tmp/secrets.env        --zone=asia-south1-a
gcloud compute scp deploy-secrets/backend.env.docker          dotstark-prod:/tmp/backend.env.docker --zone=asia-south1-a
gcloud compute scp deploy-secrets/backend-super.env.docker    dotstark-prod:/tmp/backend-super.env.docker --zone=asia-south1-a
gcloud compute scp deploy-secrets/credentials.json            dotstark-prod:/tmp/credentials.json   --zone=asia-south1-a
gcloud compute scp db-dump-*.dump                             dotstark-prod:/tmp/                    --zone=asia-south1-a
gcloud compute scp wa-auth.tar.gz                             dotstark-prod:/tmp/                    --zone=asia-south1-a
```

VM pe:
```bash
cd ~/BookingChatBot
mv /tmp/secrets.env           .env
mv /tmp/backend.env.docker    backend/.env.docker
mv /tmp/backend-super.env.docker backend-super/.env.docker
mv /tmp/credentials.json      cloudflared/credentials.json
chmod 600 cloudflared/credentials.json .env backend/.env.docker backend-super/.env.docker
ls -la cloudflared/           # config.yml (git se) + credentials.json (copy) dono hone chahiye
```

### 1.5 Stale Zoom URL fix karo
`backend/.env.docker` me abhi ye hai:
```
ZOOM_REDIRECT_URI=https://pork-departure-maritime-values.trycloudflare.com/api/v1/dashboard/meeting-providers/zoom/callback
```
Wo ek purana quick-tunnel URL hai. Fix karo:
```bash
sed -i 's|^ZOOM_REDIRECT_URI=.*|ZOOM_REDIRECT_URI=https://api.dotstark.tech/api/v1/dashboard/meeting-providers/zoom/callback|' backend/.env.docker
grep ZOOM_REDIRECT_URI backend/.env.docker
```

---

## PHASE 2 — Data migrate

### 2.1 Postgres restore
VM pe:
```bash
cd ~/BookingChatBot
bash scripts/db-migrate.sh restore /tmp/db-dump-2026xxxx-xxxxxx.dump
```
> Expected output:
> ```
>   organizations        48
>   bookings             90
>   chat_sessions      1066
>   service_units        21
>   providers           302
> ```
> Numbers laptop wale se match hone chahiye. **Mismatch = ruk jao, investigate karo.**

### 2.2 WhatsApp session restore
```bash
docker compose up -d wa-gateway
docker run --rm -v booking_agent_wa_auth:/data -v /tmp:/in alpine sh -c "cd /data && tar xzf /in/wa-auth.tar.gz"
docker compose restart wa-gateway
docker compose logs --tail 40 wa-gateway
```
> **Agar QR hi maange** (session expire ho gaya) → admin panel → WhatsApp section → QR scan karo. Ye normal hai, 2 min ka kaam hai.

---

## PHASE 3 — App build + start

VM pe:
```bash
cd ~/BookingChatBot
bash scripts/gcp-deploy-prod.sh
```

Ye script karta hai:
1. Pre-flight check (docker, compose, required files)
2. `INTERNAL_BRIDGE_SECRET` backend ↔ backend-super ke beech sync
3. Images build (Next.js compile — **15–25 min** pehli baar)
4. `postgres` + `redis` start → health wait
5. Baaki sab services start → backend `/health` wait
6. **Last me** named Cloudflare tunnel start
7. Live URLs print

> ⚠️ Ye `scripts/gcp-deploy.sh` (demo wala) NAHI hai — wo `-f docker-compose.yml` se chalta hai aur **override skip karta hai → domains tod deta hai**. Hamesha `gcp-deploy-prod.sh` use karo.

---

## PHASE 4 — CUTOVER (sabse important order)

**Dono machines ek saath tunnel na chalaayein** — warna Cloudflare load-balance karega aur aadha traffic laptop pe jayega.

### 4.1 Laptop pe: local tunnel connector band karo
```powershell
cd C:\Users\priyanshuraj\OneDrive\Desktop\dotstark\booking_agent
docker compose stop cloudflared
```

### 4.2 VM pe: tunnel confirm
```bash
cd ~/BookingChatBot
docker compose logs --tail 30 cloudflared
```
> "Registered tunnel connection" lines dikhni chahiye (multiple conns = multiple replicas)

### 4.3 Verify (browser + curl)
```
https://dotstark.tech              → tenant dashboard khuli
https://api.dotstark.tech/health   → {"status":"healthy",...}
https://super.dotstark.tech        → super admin
```

### 4.4 End-to-end smoke test
```bash
# VM pe
docker compose logs -f backend
```
Ab laptop se (ya kisi bhi WhatsApp number se) **WhatsApp message bhejo** → reply VM se aana chahiye.

Phir browser me:
- Dashboard → naya booking create karo (Cashfree live payment)
- Cashfree webhook hit hona chahiye → `docker compose logs backend | grep -i cashfree`
- Payment hone ke baad dashboard me transaction dikhe

---

## PHASE 5 — Ops (baad ke liye)

### Update code
```bash
cd ~/BookingChatBot
git pull
bash scripts/gcp-deploy-prod.sh
```

### Logs
```bash
docker compose logs -f backend
docker compose logs -f wa-gateway
docker compose ps
```

### DB backup (cron)
```bash
crontab -e
# roz raat 3 baje:
0 3 * * * cd ~/BookingChatBot && bash scripts/db-migrate.sh dump >> /var/log/db-backup.log 2>&1
```
> `db-dump-*.dump` git-ignored hai, disk space check karna.

### Restart everything (VM reboot ke baad)
```bash
cd ~/BookingChatBot && docker compose up -d
```
> `restart: unless-stopped` hai — reboot ke baad sab auto-start ho jata hai.

---

## 🧾 Checklist (cutover ke baad)

- [ ] `https://api.dotstark.tech/health` → healthy
- [ ] `https://dotstark.tech` → dashboard open, login kaam karta hai
- [ ] Google Calendar OAuth (redirect URI same hai → login kaam karega)
- [ ] WhatsApp message → reply aaya
- [ ] WhatsApp OTP (phone OTP template) aaya
- [ ] Cashfree payment → success → dashboard me transaction
- [ ] `docker compose ps` → sab `Up`
- [ ] Laptop pe `docker compose stop cloudflared` chal raha hai

---

## ❗ Abhi bhi pending (live payments ke liye)

Ye deploy se independent hain, par bina inke Cashfree/WhatsApp poora live nahi hoga:

1. **Cashfree Payment Links API disabled** hai tenant ke merchant id pe (`care@cashfree.com` ko email karo) — tab tak WhatsApp payment link nahi banega
2. **7 Meta templates** approve nahi hue: `booking_confirmed`, `booking_reminder_24h`, `booking_reminder_1h`, `booking_cancelled`, `booking_rescheduled`, `booking_waitlist_joined`, `booking_abandoned` — reminders/campaigns chup-chap fail honge
3. Cashfree **live merchant** ke liye domain whitelisting — `dotstark.tech` same hai, to pehle se approved hai ✅ (lekin naya IP/VM ke liye Cashfree se confirm kar lena)

---

## 🔁 Agar kuch atak jaye

```bash
docker compose ps                          # kaunsa down hai?
docker compose logs --tail 100 <service>   # uska log
docker compose restart <service>
docker compose down && docker compose up -d # full restart
```

**Rollback (VM pe problem):** laptop pe `docker compose start cloudflared` → traffic turant wapas laptop pe. DB alag hai dono jagah, to nayi bookings VM pe bani rahengi — but the app is back up for the users.
