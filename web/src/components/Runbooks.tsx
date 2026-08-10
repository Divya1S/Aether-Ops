import { useState } from "react";
import type { Runbook } from "../types";
import { searchRunbooks } from "../api";

export function RunbookTab({ live }: { live: boolean }) {
  const [query, setQuery] = useState("OOMKilled pods after deploy");
  const [results, setResults] = useState<Runbook[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSearch() {
    setBusy(true);
    try {
      setResults(await searchRunbooks(live, query));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="panel">
        <h2>Runbook retrieval (RAG)</h2>
        <p className="muted">
          Hybrid keyword+vector search over operational runbooks, with{" "}
          <code>rag://doc#offset</code> source attribution.
        </p>
        <div className="row">
          <input
            className="q"
            value={query}
            size={34}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="act" onClick={onSearch} disabled={busy}>Search</button>
        </div>
      </div>
      {results && (
        <div className="panel">
          {!live && <p className="muted">Recorded results for “OOMKilled pods after deploy”.</p>}
          <table>
            <thead>
              <tr><th>score</th><th>runbook</th><th>excerpt</th></tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i}>
                  <td><code>{r.score.toFixed(3)}</code></td>
                  <td><b>{r.title}</b><br /><code>{r.ref}</code></td>
                  <td className="muted">{r.excerpt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
