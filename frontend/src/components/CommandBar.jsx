import { useState } from "react";

const suggestions = [
  "Check the selected website",
  "Run a health check with latency measurement",
  "Check whether the selected website is available",
];

export default function CommandBar({ onSubmit, loading, resourceOptions = [], selectedResource = "", onResourceChange }) {
  const [prompt, setPrompt] = useState("");

  const submit = (event) => {
    event.preventDefault();
    if (!prompt.trim() || loading) return;
    onSubmit(prompt.trim());
  };

  return (
    <section className="command-panel panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">COMMAND CONSOLE</p>
          <h2>What needs attention?</h2>
        </div>
        <span className="keyboard-hint">⌘ K</span>
      </div>
      <form className="command-form" onSubmit={submit}>
        <span className="prompt-mark">&gt;_</span>
        <input
          aria-label="Operational command"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Ask OpsPulse to run an operational command..."
          disabled={loading}
        />
        <button type="submit" disabled={loading || !prompt.trim()}>
          {loading ? "Running..." : "Run command"}
          <span aria-hidden="true">↗</span>
        </button>
      </form>
      {onResourceChange && (
        <label className="command-resource">
          Target health resource
          <select value={selectedResource} onChange={(event) => onResourceChange(event.target.value)} disabled={loading}>
            <option value="">Use natural-language command</option>
            {resourceOptions.map((resource) => <option key={resource.id} value={resource.id}>{resource.name || resource.url}</option>)}
          </select>
        </label>
      )}
      <div className="suggestions" aria-label="Command suggestions">
        <span className="suggestion-label">Try:</span>
        {suggestions.map((suggestion) => (
          <button
            type="button"
            className="suggestion"
            key={suggestion}
            onClick={() => setPrompt(suggestion)}
            disabled={loading}
          >
            {suggestion}
          </button>
        ))}
      </div>
    </section>
  );
}
