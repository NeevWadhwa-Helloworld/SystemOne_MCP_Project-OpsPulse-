function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export default function AuditLogTable({ logs, loading, onRefresh }) {
  return (
    <section className="audit panel">
      <div className="panel-heading compact">
        <div>
          <p className="eyebrow">AUDIT TRAIL</p>
          <h2>Recent activity</h2>
        </div>
        <button className="refresh-button" type="button" onClick={onRefresh} disabled={loading}>
          ↻ {loading ? "Loading" : "Refresh"}
        </button>
      </div>
      {loading && !logs.length ? (
        <div className="empty-state">Loading audit events<span className="dots">...</span></div>
      ) : logs.length === 0 ? (
        <div className="empty-state">No commands have been recorded yet.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Timestamp</th><th>Command</th><th>Tool</th><th>Result</th></tr></thead>
            <tbody>
              {logs.map((log, index) => (
                <tr key={`${log.timestamp}-${index}`}>
                  <td className="timestamp">{formatDate(log.timestamp || log.created_at)}</td>
                  <td className="prompt-cell">{log.prompt || log.detail || "—"}</td>
                  <td><code>{log.tool_used || log.tool_selected || log.action || "—"}</code></td>
                  <td><span className={`status-badge ${log.status === "failure" ? "" : "success"}`}><i /> {log.status || "Complete"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
