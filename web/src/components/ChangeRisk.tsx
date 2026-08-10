import { useState } from "react";
import type { Change } from "../types";
import { CHANGE_BODIES, scoreChange } from "../api";

const bandColor = (b: string): string =>
  b === "HIGH" ? "var(--red)" : b === "MEDIUM" ? "var(--amber)" : "var(--green)";

export function ChangeTab({ live }: { live: boolean }) {
  const [state, setState] = useState<{ kind: "risky" | "benign"; change: Change } | null>(null);
  const [busy, setBusy] = useState(false);

  async function onScore(kind: "risky" | "benign") {
    setBusy(true);
    try {
      setState({ kind, change: await scoreChange(live, kind) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="panel">
        <h2>Pre-deploy change-risk scoring</h2>
        <p className="muted">
          Every change is scored against incident history, blast radius, and the
          deploy window. The platform has already learned from the checkout
          incident — watch the two changes score differently.
        </p>
        <div className="row">
          <button className="act" onClick={() => onScore("risky")} disabled={busy}>
            Score: raise DB pool 25→250
          </button>
          <button className="act ghost" onClick={() => onScore("benign")} disabled={busy}>
            Score: update README copy
          </button>
        </div>
      </div>
      {state && (
        <div className="panel">
          <h2>{CHANGE_BODIES[state.kind].title}</h2>
          <div className="row">
            <span className="stat">
              <b style={{ color: bandColor(state.change.band) }}>{state.change.band}</b>
              <span>risk band</span>
            </span>
            <span className="stat"><b>{state.change.score}/100</b><span>score</span></span>
            <span className="stat">
              <b>{state.change.canary_required ? "yes" : "no"}</b>
              <span>canary required</span>
            </span>
          </div>
          <table>
            <thead>
              <tr><th>failure signature</th><th>blast radius</th><th>deploy window</th><th>service history</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>{state.change.components.failure_signature}</td>
                <td>{state.change.components.blast_radius}</td>
                <td>{state.change.components.deploy_window}</td>
                <td>{state.change.components.service_history}</td>
              </tr>
            </tbody>
          </table>
          <p className="muted" style={{ marginTop: ".5rem" }}>
            Deterministic weights, published thresholds (HIGH≥70, MEDIUM≥40). The
            pool-raise matches a prior incident's failure signature; the copy
            change doesn't.
          </p>
        </div>
      )}
    </section>
  );
}
