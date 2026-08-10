import { useEffect, useState } from "react";
import type { Agent, Evidence, Incident, PlanStep } from "../types";
import { decide, listScenarios, triggerIncident } from "../api";
import type { ScenarioInfo } from "../api";

const confColor = (c: number): string =>
  c >= 0.8 ? "var(--green)" : c >= 0.5 ? "var(--amber)" : "var(--red)";

function AgentPipeline({ agents }: { agents: Agent[] }) {
  return (
    <div className="agents">
      {agents.map((a) => (
        <span className="chip" key={a.name}>
          <span className="dot" style={{ background: confColor(a.confidence) }} />
          {a.name}
          <b style={{ color: confColor(a.confidence) }}>{a.confidence.toFixed(2)}</b>
          <span style={{ color: "var(--dim)" }}>
            {a.model === "n/a" ? "deterministic" : a.model}
          </span>
        </span>
      ))}
    </div>
  );
}

function classBadge(c: string) {
  if (c === "CONFIDENTIAL") return <span className="rk MEDIUM">CONF</span>;
  if (c === "QUARANTINED") return <span className="rk HIGH">QUAR</span>;
  return <span className="pill">INT</span>;
}

function EvidenceTable({ evidence }: { evidence: Evidence[] }) {
  return (
    <table>
      <thead>
        <tr><th>#</th><th>kind</th><th>source</th><th>summary</th><th>class</th></tr>
      </thead>
      <tbody>
        {evidence.map((e, i) => (
          <tr key={i}>
            <td>E{i + 1}</td>
            <td>{e.kind}</td>
            <td>{e.source}</td>
            <td>{e.summary} <code>{e.ref}</code></td>
            <td>{classBadge(e.classification)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PlanTable({ plan }: { plan: PlanStep[] }) {
  return (
    <table>
      <thead>
        <tr><th>action</th><th>risk</th><th>args</th></tr>
      </thead>
      <tbody>
        {plan.map((s, i) => (
          <tr key={i}>
            <td><code>{s.action}</code></td>
            <td><span className={`rk ${s.risk}`}>{s.risk}</span></td>
            <td className="muted">{JSON.stringify(s.args)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function IncidentTab({ live }: { live: boolean }) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [resolved, setResolved] = useState<Partial<Incident> | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [selected, setSelected] = useState("");

  useEffect(() => {
    if (live) listScenarios().then(setScenarios).catch(() => setScenarios([]));
  }, [live]);

  async function onTrigger() {
    setBusy(true);
    setError(null);
    setResolved(null);
    setDenied(false);
    try {
      setIncident(await triggerIncident(live, selected || undefined));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDecide(approve: boolean) {
    if (!incident) return;
    setBusy(true);
    try {
      const result = await decide(live, incident, approve);
      if (approve) setResolved(result);
      else setDenied(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const agents = resolved?.agents ?? incident?.agents ?? [];
  const tokens = resolved?.tokens ?? incident?.tokens ?? 0;
  const cost = resolved?.est_cost_usd ?? incident?.est_cost_usd ?? 0;

  return (
    <section>
      <div className="panel row spread">
        <div>
          <h2 style={{ margin: 0 }}>Autonomous incident remediation</h2>
          <p className="muted">
            End to end: gather → screen → diagnose → plan → gate → execute →
            verify → learn.
            {live && scenarios.length > 0 &&
              " Pick a scenario — the adversarial ones escalate instead of remediating."}
          </p>
        </div>
        <div className="row">
          {live && scenarios.length > 0 && (
            <select
              className="q"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">canonical (default)</option>
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — {s.expected_outcome}
                </option>
              ))}
            </select>
          )}
          <button className="act" onClick={onTrigger} disabled={busy}>
            ▶ Trigger incident
          </button>
        </div>
      </div>

      {error && <div className="panel" style={{ color: "var(--red)" }}>Error: {error}</div>}
      {busy && !incident && <div className="panel muted">Running pipeline…</div>}

      {incident && (
        <>
          <div className="panel">
            <h2>Agent pipeline</h2>
            <AgentPipeline agents={agents} />
            <div className="row">
              <span className="stat"><b>{incident.evidence_count}</b><span>evidence</span></span>
              <span className="stat"><b>{tokens}</b><span>model tokens</span></span>
              <span className="stat"><b>${cost.toFixed(4)}</b><span>est. prod cost</span></span>
            </div>
          </div>

          <div className="panel">
            <h2>Evidence bundle (cited · redacted at the gateway)</h2>
            <EvidenceTable evidence={incident.evidence} />
          </div>

          {incident.diagnosis && (
            <div className="panel">
              <h2>
                Diagnosis · {incident.diagnosis.failure_class} ·{" "}
                <span style={{ color: confColor(incident.diagnosis.confidence) }}>
                  confidence {incident.diagnosis.confidence}
                </span>
              </h2>
              <div className="hyp">{incident.hypothesis}</div>
            </div>
          )}

          {incident.plan && (
            <div className="panel">
              <h2>Remediation plan (Step Catalog actions only)</h2>
              <PlanTable plan={incident.plan} />
            </div>
          )}

          {resolved ? (
            <div className="panel">
              <div className="gate ok">
                <b className="ok">✓ Approved — executed, verified, learned.</b>
                <p className="muted" style={{ marginTop: ".3rem" }}>
                  Recovery verified; incident episode written to memory.
                </p>
              </div>
            </div>
          ) : denied ? (
            <div className="panel">
              <div className="gate no">
                <b style={{ color: "var(--red)" }}>
                  ✕ Denied — escalated to a human. Nothing executed.
                </b>
              </div>
            </div>
          ) : incident.pending_gate ? (
            <div className="panel">
              <div className="gate">
                <b className="warn">⏸ PAUSED at approval gate — tier {incident.approval_tier}</b>
                <p className="muted" style={{ margin: ".3rem 0 .7rem" }}>
                  HIGH-risk write in prod requires human approval. Nothing has executed.
                </p>
                <div className="row">
                  <button className="act approve" onClick={() => onDecide(true)} disabled={busy}>
                    ✓ Approve rollback
                  </button>
                  <button className="act deny" onClick={() => onDecide(false)} disabled={busy}>
                    ✕ Deny
                  </button>
                </div>
              </div>
            </div>
          ) : incident.status === "FAILED" ? (
            <div className="panel">
              <div className="gate no">
                <b style={{ color: "var(--red)" }}>
                  ⇱ Escalated — the platform refused to auto-remediate.
                </b>
                <p className="muted" style={{ marginTop: ".3rem" }}>{incident.error}</p>
              </div>
            </div>
          ) : null}

          {resolved?.postmortem_excerpt && (
            <div className="panel">
              <h2>Generated postmortem</h2>
              <pre>{resolved.postmortem_excerpt}</pre>
              <h2 style={{ marginTop: ".8rem" }}>Follow-ups (derived)</h2>
              <ul className="fu">
                {(resolved.follow_ups ?? []).map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
