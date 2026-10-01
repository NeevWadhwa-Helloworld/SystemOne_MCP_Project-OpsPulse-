const metrics = [
  ["route_latency_ms", "Route latency", "ms"],
  ["extraction_latency_ms", "Extraction", "ms"],
  ["mcp_latency_ms", "MCP execution", "ms"],
  ["total_latency_ms", "Total latency", "ms"],
];

export default function TelemetryCard({ telemetry }) {
  if (!telemetry) return null;
  return (
    <section className="telemetry panel">
      <div className="panel-heading compact">
        <div>
          <p className="eyebrow">LIVE TELEMETRY</p>
          <h2>Command performance</h2>
        </div>
        <span className="live-indicator"><i /> LIVE</span>
      </div>
      <div className="metric-grid">
        {metrics.map(([key, label, unit]) => (
          <div className="metric" key={key}>
            <span>{label}</span>
            <strong>{telemetry[key] ?? "—"}<small>{unit}</small></strong>
          </div>
        ))}
      </div>
    </section>
  );
}
