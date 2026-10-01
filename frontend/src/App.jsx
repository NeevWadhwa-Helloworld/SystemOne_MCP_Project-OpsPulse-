import { useCallback, useEffect, useState } from "react";
import CommandBar from "./components/CommandBar";
import ResultDisplay from "./components/ResultDisplay";
import AuditLogTable from "./components/AuditLogTable";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000/api").replace(/\/$/, "");
const ROLE_ORDER = { viewer: 1, operator: 2, admin: 3 };
const can = (role, required) => (ROLE_ORDER[role] || 0) >= (ROLE_ORDER[required] || 0);
const emptyResource = { name: "", url: "", method: "GET", interval_minutes: 5, timeout_seconds: 10, retries: 2, failure_threshold: 1, enabled: true, alert_webhook_id: "" };

function message(data, fallback) {
  const detail = data?.detail;
  return typeof detail === "string" ? detail : detail?.message || data?.message || data?.error || fallback;
}
function unwrap(data, keys = []) {
  for (const key of keys) if (Array.isArray(data?.[key])) return data[key];
  return Array.isArray(data) ? data : [];
}
function date(value) {
  if (!value) return "Never";
  const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export default function App() {
  const [session, setSession] = useState(() => { try { return JSON.parse(localStorage.getItem("opspulse_session") || "null"); } catch { return null; } });
  const [loginError, setLoginError] = useState("");
  const [tab, setTab] = useState("overview");
  const [error, setError] = useState("");
  const [resources, setResources] = useState([]);
  const [webhooks, setWebhooks] = useState([]);
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  const request = useCallback(async (path, options = {}) => {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (session?.token) headers.Authorization = `Bearer ${session.token}`;
    const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { localStorage.removeItem("opspulse_session"); setSession(null); throw new Error("Your session expired. Please sign in again."); }
    if (!res.ok) { const err = new Error(message(data, `Request failed (${res.status}).`)); err.status = res.status; err.data = data; throw err; }
    return data;
  }, [session]);

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true); setError("");
    const results = await Promise.allSettled([
      request("/health-checks"), request("/webhooks"), request("/logs?limit=50"), request("/scheduler/status"),
    ]);
    const [health, hooks, audit, monitor] = results;
    if (health.status === "fulfilled") setResources(unwrap(health.value, ["items", "health_checks", "resources"]));
    if (hooks.status === "fulfilled") setWebhooks(unwrap(hooks.value, ["items", "webhooks"]));
    if (audit.status === "fulfilled") setLogs(unwrap(audit.value, ["logs", "items"]));
    if (monitor.status === "fulfilled") setStatus(monitor.value);
    const failure = results.find((r) => r.status === "rejected");
    if (failure && failure.reason?.status !== 404) setError(failure.reason.message);
    setLoading(false);
  }, [request, session]);
  useEffect(() => { load(); }, [load]);

  async function login(credentials) {
    setLoginError("");
    try {
      let data;
      for (const path of ["/auth/login", "/login"]) {
        try { const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify(credentials) }); const d = await res.json().catch(() => ({})); if (!res.ok) throw new Error(message(d, "Sign in failed.")); data = d; break; } catch (e) { if (path === "/login") throw e; }
      }
      const next = { token: data.access_token || data.token, username: data.user?.username || data.username || credentials.username, role: data.user?.role || data.role || "viewer" };
      localStorage.setItem("opspulse_session", JSON.stringify(next)); setSession(next);
    } catch (e) { setLoginError(e.message); }
  }
  if (!session) return <LoginScreen onLogin={login} error={loginError} />;
  const logout = () => { localStorage.removeItem("opspulse_session"); setSession(null); };
  return <Dashboard session={session} logout={logout} tab={tab} setTab={setTab} resources={resources} setResources={setResources} webhooks={webhooks} logs={logs} status={status} loading={loading} error={error} setError={setError} reload={load} request={request} />;
}

function LoginScreen({ onLogin, error }) {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ username: "", password: "", confirmPassword: "" });
  const [busy, setBusy] = useState(false);
  const [messageText, setMessageText] = useState("");
  const isSignup = mode === "signup";
  async function submit(event) {
    event.preventDefault();
    setMessageText("");
    if (isSignup && form.password !== form.confirmPassword) {
      setMessageText("Passwords do not match.");
      return;
    }
    setBusy(true);
    if (isSignup) {
      try {
        const res = await fetch(`${API_BASE}/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: form.username, password: form.password }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(message(data, "Account creation failed."));
        setMode("login");
        setMessageText(data.message || "Account created. Sign in to continue.");
        setForm({ username: form.username, password: "", confirmPassword: "" });
      } catch (e) {
        setMessageText(e.message);
      } finally {
        setBusy(false);
      }
      return;
    }
    await onLogin({ username: form.username, password: form.password });
    setBusy(false);
  }
  return <div className="login-shell"><div className="login-card panel"><div className="brand"><span className="brand-mark">◈</span> Ops<span>Pulse</span></div><p className="eyebrow">SECURE OPERATIONS CONTROL CENTER</p><h1>{isSignup ? "Create your operations account." : "Sign in to your command layer."}</h1><p className="muted">{isSignup ? "Create a viewer account to access your monitoring workspace." : "Use your OpsPulse credentials to access monitoring controls."}</p>{(error || messageText) && <div className={`error-banner ${!error && messageText.startsWith("Account created") ? "success-banner" : ""}`} role="alert"><span>{!error && messageText.startsWith("Account created") ? "✓" : "!"}</span>{error || messageText}</div>}<form onSubmit={submit}><label>Username<input required minLength={isSignup ? 3 : 1} autoComplete="username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label><label>Password<input required minLength={isSignup ? 12 : 1} type="password" autoComplete={isSignup ? "new-password" : "current-password"} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>{isSignup && <label>Confirm password<input required minLength={12} type="password" autoComplete="new-password" value={form.confirmPassword} onChange={e => setForm({ ...form, confirmPassword: e.target.value })} /></label>}<button className="primary-button" disabled={busy}>{busy ? (isSignup ? "Creating account…" : "Signing in…") : (isSignup ? "Create account" : "Sign in securely")}</button></form><button className="auth-switch" onClick={() => { setMode(isSignup ? "login" : "signup"); setMessageText(""); }} disabled={busy}>{isSignup ? "Already have an account? Sign in" : "New to OpsPulse? Create an account"}</button><small className="muted">API: {API_BASE}</small></div></div>;
}

function Dashboard({ session, logout, tab, setTab, resources, setResources, webhooks, logs, status, loading, error, setError, reload, request }) {
  const [commandResult, setCommandResult] = useState(null); const [runError, setRunError] = useState(""); const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const tabs = [["overview", "Monitoring"], ["command", "Command console"], ["checks", "Health checks"], ["webhooks", "Webhooks"], ["audit", "Audit logs"]];
  async function runResource(resource) {
    setRunning(true); setRunError("");
    try {
      const data = await request(`/resources/${resource.id}/run`, { method: "POST" });
      setCommandResult(data);
      await reload();
    }
    catch (e) { setRunError(e.message); } finally { setRunning(false); }
  }
  async function command(prompt, resourceId, approvalId) {
    setRunning(true); setRunError("");
    try { const data = await request("/command", { method: "POST", body: JSON.stringify({ prompt, resource_id: resourceId || undefined, approval_id: approvalId || undefined }) }); setCommandResult(data); await reload(); }
    catch (e) {
      if (e.status === 409 && e.data?.detail?.approval_required) {
        const approval = e.data.detail.approval;
        setConfirm({ type: "approval", prompt, resourceId, approvalId: approval.id,
          message: `Approval required for ${approval.action}. An administrator must approve this action.` });
      } else setRunError(e.message);
    } finally { setRunning(false); }
  }
  return <div className="app-shell"><header className="topbar"><a className="brand" href="/"><span className="brand-mark">◈</span> Ops<span>Pulse</span></a><div className="user-menu"><span className="status-dot" /> {session.username}<span className="role-badge">{session.role}</span><button className="link-button" onClick={logout}>Sign out</button></div></header><main>
    <div className="hero"><div><p className="eyebrow">OPERATIONS CONTROL CENTER</p><h1>Stay ahead of<br /><em>every signal.</em></h1></div><div className="hero-copy"><strong>{resources.length} monitored resources</strong><br />Authenticated, role-aware observability for your services.</div></div>
    <nav className="tabs">{tabs.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}<button className="refresh-button nav-refresh" onClick={reload} disabled={loading}>↻ {loading ? "Refreshing" : "Refresh"}</button></nav>
    {(error || runError) && <div className="error-banner" role="alert"><span>!</span><div><strong>Unable to complete request</strong><p>{error || runError}</p></div><button onClick={() => { setError(""); setRunError(""); }}>×</button></div>}
    {tab === "overview" && <Overview resources={resources} status={status} onRun={runResource} role={session.role} request={request} />}
    {tab === "command" && <CommandWorkspace resources={resources} result={commandResult} onSubmit={command} running={running} role={session.role} />}
    {tab === "checks" && <ResourcePanel title="HTTP health checks" items={resources} webhooks={webhooks} role={session.role} request={request} reload={reload} onRun={runResource} running={running} setConfirm={setConfirm} />}
    {tab === "webhooks" && <WebhookPanel items={webhooks} role={session.role} request={request} reload={reload} confirm={confirm} setConfirm={setConfirm} />}
    {tab === "audit" && <AuditLogTable logs={logs} loading={loading} onRefresh={reload} />}
  </main><footer><span>OPSPULSE COMMAND CENTER</span><span>SECURE • AUDITED • OBSERVABLE</span></footer>{confirm && <ConfirmModal data={confirm} onCancel={() => setConfirm(null)} onConfirm={async () => { const c = confirm; setConfirm(null); if (c.type === "approval") { try { await request(`/approvals/${c.approvalId}/approve`, { method: "POST" }); await command(c.prompt, c.resourceId, c.approvalId); } catch (e) { setRunError(e.message); } } else if (c.type === "command") command(c.prompt, c.resourceId); else c.action(); }} />}</div>;
}

function Overview({ resources, status, onRun, role, request }) {
  const [selected, setSelected] = useState(null); const [history, setHistory] = useState([]); const [summary, setSummary] = useState(null);
  const item = resources.find(r => String(r.id) === String(selected)) || resources[0];
  useEffect(() => { if (!item?.id) return; let cancelled = false; Promise.allSettled([request(`/resources/${item.id}/history`), request(`/resources/${item.id}/summary`)]).then(([h, s]) => { if (!cancelled) { setHistory(h.status === "fulfilled" ? unwrap(h.value, ["history", "items", "events"]) : []); setSummary(s.status === "fulfilled" ? s.value : null); } }); return () => { cancelled = true; }; }, [item?.id, request]);
  const healthy = resources.filter(r => r.last_status === "success" || r.status === "healthy" || r.status === "up").length;
  const uptime = summary?.uptime_percent ?? item?.uptime_percent ?? status?.uptime_percent;
  return <><div className="summary-grid"><Stat label="Monitors" value={resources.length} /><Stat label="Healthy" value={`${healthy}/${resources.length}`} tone="green" /><Stat label="Uptime" value={uptime == null ? "—" : `${Number(uptime).toFixed(2)}%`} tone="cyan" /><Stat label="Last check" value={date(item?.last_checked_at || item?.last_checked)} /></div><section className="monitor-layout"><div className="panel monitor-list"><div className="panel-heading compact"><div><p className="eyebrow">LIVE MONITORING</p><h2>Resource status</h2></div></div>{resources.length ? resources.map(r => <div className="monitor-row" key={r.id}><div className="status-pip" data-state={r.last_status || r.status}><i /></div><div className="monitor-name"><strong>{r.name || r.url}</strong><small>{r.method || "GET"} · {r.url}</small></div><span className="latency">{r.latency_ms ?? "—"}<small>ms</small></span><span className={`status-badge ${r.last_status === "failure" || r.status === "down" ? "failure" : "success"}`}>{r.last_status || r.status || "pending"}</span>{can(role, "operator") && <button className="row-run" onClick={() => onRun(r)}>Run check</button>}</div>) : <div className="empty-state">No health checks configured.</div>}</div><div className="panel history-panel"><div className="panel-heading compact"><div><p className="eyebrow">UPTIME & HISTORY</p><h2>{item?.name || "Select a monitor"}</h2></div><select value={item?.id || ""} onChange={e => setSelected(e.target.value)} aria-label="Select monitor">{resources.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div><div className="history-chart">{history.length ? history.slice(-24).map((h, i) => <span key={i} className={h.status === "success" || h.ok ? "ok" : "bad"} title={`${date(h.checked_at || h.timestamp)} · ${h.latency_ms ?? "—"} ms`} />) : <span className="empty-chart">History will appear after the first check.</span>}</div><div className="history-meta"><span>Latency <b>{summary?.avg_latency_ms ?? item?.latency_ms ?? "—"} ms avg</b></span><span>Last checked <b>{date(item?.last_checked_at || item?.last_checked)}</b></span></div></div></section></>;
}
function Stat({ label, value, tone }) { return <div className={`stat panel ${tone || ""}`}><span>{label}</span><strong>{value}</strong></div>; }

function CommandWorkspace({ resources, result, onSubmit, running, role }) {
  const [resourceId, setResourceId] = useState(""); const selected = resources.find(r => String(r.id) === String(resourceId));
  return <><CommandBar onSubmit={p => onSubmit(p, resourceId)} loading={running} resourceOptions={resources} selectedResource={resourceId} onResourceChange={setResourceId} /><div className="dashboard-grid"><div className="main-column"><ResultDisplay result={result?.result || result?.output || result} naturalLanguage={result?.natural_language} workflow={result?.workflow} tool={result?.tool_selected || result?.tool} arguments={result?.arguments} /></div><div className="side-column"><div className="health-card panel"><span className="health-icon">✦</span><div><p className="eyebrow">SELECTED RESOURCE</p><h3>{selected?.name || "Command mode"}</h3><p>{selected ? `${selected.method || "GET"} ${selected.url}` : "Choose a health resource in the command console."}</p></div></div><div className="tip panel"><span>✧</span><p><strong>Role-aware controls</strong><br />{can(role, "operator") ? "Operators can run checks." : "Viewers have read-only access."} Admins manage schedules and webhooks.</p></div></div></div></>;
}

function ResourcePanel({ title, items, webhooks, role, request, reload, onRun, running, setConfirm }) {
  const [editing, setEditing] = useState(null); const editable = can(role, "admin");
  async function save(e) { e.preventDefault(); try { const body = { ...editing, kind: "health_check", interval_minutes: Number(editing.interval_minutes), timeout_seconds: Number(editing.timeout_seconds), retries: Number(editing.retries), failure_threshold: Number(editing.failure_threshold), alert_webhook_id: editing.alert_webhook_id || null }; await request(`/health-checks${editing.id ? `/${editing.id}` : ""}`, { method: editing.id ? "PUT" : "POST", body: JSON.stringify(body) }); setEditing(null); await reload(); } catch (e2) { setConfirm({ type: "error", action: () => {}, message: e2.message }); } }
  async function remove(item) { setConfirm({ type: "delete", message: `Delete ${item.name}?`, action: async () => { try { await request(`/health-checks/${item.id}`, { method: "DELETE" }); await reload(); } catch (e) { setConfirm({ type: "error", message: e.message, action: () => {} }); } } }); }
  return <section className="panel resource-panel"><div className="panel-heading"><div><p className="eyebrow">RESOURCE MANAGEMENT</p><h2>{title}</h2></div>{editable && <button className="primary-button small" onClick={() => setEditing({ ...emptyResource })}>＋ Add check</button>}</div>{editing && <ResourceForm value={editing} setValue={setEditing} onSubmit={save} onCancel={() => setEditing(null)} webhooks={webhooks} />}{items.length ? <div className="resource-list">{items.map(item => <div className="resource-row detailed" key={item.id}><div className="monitor-name"><strong>{item.name || item.url}</strong><small>{item.url} · every {item.interval_minutes ?? "—"} min · timeout {item.timeout_seconds ?? "—"}s · {item.retries ?? 0} retries</small></div><span className={`status-badge ${item.enabled === false ? "" : "success"}`}>{item.enabled === false ? "DISABLED" : "ACTIVE"}</span>{can(role, "operator") && <button className="row-run" onClick={() => onRun(item)} disabled={running}>Run check</button>}{editable && <><button className="text-button" onClick={() => setEditing({ ...emptyResource, ...item })}>Edit</button><button className="danger-link" onClick={() => remove(item)}>Delete</button></>}</div>)}</div> : <div className="empty-state">No resources configured.</div>}</section>;
}
function ResourceForm({ value, setValue, onSubmit, onCancel, webhooks }) { const update = (k, v) => setValue({ ...value, [k]: v }); return <form className="resource-form" onSubmit={onSubmit}><div className="form-grid"><label>Name<input required value={value.name} onChange={e => update("name", e.target.value)} /></label><label>URL<input required type="url" value={value.url} onChange={e => update("url", e.target.value)} /></label><label>Method<select value={value.method || "GET"} onChange={e => update("method", e.target.value)}><option>GET</option><option>HEAD</option><option>POST</option></select></label><label>Interval (minutes)<input type="number" min="1" value={value.interval_minutes} onChange={e => update("interval_minutes", e.target.value)} /></label><label>Timeout (seconds)<input type="number" min="1" value={value.timeout_seconds} onChange={e => update("timeout_seconds", e.target.value)} /></label><label>Retries<input type="number" min="0" max="10" value={value.retries} onChange={e => update("retries", e.target.value)} /></label><label>Failure threshold<input type="number" min="1" max="10" value={value.failure_threshold ?? 1} onChange={e => update("failure_threshold", e.target.value)} /></label><label>Alert webhook<select value={value.alert_webhook_id || ""} onChange={e => update("alert_webhook_id", e.target.value)}><option value="">No webhook</option>{webhooks.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label><label className="toggle-label"><input type="checkbox" checked={value.enabled !== false} onChange={e => update("enabled", e.target.checked)} /> Scheduling enabled</label></div><div className="form-actions"><button className="primary-button">Save schedule</button><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button></div></form>; }

function WebhookPanel({ items, role, request, reload, confirm, setConfirm }) {
  const [editing, setEditing] = useState(null); const editable = can(role, "admin");
  async function save(e) { e.preventDefault(); try { await request(`/webhooks${editing.id ? `/${editing.id}` : ""}`, { method: editing.id ? "PUT" : "POST", body: JSON.stringify({ ...editing, kind: "webhook" }) }); setEditing(null); await reload(); } catch (e2) { setConfirm({ type: "error", message: e2.message, action: () => {} }); } }
  return <section className="panel resource-panel"><div className="panel-heading"><div><p className="eyebrow">ALERT DELIVERY</p><h2>Webhooks</h2></div>{editable && <button className="primary-button small" onClick={() => setEditing({ name: "", url: "", secret: "" })}>＋ Add webhook</button>}</div>{editing && <form className="resource-form" onSubmit={save}><div className="form-grid"><label>Name<input required value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} /></label><label>Endpoint URL<input required type="url" value={editing.url} onChange={e => setEditing({ ...editing, url: e.target.value })} /></label><label>Signing secret<input type="password" value={editing.secret || ""} onChange={e => setEditing({ ...editing, secret: e.target.value })} /></label></div><div className="form-actions"><button className="primary-button">Save webhook</button><button type="button" className="secondary-button" onClick={() => setEditing(null)}>Cancel</button></div></form>}{items.length ? <div className="resource-list">{items.map(w => <div className="resource-row detailed" key={w.id}><div className="monitor-name"><strong>{w.name}</strong><small>{w.url}</small></div><span className="status-badge success">CONFIGURED</span>{editable && <><button className="text-button" onClick={() => setEditing({ ...w, secret: "" })}>Edit</button><button className="danger-link" onClick={() => setConfirm({ type: "delete", message: `Delete webhook ${w.name}? This may stop alert delivery.`, action: async () => { await request(`/webhooks/${w.id}`, { method: "DELETE", body: JSON.stringify({ confirmed: true }) }); await reload(); } })}>Delete</button></>}</div>)}</div> : <div className="empty-state">No webhooks configured.</div>}</section>;
}
function ConfirmModal({ data, onCancel, onConfirm }) { if (data.type === "error") return <div className="modal-backdrop"><div className="modal panel" role="dialog"><p className="eyebrow">REQUEST ERROR</p><h2>Could not save changes</h2><p>{data.message}</p><button className="secondary-button" onClick={onCancel}>Close</button></div></div>; return <div className="modal-backdrop"><div className="modal panel" role="dialog" aria-modal="true"><p className="eyebrow">CONFIRMATION REQUIRED</p><h2>{data.type === "delete" ? "Delete this resource?" : "Approve command?"}</h2><p>{data.message || "This action can change operational state."}</p><div className="form-actions"><button className="secondary-button" onClick={onCancel}>Cancel</button><button className="danger-button" onClick={onConfirm}>{data.type === "delete" ? "Delete" : "Approve and run"}</button></div></div></div>; }
