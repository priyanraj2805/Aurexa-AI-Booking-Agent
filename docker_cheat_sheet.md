# 🐳 Docker Workflow & Developer Cheat Sheet

You asked how this all suddenly worked, and how you can manage this going forward. Here is the ultimate guide to your new setup.

---

## 🛠️ How I Fixed It (The Magic)
Previously, your database `postgres_data` volume on your local hard drive got mismatched and corrupted with old migrations. Because Docker saves volumes permanently even when you restart, the backend crashes kept carrying over.

I used the **Nuclear Option**: `docker compose down -v`. This completely destroyed your corrupted database and forced the system to rebuild a 100% fresh, perfectly clean database from scratch. 

---

## 🚀 Daily Driver Commands

### 1. The Magic Start Button
Whenever you sit down to work, just open PowerShell in your `booking_agent` folder and run:
```powershell
.\start.ps1
```
I modified this script for you. It automatically tells Docker to start everything in the background (`-d`), waits a few seconds, and **only prints your Frontend and Backend URLs** cleanly in your terminal. 

### 2. When you modify your code
If you update any Python code, Next.js code, or install new packages, Docker doesn't know about it unless you rebuild the containers. To apply new code changes, run:
```powershell
docker compose up --build -d
```
*(Note: `.\start.ps1` already includes `--build`, so running your script also works perfectly for updating code!)*

### 3. Tomorrow morning after a PC restart
If you turn your computer off and on again, Docker will actually try to automatically resume your containers. 
If it doesn't, or you want to be sure, just type:
```powershell
docker compose up -d
```
This forces them to wake up immediately without spending time rebuilding.

### 4. How to view logs (If something breaks)
If you ever want to see what is happening inside the Matrix (or why a login isn't working), you can stream the live logs of your backend:
```powershell
docker compose logs -f backend
```
Press `CTRL + C` to exit the log view.

### 5. The "Nuclear Option" (Hard Reset)
If your database gets corrupted again or you just want to obliterate all test users to start totally fresh, run:
```powershell
docker compose down -v
```
**Warning:** This completely wipes your database. Next time you run `docker compose up`, it will recreate a brand new empty database.

---

## 🤝 How to Share With a Colleague

If you want your colleague to have the exact same setup on their computer, doing it is beautifully simple:

1. **Clean up your folder:** Delete any big, hidden temp folders like `node_modules` in frontend/backend, or `__pycache__`. (This makes the zip file small). 
2. **Zip the `booking_agent` folder** and send it to them.
3. **Tell them exactly this process:**
   > *"Hey! Make sure you have Docker Desktop installed. Unzip this folder, open PowerShell inside it, and run `.\start.ps1`. It will build everything automatically and spit out your live test URLs."*

*Note: Since `.env` files are ignored by git/zip files, make sure they copy the `.env.example` files into `.env` equivalents on their machine just like you did.*

---

## 🔐 Dealing with Cloudflare URLs & Google/Microsoft OAuth

Because we are using Cloudflare Tunnels anonymously, **your URLs change every single time you restart your PC or stop the containers**. 

Because your URLs change, Google and Microsoft will block your logins, saying *"Unauthorized Redirect URI"* or *"Domain Mismatch"*.

### Step-By-Step Update Process Every Morning:
Every time you run `.\start.ps1` and get **New URLs**, you must do the following to make logins work:

1. **Update Google Cloud Console (Google Login):**
   * Go to [Google Cloud Console (Credentials)](https://console.cloud.google.com/apis/credentials)
   * Edit your OAuth 2.0 Web Client.
   * Add your new **Admin Panel URL** as an *Authorized JavaScript origin* (e.g. `https://your-new-admin-url.trycloudflare.com`)
   * Add the NextAuth callback URL to *Authorized redirect URIs*: `https://your-new-admin-url.trycloudflare.com/api/auth/callback/google`
   * Click **Save**.

2. **Update Microsoft Entra ID (Azure / Outlook Login)**
   * Go to [Azure App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
   * Open your App -> **Authentication**
   * Add the new Redirect URI: `https://your-new-admin-url.trycloudflare.com/api/auth/callback/microsoft`
   * Click **Save**.

3. **Update Your `.env` Files (If Hardcoded)**
   If your `backend/.env.docker` or your frontend `.env` require the URL to be specified to know where they live:
   * Open `booking_agent/backend/.env.docker` and change `FRONTEND_URL` or `CORS_ORIGINS` if necessary.
   * *You MUST restart the specific container for `.env` file changes to take effect:*
     ```powershell
     docker compose restart backend
     ```
