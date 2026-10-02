# OpsPulse Lifetime Free Deployment (Render + Turso + Vercel)

This setup is **100% Lifetime Free**, costs **$0 forever**, and **does NOT require a credit card**.

- **Backend:** FastAPI on **Render** (Free Web Service)
- **Database:** SQLite on **Turso** (Free Cloud SQLite — 500 MB DB, 9B reads/mo, 100% lifetime free)
- **Frontend:** Static Vite site on **Vercel** (Free forever)

---

## 1. Create Free Cloud SQLite Database on Turso (2 minutes)

1. Go to **https://turso.tech** → Sign in with GitHub (Free forever, no card).
2. Create a database named `opspulse`:
   - From web dashboard or CLI: `turso db create opspulse`
3. Copy your **Database URL** (e.g., `libsql://opspulse-yourusername.turso.io`) and generate an **Auth Token**:
   - Dashboard → Databases → opspulse → Tokens → **Create Token** (or CLI: `turso db tokens create opspulse`)

---

## 2. Deploy Backend on Render (Free Web Service)

1. Go to **https://render.com** → Sign in with GitHub.
2. Click **New +** → **Web Service** → Connect your repository `SystemOne_MCP_Project-OpsPulse-`.
3. Configure settings:
   - **Name:** `opspulse-backend`
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r backend/requirements.txt`
   - **Start Command:** `uvicorn backend.api_server:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** `Free` ($0/mo)

4. Add **Environment Variables** under Environment:

```text
APP_MASTER_KEY=<generate a Fernet key>
BOOTSTRAP_ADMIN_USERNAME=neevwadhwa9568@gmail.com
BOOTSTRAP_ADMIN_PASSWORD=OpsPulse-Admin-2026!
TURSO_DATABASE_URL=libsql://opspulse-yourusername.turso.io
TURSO_AUTH_TOKEN=<your-turso-token>
CORS_ORIGINS=*
GEMINI_API_KEY=<your-key>
GEMINI_MODEL=gemini/gemini-2.0-flash
MONITOR_ALERT_COOLDOWN_SECONDS=300
```

> Generate a fresh Fernet key locally:
> `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

5. Click **Create Web Service**.
6. Once deployed, copy your Render URL: `https://opspulse-backend-xxxx.onrender.com`.

---

## 3. Deploy Frontend on Vercel

1. Go to **https://vercel.com** → **Add New Project** → Import `SystemOne_MCP_Project-OpsPulse-`.
2. Set **Root Directory** to `frontend`.
3. Under **Environment Variables**, add:
   - `VITE_API_BASE` = `https://opspulse-backend-xxxx.onrender.com/api`
4. Click **Deploy**.
5. Copy your Vercel URL: `https://opspulse.vercel.app`.

---

## 4. Final step — Lock down CORS

In Render dashboard → `opspulse-backend` → Environment:
- Update `CORS_ORIGINS` to `https://opspulse.vercel.app`
- Save changes (Render auto-redeploy).

---

## Summary of 100% Free Stack

| Component | Provider | Free Plan Details |
|-----------|----------|-------------------|
| Backend | Render Web Service | 750 free hours/mo (Lifetime Free) |
| Database | Turso (Cloud SQLite) | 500 MB DB, 9B reads/mo (Lifetime Free) |
| Frontend | Vercel | Unlimited static hosting (Lifetime Free) |
