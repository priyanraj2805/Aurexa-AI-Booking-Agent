# 🚀 Booking Agent — Setup & Run Guide

> **Who is this for?** Anyone who received this project as a ZIP file and wants to get it running on their machine in minutes.
> No prior knowledge of Docker or the codebase required — just copy-paste the commands below.

---

## ⚡ What You Need First (Prerequisites)

You only need **two things** installed on your machine:

| Tool | Why | Install Guide |
|------|-----|---------------|
| **Docker** | Runs the entire project (database, backend, frontend) in isolated containers | See Section 1 below |
| **A terminal** | To run the commands | Windows: use **PowerShell** or **Windows Terminal** |

> **That's it.** No Python installation. No Node.js. No PostgreSQL. Docker handles all of it.

---

## 📦 Section 1 — Install Docker (Two Options)

Your friend or teammate may have sent you here — pick whichever method suits you:

---

### ✅ Option A — Docker Desktop (Easiest, GUI app)
> Recommended if you prefer a graphical interface.

1. Go to: **https://www.docker.com/products/docker-desktop/**
2. Click **"Download for Windows"** and install it
3. Restart your PC when prompted
4. After restart, open **Docker Desktop** from Start Menu
5. Wait until you see **"Docker is running"** (whale icon 🐳 in system tray stops animating)

---

### ✅ Option B — Docker Engine via WSL2 (No GUI app, command-line only)
> What your friend recommended — lighter, no paid subscription risk, runs in background.

Open **PowerShell as Administrator** and run these commands **one by one**:

```powershell
# Step 1 — Enable WSL2 (Windows Subsystem for Linux)
wsl --install
```
> ⚠️ Your PC will restart after this. Come back and continue from Step 2.

```powershell
# Step 2 — After restart, Ubuntu opens automatically.
# Set a username + password when prompted, then close Ubuntu.
# Back in PowerShell, install Docker Engine inside WSL2:
wsl -d Ubuntu -- bash -c "curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER"
```

```powershell
# Step 3 — Verify Docker works inside WSL2
wsl -d Ubuntu -- docker --version
```
You should see something like: `Docker version 27.x.x`

```powershell
# Step 4 — For the rest of this guide, always run docker commands via:
wsl -d Ubuntu -- docker compose up --build
# (We'll show this below in the correct context)
```

---

## 📁 Section 2 — Project Setup

### Step 1 — Unzip the project
Unzip the folder you received anywhere on your machine.
Example: `C:\Projects\booking_agent\`

Open a terminal (**PowerShell** or **Windows Terminal**) and navigate to the project folder:

```powershell
cd C:\Projects\booking_agent
```
> Replace `C:\Projects\booking_agent` with wherever you actually unzipped it.

---

### Step 2 — Set up Environment Variables

The project requires some configuration files containing passwords and API keys. Because these are secrets, they are **not** committed to version control. Instead, we use `.example` templates.

If you don't already have them, you need to create two environment files by copying the examples.

**Run these commands to copy the templates:**
```powershell
# Windows
Copy-Item .env.example -Destination .env
Copy-Item backend\.env.docker.example -Destination backend\.env.docker

# Mac/Linux (WSL)
cp .env.example .env
cp backend/.env.docker.example backend/.env.docker
```

> ⚠️ **Important:** Open `backend/.env.docker` in any text editor and fill in your actual API keys (e.g., `LLM_API_KEY`, etc.) before continuing, or the backend won't start correctly.
---

## 🟢 Section 3 — Run The Project

Recommended on Windows (starts in background and prints Cloudflare URLs automatically):

```powershell
.\start.ps1
```

Or run Docker Compose directly:

### If you installed **Docker Desktop (Option A)**:

```powershell
docker compose up --build
```

### If you installed **Docker via WSL2 (Option B)**:

```powershell
wsl -d Ubuntu -- bash -c "cd /mnt/c/Projects/booking_agent && docker compose up --build"
```
> Replace `/mnt/c/Projects/booking_agent` with your actual path. Windows path `C:\Projects\booking_agent` becomes `/mnt/c/Projects/booking_agent` in WSL2.

---

### ⏳ What happens after you run the command?

Docker will now:

1. **Download base images** (Python, Node.js, PostgreSQL) — ~2 minutes, only on first run
2. **Build the backend** — installs Python packages (~1 minute)
3. **Build the admin panel** — installs Node packages and compiles Next.js (~2 minutes)
4. **Start all services** — database, cache, backend API, admin panel
5. **Create public URLs** via Cloudflare Tunnel — watch for these lines in the terminal:

```
cloudflared-backend  | +-------------------------------------------------------------+
cloudflared-backend  |  Your quick Tunnel has been created! Visit it at:
cloudflared-backend  |  https://RANDOM-WORDS-HERE.trycloudflare.com   ← BACKEND URL
cloudflared-admin    |  https://OTHER-WORDS-HERE.trycloudflare.com    ← ADMIN PANEL URL
```

> 💡 Copy these URLs — the admin panel URL is your login page.
> The backend URL + `/docs` shows the full API documentation.

**First full build takes ~5 minutes. Subsequent starts take ~30 seconds.**

---

## 🌐 Section 4 — Access the Application

Once the terminal shows the Cloudflare URLs:

| What | Where |
|------|-------|
| **Admin Panel** (login page) | The `cloudflared-admin` URL from terminal |
| **API Documentation** | The `cloudflared-backend` URL + `/docs` |
| **Local Admin Panel** | http://localhost:3002 |
| **Local API** | http://localhost:8005 |

> Both the public (Cloudflare) URL and local URLs work simultaneously.

---

## 🧪 Section 4.1 — Test Widget Embed on a Real Web Page

After running `.\start.ps1`, it prints:
- backend tunnel URL
- admin tunnel URL
- an **Integrations (prefilled)** link

Use that prefilled link first. It opens Integrations with `public_api_base` auto-filled.

Then:

1. Go to **Integrations → Widget**
2. Confirm **Public URL Configuration** shows your backend `/api/v1`
3. Copy the **Production** snippet
4. Create `widget-test.html` anywhere and paste:

```html
<!doctype html>
<html>
  <body>
    <h1>Widget test page</h1>
    <!-- Paste copied widget snippet below -->
  </body>
</html>
```

5. Serve it locally:

```powershell
python -m http.server 8090
```

6. Open `http://localhost:8090/widget-test.html`
7. (Optional public test) tunnel this test page with cloudflared and open from another device.

### OAuth note (Google/Microsoft/Zoom)

OAuth providers require **stable redirect URIs**. `trycloudflare.com` URLs rotate, so update redirect URIs after tunnel changes or move to a stable tunnel/domain for reliable OAuth testing.

---

## 🛑 Section 5 — Stopping the Project

In the same terminal where you ran `docker compose up`, press:

```
Ctrl + C
```

To completely remove containers (data is preserved):

```powershell
docker compose down
```

To restart later (fast, no rebuild needed):

```powershell
docker compose up
```

---

## 🔄 Section 6 — Common Issues & Fixes

### ❌ "Port already in use" error
Something else on your machine is using port 8005 or 3002.

```powershell
# Find and kill the process using port 8005
netstat -ano | findstr :8005
# Note the PID number at the end, then:
taskkill /PID <PID_NUMBER> /F
```

---

### ❌ Backend starts but shows database errors
The database might still be initializing. Wait 10 seconds and check again.
If it persists, restart:

```powershell
docker compose restart backend
```

---

### ❌ Cloudflare URL doesn't appear in logs
The tunnel takes 10-20 seconds to connect. If you missed the startup lines (or started in detached mode), run:

```powershell
# Show latest backend + admin tunnel URLs right now
docker compose logs --no-color cloudflared-backend cloudflared-admin `
  | Select-String -Pattern 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' `
  | Select-Object -Last 2
```

Or simply run:

```powershell
.\start.ps1
```

It restarts services (if needed) and prints the latest detected URLs clearly.

---

### ❌ Want to see full logs for a specific service?
```powershell
# Show backend logs only
docker compose logs backend

# Follow live logs
docker compose logs -f backend
docker compose logs -f admin-panel
```

---

### ❌ Nuclear reset — wipe everything and start fresh
```powershell
docker compose down -v --remove-orphans
docker compose up --build
```
> ⚠️ `-v` removes the database volume. All data will be lost. Only use if something is seriously broken.

---

## 📋 Quick Reference Card

```
START (first time):    docker compose up --build
START (after that):    docker compose up
STOP:                  Ctrl+C  then  docker compose down
LOGS (live):           docker compose logs -f
FULL RESET:            docker compose down -v && docker compose up --build
```

---

## 📞 Need Help?

Contact the developer who sent you this ZIP file.
Share the exact error message from your terminal — that will help diagnose the issue quickly.
