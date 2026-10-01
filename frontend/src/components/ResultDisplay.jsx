function formatResult(result) {
  if (typeof result === "string") return result;
  return JSON.stringify(result, null, 2);
}

export default function ResultDisplay({ result, naturalLanguage, workflow, tool, arguments: args }) {
  if (result === null || result === undefined) return null;
  return (
    <section className="result panel">
      <div className="panel-heading compact">
        <div>
          <p className="eyebrow">EXECUTION RESULT</p>
          <h2>{tool || "Command completed"}</h2>
        </div>
        {workflow && <div className="argument-row"><span><b>Workflow</b> {workflow.status}</span><span>{workflow.completed_steps}/{workflow.steps} steps completed</span></div>}
        <span className="status-badge success"><i /> SUCCESS</span>
      </div>
      {args && Object.keys(args).length > 0 && (
        <div className="argument-row">
          {Object.entries(args).map(([key, value]) => (
            <span key={key}><b>{key}</b> {String(value)}</span>
          ))}
        </div>
      )}
      {naturalLanguage && (
        <div className="natural-language-result">
          <p className="eyebrow">NATURAL LANGUAGE SUMMARY</p>
          <p>{naturalLanguage}</p>
        </div>
      )}
      <pre className="result-payload">{formatResult(result)}</pre>
    </section>
  );
}
