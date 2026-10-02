# OpsPulse Deployment — Railway (backend) + Vercel (frontend)

OpsPulse is deployed as two independent services:

- **Backend:** FastAPI/Uvicorn on **Railway** (free tier, persistent volume included)
- **Frontend:** Vite static build on **Vercel** (free forever)

Both platforms are **100% free** — no credit card required for Railway's Hobby plan
(500 hours/month free), and Vercel's free tier covers frontend hosting indefinitely.

---

## Why Railway instead of Render?

Render's free tier does **not** include persistent disk storage — adding a disk costs
$7+/month. Railway's free tier includes a **persistent volume** so SQLite data
(users, monitors, history, audit logs) survives every redeploy.

---

## Backend — Railway

### 1. Prerequisites

- GitHub account with this repo pushed to it
- Railway account: https://railway.app (sign in with GitHub — free, no card needed)

### 2. Create a new Railway project

1. Go to https://railway.app/dashboard → **New Project**
2. Choose **Deploy from GitHub repo** → select this repository
3. Railway auto-detects `railway.json` and `nixpacks.toml` — no manual config needed
4. The build command is `pip install -r backend/requirements.txt`
5. The start command is `uvicorn backend.api_server:app --host 0.0.0.0 --port $PORT`

### 3. Add a persistent volume

> This is the critical step that replaces the Render disk — and it's FREE on Railway.

1. In your Railway project, click **+ New** → **Volume**
2. Attach it to your backend service
3. Set the **Mount Path** to `/var/data`
4. Railway will keep this volume between deploys

### 4. Set environment variables

In Railway → your service → **Variables**, add:

```text
APP_MASTER_KEY=<generate a new Fernet key — see below>
BOOTSTRAP_ADMIN_USERNAME=<your admin username>
BOOTSTRAP_ADMIN_PASSWORD=<password with at least 12 characters>
CORS_ORIGINS=https://<your-vercel-frontend-url>
OPSPULSE_DB_PATH=/var/data/opspulse.db
GEMINI_API_KEY=<your Gemini API key>
GEMINI_MODEL=gemini/gemini-2.0-flash
MONITOR_ALERT_COOLDOWN_SECONDS=300
```

Generate a fresh Fernet key (run locally):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **Warning:** Never reuse the `.env` dev values in production. `APP_MASTER_KEY`
> must never change after data is written — it decrypts stored webhook secrets.

### 5. Get your backend URL

After the first deploy succeeds, Railway assigns a public URL like:
`https://your-project-name.up.railway.app`

Verify it works:
```
GET https://your-project-name.up.railway.app/api/health
→ {"status": "ok"}
```

---

## Frontend — Vercel

### 1. Import the project

1. Go to https://vercel.com → **Add New Project**
2. Import from GitHub → select this repository
3. Set **Root Directory** to `frontend`
4. Vercel auto-detects Vite

### 2. Set the environment variable

Before the first build, add in Vercel → Project Settings → Environment Variables:

```text
VITE_API_BASE=https://<your-railway-backend-url>/api
```

Include the `/api` suffix. Without this, all API calls will fail (fallback is
`http://localhost:8000/api`).

### 3. Build settings (auto-detected)

```text
Build Command:    npm run build
Output Directory: dist
Install Command:  npm ci
```

### 4. SPA routing

`frontend/vercel.json` already handles SPA catch-all rewrites:

```json
{
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

---

## Final step — lock down CORS

Once Vercel assigns your frontend URL (e.g. `https://opspulse.vercel.app`):

1. In Railway → your service → Variables, update:
   ```text
   CORS_ORIGINS=https://opspulse.vercel.app
   ```
2. Railway will auto-redeploy. CORS origins are read at startup.

---

## Deployment order

1. Push repo to GitHub (if not done yet).
2. Deploy backend on Railway → add volume at `/var/data` → set env vars → verify `/api/health`.
3. Copy Railway backend URL.
4. Set `VITE_API_BASE=https://<railway-url>/api` in Vercel.
5. Deploy frontend on Vercel → copy Vercel URL.
6. Update `CORS_ORIGINS` in Railway to the exact Vercel URL → auto-redeploys.

---

## Free tier limits

| Platform | Free limit |
|----------|-----------|
| Railway  | 500 hours/month compute + 1GB volume (Hobby plan, no card required for trial) |
| Vercel   | Unlimited static hosting on free plan |

> Railway's free "Trial" gives $5 credit. To avoid expiry, add a card (no charge
> unless you exceed limits) or use the Hobby plan at $5/month — still far cheaper
> than Render's $7/month disk alone.

---

## Data safety

- The SQLite database lives at `/var/data/opspulse.db` on the Railway volume.
- It survives redeploys, restarts, and Railway maintenance.
- Back it up periodically via `railway run -- sqlite3 /var/data/opspulse.db .dump > backup.sql`.
