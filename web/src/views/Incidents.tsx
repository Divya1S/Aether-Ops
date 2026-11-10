import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  Loading,
  OutcomeBadge,
  outcomeLabel,
  PageHead,
  SeverityBadge,
  useApp,
  useAsync,
} from "../components/ui";
import { ago, duration, failureClass, percent } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { Outcome } from "../types";
import { markFresh } from "./IncidentDetail";

const FILTERS: (Outcome | "all")[] = ["all", "awaiting-approval", "remediated", "escalated", "denied"];

export function Incidents() {
  const { client, can, notify } = useApp();
  const rows = useAsync(() => client.listIncidents(), [client]);
  const scenarios = useAsync(() => client.scenarios(), [client]);
  const [scenario, setScenario] = useState("");
  const [filter, setFilter] = useState<Outcome | "all">("all");
  const [busy, setBusy] = useState(false);

  async function start(): Promise<void> {
    setBusy(true);
    try {
      const incident = await client.createIncident(scenario || undefined);
      markFresh(incident.incident_id);
      navigate("incidents", incident.incident_id);
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      setBusy(false);
    }
  }

  const all = rows.data ?? [];
  const shown = filter === "all" ? all : all.filter((row) => row.outcome === filter);
  const picked = scenarios.data?.find((s) => s.id === scenario);

  return (
    <>
      <PageHead title="Incidents">
        Every investigation the platform has run: what it concluded, how sure it was, and whether a human had to step
        in.
      </PageHead>

      <div className="stack">
        <Card title="New investigation">
          <div className="row" style={{ alignItems: "flex-end" }}>
            <label className="field" style={{ flex: "1 1 22rem" }}>
              Scenario (frozen evidence snapshot)
              <select className="select" value={scenario} onChange={(event) => setScenario(event.target.value)}>
                <option value="">Canonical SEV2 — checkout-service deploy regression</option>
                {(scenarios.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <Button variant="primary" icon="play" busy={busy} disabled={!can("create")} onClick={() => void start()}>
              Run investigation
            </Button>
          </div>
          <p className="muted small" style={{ marginTop: "0.55rem" }}>
            {picked ? (
              <>
                Ground truth for this scenario: <b>{picked.expected_outcome}</b>.{" "}
                {picked.expected_outcome === "escalated"
                  ? "It is adversarial — the right answer is to refuse to act."
                  : "The platform should diagnose it, then stop at the approval gate."}
              </>
            ) : (
              "Connectors serve frozen evidence by default, so each scenario replays one real-shaped incident end to end."
            )}
            {!can("create") && " Your role cannot start investigations."}
          </p>
        </Card>

        <Card
          title={`History · ${all.length}`}
          flush
          actions={FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              className={`chip ${filter === option ? "active" : ""}`}
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
            >
              {option === "all" ? "All" : outcomeLabel(option)}
            </button>
          ))}
        >
          {rows.error ? (
            <div style={{ padding: "0 1rem 1rem" }}>
              <ErrorNote error={rows.error} retry={rows.reload} />
            </div>
          ) : !rows.data ? (
            <Loading />
          ) : shown.length === 0 ? (
            <Empty icon="incidents" title={all.length ? "Nothing matches that filter" : "No incidents yet"}>
              {all.length ? "Try another outcome." : "Run an investigation above, or start from an alert group."}
            </Empty>
          ) : (
            <div className={`table-wrap ${rows.loading ? "stale" : ""}`}>
              <table>
                <thead>
                  <tr>
                    <th>Incident</th>
                    <th>Outcome</th>
                    <th>Diagnosis</th>
                    <th className="num">Confidence</th>
                    <th className="num">To diagnosis</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((row) => (
                    <tr key={row.incident_id} className="clickable" onClick={() => navigate("incidents", row.incident_id)}>
                      <td>
                        <a href={href("incidents", row.incident_id)} onClick={(event) => event.stopPropagation()}>
                          {row.title}
                        </a>
                        <div className="row tight" style={{ marginTop: "0.15rem" }}>
                          <SeverityBadge severity={row.severity} />
                          <span className="dim small mono">{row.service}</span>
                          {row.archived && <Badge icon="lock">archived</Badge>}
                          {row.feedback && (
                            <Badge tone={row.feedback === "correct" ? "good" : "crit"} icon={row.feedback === "correct" ? "up" : "down"}>
                              {row.feedback}
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td>
                        <OutcomeBadge outcome={row.outcome} />
                      </td>
                      <td className="small">{failureClass(row.failure_class)}</td>
                      <td className="num">{row.failure_class ? percent(row.confidence) : "—"}</td>
                      <td className="num">{duration(row.time_to_diagnosis_ms)}</td>
                      <td className="dim small nowrap">{ago(row.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
