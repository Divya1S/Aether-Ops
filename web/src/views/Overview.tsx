import { useState } from "react";
import { STORM_SIZE } from "../api";
import {
  Button,
  Card,
  Empty,
  ErrorNote,
  Loading,
  OutcomeBadge,
  outcomeLabel,
  PageHead,
  SeverityBadge,
  Stat,
  useApp,
  useAsync,
} from "../components/ui";
import { ago, compact, duration, failureClass, percent, usd } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { Outcome, Stats } from "../types";
import { markFresh } from "./IncidentDetail";

// Outcome is a state, so the split uses the reserved status colours — each
// always paired with its label and count in the legend below the bar.
const OUTCOME_FILL: Record<string, { color: string; darkInk?: boolean }> = {
  remediated: { color: "var(--good)" },
  "awaiting-approval": { color: "var(--warn)", darkInk: true },
  escalated: { color: "var(--serious)", darkInk: true },
  denied: { color: "var(--fg-dim)" },
  failed: { color: "var(--crit)" },
  running: { color: "var(--accent)" },
};
const OUTCOME_ORDER = ["remediated", "awaiting-approval", "escalated", "denied", "failed", "running"];

function OutcomeBar({ stats }: { stats: Stats }) {
  const total = stats.incidents.total;
  const parts = OUTCOME_ORDER.map((key) => ({ key, count: stats.incidents.by_outcome[key] ?? 0 })).filter(
    (part) => part.count > 0,
  );
  const summary = parts.map((p) => `${outcomeLabel(p.key as Outcome)} ${p.count}`).join(", ");
  return (
    <div>
      <div className="segbar" role="img" aria-label={`Incident outcomes: ${summary}`}>
        {parts.map((part) => {
          const share = part.count / total;
          return (
            <div
              key={part.key}
              className={`seg ${OUTCOME_FILL[part.key]?.darkInk ? "dark-ink" : ""}`}
              style={{ flex: `${part.count} 0 0`, background: OUTCOME_FILL[part.key]?.color }}
              title={`${outcomeLabel(part.key as Outcome)}: ${part.count} (${percent(share)})`}
            >
              {share >= 0.12 ? percent(share) : ""}
            </div>
          );
        })}
      </div>
      <div className="legend">
        {parts.map((part) => (
          <span className="legend-item" key={part.key}>
            <span className="swatch" style={{ background: OUTCOME_FILL[part.key]?.color }} />
            {outcomeLabel(part.key as Outcome)} · {part.count}
          </span>
        ))}
      </div>
    </div>
  );
}

function Funnel({ stats }: { stats: Stats }) {
  const alerts = stats.alerts;
  const rows = [
    { label: "Raw events received", value: alerts.received },
    { label: "After fingerprint dedup", value: alerts.unique_alerts },
    { label: "Correlated groups", value: alerts.groups },
  ];
  const max = Math.max(1, alerts.received);
  return (
    <div className="meters">
      {rows.map((row) => (
        <FunnelRow key={row.label} label={row.label} value={row.value} share={row.value / max} />
      ))}
    </div>
  );
}

function FunnelRow({ label, value, share }: { label: string; value: number; share: number }) {
  return (
    <>
      <span className="meter-name">{label}</span>
      <span className="meter-track" role="img" aria-label={`${label}: ${value}`}>
        <span className="meter-fill" style={{ display: "block", width: `${Math.max(share * 100, value ? 2 : 0)}%` }} />
      </span>
      <span className="meter-value">{value}</span>
    </>
  );
}

export function Overview() {
  const { client, can, notify } = useApp();
  const stats = useAsync(() => client.stats(), [client]);
  const recent = useAsync(() => client.listIncidents(), [client]);
  const [busy, setBusy] = useState<string | null>(null);

  async function storm(): Promise<void> {
    setBusy("storm");
    try {
      await client.ingestStorm();
      navigate("alerts");
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      setBusy(null);
    }
  }

  async function canonical(): Promise<void> {
    setBusy("incident");
    try {
      const incident = await client.createIncident();
      markFresh(incident.incident_id);
      navigate("incidents", incident.incident_id);
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      setBusy(null);
    }
  }

  if (stats.error) return <ErrorNote error={stats.error} retry={stats.reload} />;
  if (!stats.data) return <Loading />;
  const s = stats.data;
  const idle = s.incidents.total === 0 && s.alerts.received === 0;
  const waiting = s.incidents.by_outcome["awaiting-approval"] ?? 0;

  return (
    <>
      <PageHead
        title="Overview"
        actions={
          <Button icon="refresh" variant="quiet" onClick={() => { stats.reload(); recent.reload(); }}>
            Refresh
          </Button>
        }
      >
        Alert in, verified remediation out — with a human at the gate and a citation behind every claim.
      </PageHead>

      <div className={`stack ${stats.loading ? "stale" : ""}`}>
        {idle && (
          <Card title="Start here">
            <div className="guide">
              <div className="guide-step">
                <h3>Send an alert storm</h3>
                <p>
                  {STORM_SIZE} raw events from three monitoring sources. Watch dedup and topology correlation collapse
                  them into the few things worth a human's attention.
                </p>
              </div>
              <div className="guide-step">
                <h3>Investigate a group</h3>
                <p>
                  Agents gather cited evidence and the hypothesis tree shows which causes were validated, ruled out, or
                  left open.
                </p>
              </div>
              <div className="guide-step">
                <h3>Decide at the gate</h3>
                <p>
                  Nothing writes to production without approval. Approve the rollback, then read the generated
                  postmortem and the audit ledger.
                </p>
              </div>
            </div>
            <div className="row" style={{ marginTop: "0.9rem" }}>
              <Button variant="primary" icon="zap" busy={busy === "storm"} disabled={!can("ingest") || busy !== null} onClick={() => void storm()}>
                Simulate an alert storm
              </Button>
              <Button icon="play" busy={busy === "incident"} disabled={!can("create") || busy !== null} onClick={() => void canonical()}>
                Or run the canonical incident
              </Button>
            </div>
          </Card>
        )}

        <div className="stats">
          <Stat label="Incidents" value={s.incidents.total} hint={`${s.memory.episodes} episode(s) in memory`} />
          <Stat
            label="Awaiting approval"
            value={waiting}
            hint={waiting ? <a href={href("incidents")}>review at the gate →</a> : "no gate is waiting"}
          />
          <Stat label="Grounded diagnoses" value={percent(s.diagnosis_rate)} hint="the rest were escalated" />
          <Stat label="Mean time to diagnosis" value={duration(s.mean_time_to_diagnosis_ms)} hint="alert to root cause" />
          <Stat
            label="Alert noise removed"
            value={s.alerts.received ? percent(s.alerts.noise_reduction) : "—"}
            hint={`${s.alerts.received} events → ${s.alerts.groups} groups`}
          />
          <Stat label="Model spend" value={usd(s.est_cost_usd)} hint={`${compact(s.tokens)} tokens, production-priced`} />
        </div>

        <div className="grid half">
          <Card title="Incident outcomes">
            {s.incidents.total ? (
              <OutcomeBar stats={s} />
            ) : (
              <p className="muted">No incidents yet. Outcomes appear here as investigations finish.</p>
            )}
          </Card>
          <Card title="Alert funnel" foot={<a href={href("alerts")}>Open the alert inbox →</a>}>
            {s.alerts.received ? (
              <Funnel stats={s} />
            ) : (
              <p className="muted">No alerts ingested. Point Alertmanager at POST /v1/alerts, or simulate a storm.</p>
            )}
          </Card>
        </div>

        <div className="grid two">
          <Card title="Recent incidents" flush foot={<a href={href("incidents")}>All incidents →</a>}>
            {recent.data?.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Incident</th>
                      <th>Outcome</th>
                      <th>Diagnosis</th>
                      <th>Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.data.slice(0, 6).map((row) => (
                      <tr key={row.incident_id} className="clickable" onClick={() => navigate("incidents", row.incident_id)}>
                        <td>
                          <a href={href("incidents", row.incident_id)} onClick={(event) => event.stopPropagation()}>
                            {row.title}
                          </a>
                          <div className="row tight" style={{ marginTop: "0.15rem" }}>
                            <SeverityBadge severity={row.severity} />
                            <span className="dim small mono">{row.service}</span>
                          </div>
                        </td>
                        <td>
                          <OutcomeBadge outcome={row.outcome} />
                        </td>
                        <td className="small">{failureClass(row.failure_class)}</td>
                        <td className="dim small nowrap">{ago(row.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty icon="incidents" title="No incidents yet">
                Investigations you start appear here.
              </Empty>
            )}
          </Card>

          <Card title="Diagnosed failure classes" flush>
            {Object.keys(s.incidents.by_failure_class).length ? (
              <table>
                <thead>
                  <tr>
                    <th>Class</th>
                    <th className="num">Incidents</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(s.incidents.by_failure_class).map(([name, count]) => (
                    <tr key={name}>
                      <td>
                        {failureClass(name)}
                        <div className="dim small mono">{name}</div>
                      </td>
                      <td className="num">{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted" style={{ padding: "0.3rem 1rem 0.8rem" }}>
                Nothing diagnosed yet.
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
