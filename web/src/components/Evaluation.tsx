import { useState } from "react";
import type { Evals } from "../types";
import { runEvals } from "../api";

export function EvalTab({ live }: { live: boolean }) {
  const [evals, setEvals] = useState<Evals | null>(null);
  const [busy, setBusy] = useState(false);

  async function onRun() {
    setBusy(true);
    try {
      setEvals(await runEvals(live));
    } finally {
      setBusy(false);
    }
  }

  const a = evals?.aggregates;

  return (
    <section>
      <div className="panel row spread">
        <div>
          <h2 style={{ margin: 0 }}>Evaluation &amp; release gates</h2>
          <p className="muted">
            Golden scenarios + labeled retrieval — the CI release gates.
          </p>
        </div>
        <button className="act" onClick={onRun} disabled={busy}>Run evaluation</button>
      </div>

      {busy && !evals && (
        <div className="panel muted">Running golden scenarios + retrieval eval…</div>
      )}

      {evals && a && (
        <>
          <div className="panel">
            <h2>Golden scenarios</h2>
            <div className="row">
              <span className="stat"><b className="ok">{String(a.rca_precision_at_1)}</b><span>RCA precision@1</span></span>
              <span className="stat"><b className="ok">{String(a.escalation_correctness)}</b><span>escalation correctness</span></span>
              <span className="stat"><b className="ok">{String(a.citation_faithfulness)}</b><span>citation faithfulness</span></span>
              <span className="stat"><b>{String(a.mean_calibration_error)}</b><span>mean calibration err</span></span>
              <span className="stat">
                <b className={a.all_audit_chains_verified ? "ok" : "warn"}>
                  {a.all_audit_chains_verified ? "✓" : "✕"}
                </b>
                <span>audit chains verified</span>
              </span>
            </div>
          </div>

          <div className="panel">
            <h2>Retrieval quality (per chunking strategy)</h2>
            <table>
              <thead>
                <tr><th>strategy</th><th>chunks</th><th>P@1</th><th>R@5</th><th>MRR</th></tr>
              </thead>
              <tbody>
                {Object.entries(evals.retrieval).map(([name, m]) => (
                  <tr key={name}>
                    <td>{name}</td><td>{m.chunks}</td>
                    <td><code>{m.precision_at_1}</code></td>
                    <td><code>{m.recall_at_5}</code></td>
                    <td><code>{m.mrr}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h2>Trust ladder (per failure class)</h2>
            <table>
              <thead>
                <tr><th>class</th><th>episodes</th><th>precision</th><th>stage</th></tr>
              </thead>
              <tbody>
                {Object.entries(evals.trust_ladder).map(([name, v]) => (
                  <tr key={name}>
                    <td><code>{name}</code></td><td>{v.episodes}</td>
                    <td>{v.precision}</td><td>{v.stage}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h2>Release gate</h2>
            <p>
              <code>{evals.release_gate.criterion}</code> →{" "}
              <b className={evals.release_gate.passed ? "ok" : "warn"}>
                {evals.release_gate.passed ? "PASSED" : "FAILED"}
              </b>
            </p>
          </div>
        </>
      )}
    </section>
  );
}
