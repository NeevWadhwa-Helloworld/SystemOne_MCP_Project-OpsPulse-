# OpsPulse deployment — Render (backend) + Vercel (frontend)

OpsPulse is deployed as two independent services:

- **Backend:** FastAPI/Uvicorn on Render (Python web service with persistent disk)
- **Frontend:** Vite static build on Vercel

The backend owns authentication, monitoring, scheduled checks, SQLite persistence,
webhook secrets, and optional Gemini calls. The frontend is only a static browser
application and must be built with the public backend URL baked in at build time.

---

## Backend service (Render)

Use a Python 3.11+ web service with the repository root as its working directory.

The repository includes `render.yaml` for the backend service only. Create a new
Blueprint from the repository in the Render dashboard and provide the secret
environment values marked `sync: false`.

Build/install command:

```bash
pip install -r backend/requirements.txt
```

Start command:

```bash
uvicorn backend.api_server:app --host 0.0.0.0 --port $PORT
```

### Required environment variables (set in Render dashboard)

```text
APP_MASTER_KEY=<new Fernet key>
BOOTSTRAP_ADMIN_USERNAME=<admin username>
BOOTSTRAP_ADMIN_PASSWORD=<password with at least 12 characters>
CORS_ORIGINS=https://<your-vercel-frontend-url>
OPSPULSE_DB_PATH=/var/data/opspulse.db
```

Generate a new encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Optional environment variables

```text
GEMINI_API_KEY=<optional>
GEMINI_MODEL=gemini/gemini-2.0-flash
MONITOR_ALERT_COOLDOWN_SECONDS=300
```

> **Warning:** Do not reuse the development values from `.env.example`, and do not
> commit `.env`. `APP_MASTER_KEY` must remain unchanged after data is created
> because it decrypts stored webhook secrets.

> **Warning:** Render's free tier does **not** support persistent disks. Use at
> least the **Starter** plan ($7/mo) to retain SQLite data across deploys.

### Persistent disk

`render.yaml` mounts a 1 GB disk at `/var/data` and stores SQLite at
`/var/data/opspulse.db`. Do not skip the disk configuration — ephemeral storage
will lose all users, resources, history, and audit logs on every redeploy.

### Health check

```text
GET https://<your-backend>.onrender.com/api/health
```

---

## User registration

The public sign-up form is available from the frontend login screen. New accounts
are created with the `viewer` role, which is intentionally read-only. This lets
new users access the workspace without granting permission to modify monitors,
send alerts, or approve sensitive actions. The bootstrap account remains the
initial administrator.

---

## Frontend service (Vercel)

### 1. Import the project

In the Vercel dashboard, import the repository and set the **Root Directory** to
`frontend`. Vercel will auto-detect Vite.

### 2. Set the environment variable

Before triggering the first build, add this environment variable in
Vercel → Project Settings → Environment Variables:

```text
VITE_API_BASE=https://<your-backend>.onrender.com/api
```

The value must include the `/api` suffix. If this is not set at build time the
frontend will silently fall back to `http://localhost:8000/api` and all API
calls will fail in production.

### 3. Build settings (auto-detected by Vercel)

```text
Build Command:   npm run build
Output Directory: dist
Install Command: npm ci
```

### 4. SPA routing

`frontend/vercel.json` provides a catch-all rewrite so that direct URL visits
and page refreshes do not return 404:

```json
{
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

---

## Final step — lock down CORS

Once Vercel assigns the frontend URL (e.g. `https://opspulse.vercel.app`):

1. In the Render dashboard, update `CORS_ORIGINS` to that exact origin:
   ```text
   CORS_ORIGINS=https://opspulse.vercel.app
   ```
2. Redeploy the backend service (CORS origins are read at startup).

Use comma-separated values if more than one frontend origin is needed.

---

## Deployment order

1. Deploy the backend on Render via Blueprint → verify `/api/health` returns `ok`.
2. Copy the backend URL.
3. Set `VITE_API_BASE` and `CORS_ORIGINS` (temporarily `*` if Vercel URL is unknown).
4. Deploy the frontend on Vercel → copy the Vercel URL.
5. Update `CORS_ORIGINS` on Render to the exact Vercel URL → redeploy backend.
