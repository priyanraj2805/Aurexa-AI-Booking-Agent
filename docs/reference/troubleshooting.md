# Troubleshooting

> Solutions to common issues

This document covers common problems and their solutions.

---

## 1. Backend Issues

### 1.1 "Connection refused" to Database

**Error:**
```
psycopg2.OperationalError: could not connect to server
```

**Cause:** PostgreSQL not running or wrong credentials

**Solution:**
```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Verify credentials in .env
cat backend/.env
```

---

### 1.2 "relation does not exist" Error

**Error:**
```
relation "table_name" does not exist
```

**Cause:** Database migrations not run

**Solution:**
```bash
cd backend
alembic upgrade head
```

---

### 1.3 LLM API Errors

**Error:**
```
LLM API error: Invalid API key
```

**Cause:** Wrong or missing Groq API key

**Solution:**
1. Get API key from https://console.groq.com/keys
2. Add to `.env`:
```bash
LLM_API_KEY=gsk_xxxxxxxxxxxxx
```

---

### 1.4 Rate Limiting Errors

**Error:**
```
429 Too Many Requests
```

**Cause:** Exceeded API rate limit

**Solution:**
- Wait 1 minute before retrying
- Check slowapi configuration in code

---

### 1.5 JWT Token Errors

**Error:**
```
JWT token verification failed
```

**Cause:** Wrong or expired JWT secret

**Solution:**
```bash
# Generate new secret
python -c "import secrets; print(secrets.token_urlsafe(64))"

# Update .env
JWT_SECRET_KEY=<new-secret>
```

---

## 2. Frontend Issues

### 2.1 Admin Panel Not Loading

**Error:** Blank page or loading spinner

**Solution:**
```bash
# Check backend is running
curl http://localhost:8000/health

# Clear cache and rebuild
cd frontend/admin-panel
rm -rf .next
npm run dev
```

### 2.2 Widget Not Connecting

**Error:** "Couldn't reach the server"

**Solution:**
1. Verify backend URL in widget config
2. Check CORS settings in backend
3. Check network tab for errors

---

### 2.3 Build Errors

**Error:**
```
Module not found: Can't resolve 'module-name'
```

**Solution:**
```bash
cd frontend/admin-panel
npm install
```

---

## 3. Database Issues

### 3.1 Slot Already Booked Error

**Error:**
```
Slot already booked when trying to confirm
```

**Cause:** Another customer booked the slot

**Solution:**
- Show customer alternative time slots
- Implement retry logic in frontend

---

### 3.2 OTP Not Sending

**Error:**
```
SMTP connection failed
```

**Cause:** Wrong SMTP credentials

**Solution:**
1. Check SMTP settings in `.env`
2. For Gmail: Use [App Password](https://support.google.com/accounts/answer/185833)
3. Test with:
```bash
cd backend
python -c "
import smtplib
s = smtplib.SMTP('smtp.gmail.com', 587)
s.starttls()
s.login('user', 'password')
print('OK')
"
```

---

## 4. Configuration Issues

### 4.1 Environment Variables Not Loading

**Cause:** `.env` file not in correct location

**Solution:**
```bash
# .env should be in backend/ folder
ls backend/.env
```

### 4.2 Port Already in Use

**Error:**
```
Port 8000 is already in use
```

**Solution:**
```bash
# Find process using port
netstat -ano | findstr :8000

# Kill process
taskkill /PID <pid> /F
```

---

## 5. GitHub Push Issues

### 5.1 Secret Detected in Push

**Error:**
```
remote: Push cannot contain secrets
```

**Solution:**
1. Remove secrets from files
2. Use `.gitignore` properly
3. Amend commit:
```bash
git rm --cached <file-with-secret>
git commit --amend
git push --force
```

---

## 6. Getting Help

### Debug Mode

Enable detailed logging:
```bash
# .env
DEBUG=true
```

### Check Logs

```bash
# Backend logs
docker-compose logs backend

# Or uvicorn
uvicorn app.main:app --log-level debug
```

### Health Check

```bash
# Backend
curl http://localhost:8000/health

# Should return:
{"status":"ok"}
```

---

## 7. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Environment Variables](environment-vars.md) | Configuration |
| [Production Deployment](../deployment/production.md) | Production setup |
| [API Overview](../api/overview.md) | Endpoint reference |

---

*Level: All levels — Reference guide for common issues.*
