import { useState } from "react";
import { STORM_SIZE } from "../api";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  Icon,
  Loading,
  PageHead,
  SeverityBadge,
  Stat,
  useApp,
  useAsync,
} from "../components/ui";
import { ago, percent } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { AlertGroup } from "../types";
import { markFresh } from "./IncidentDetail";

const message = (reason: unknown): string => (reason instanceof Error ? reason.message : String(reason));

function GroupCard({
  group,
  onInvestigate,
  busy,
  canCreate,
}: {
  group: AlertGroup;
  onInvestigate: (group: AlertGroup) => void;
  busy: boolean;
  canCreate: boolean;
}) {
  const [open, setOpen] = useState(false);
  const downstream = group.services.filter((service) => service !== group.origin);
  return (
    <section className="card">
      <div className="card-body">
        <div className="row spread" style={{ alignItems: "flex-start" }}>
          <div style={{ minWidth: 0 }}>
            <div className="row">
              <SeverityBadge severity={group.severity} />
              <strong>{group.title}</strong>
              {group.status === "investigating" && (
                <Badge tone="accent" icon="search">
                  Investigating
                </Badge>
              )}
              {group.status === "resolved" && (
                <Badge tone="good" icon="check">
                  Resolved
                </Badge>
              )}
            </div>
            <div className="row" style={{ marginTop: "0.5rem" }}>
              <span className="muted small">Probable origin</span>
              <Badge tone="accent" mono icon="flag">
                {group.origin}
              </Badge>
              {downstream.length > 0 && (
                <>
                  <span className="muted small">also alerting</span>
                  {downstream.map((service) => (
                    <span className="chip" key={service}>
                      {service}
                    </span>
                  ))}
                </>
              )}
            </div>
            <p className="muted small" style={{ marginTop: "0.5rem" }}>
              {group.events} raw event{group.events === 1 ? "" : "s"} → {group.unique_alerts} distinct alert
              {group.unique_alerts === 1 ? "" : "s"} from {group.sources.join(", ")} · last seen {ago(group.last_seen)}
            </p>
          </div>
          <div className="row">
            {group.incident_id ? (
              <a className="btn primary" href={href("incidents", group.incident_id)}>
                Open incident <Icon name="chevron" />
              </a>
            ) : group.investigable ? (
              <Button variant="primary" icon="search" busy={busy} disabled={!canCreate} onClick={() => onInvestigate(group)}>
                Investigate
              </Button>
            ) : (
              <span className="muted small aside-note">
                No evidence source for <b>{group.origin}</b> — connectors are snapshot fakes.
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          className="btn quiet sm"
          style={{ marginTop: "0.6rem" }}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <Icon name="chevron" className={`chev ${open ? "open" : ""}`} />
          {open ? "Hide" : "Show"} the {group.unique_alerts} alert{group.unique_alerts === 1 ? "" : "s"} and why each
          was grouped
        </button>
      </div>
      {open && (
        <div className="table-wrap" style={{ borderTop: "1px solid var(--border)" }}>
          <table>
            <thead>
              <tr>
                <th>Alert</th>
                <th>Service</th>
                <th>Source</th>
                <th className="num">Fired</th>
                <th>Why it is in this group</th>
              </tr>
            </thead>
            <tbody>
              {group.alerts.map((alert) => (
                <tr key={alert.id}>
                  <td>
                    <div className="row tight">
                      <SeverityBadge severity={alert.severity} />
                      {alert.name}
                    </div>
                    {alert.summary && <div className="muted small">{alert.summary}</div>}
                  </td>
                  <td className="mono small">{alert.service}</td>
                  <td className="small">{alert.source}</td>
                  <td className="num">×{alert.count}</td>
                  <td className="small muted">{alert.joined_because}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Alerts() {
  const { client, can, notify } = useApp();
  const { data, error, loading, reload, set } = useAsync(() => client.alerts(), [client]);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, action: () => Promise<void>): Promise<void> {
    setBusy(key);
    try {
      await action();
    } catch (reason) {
      notify(message(reason), "error");
      reload();
    } finally {
      setBusy(null);
    }
  }

  const storm = (): Promise<void> =>
    run("storm", async () => {
      set(await client.ingestStorm());
      notify(`${STORM_SIZE} events ingested.`, "success");
    });
  const clear = (): Promise<void> => run("clear", async () => set(await client.clearAlerts()));
  const investigate = (group: AlertGroup): Promise<void> =>
    run(group.id, async () => {
      const incident = await client.investigateGroup(group.id);
      markFresh(incident.incident_id);
      navigate("incidents", incident.incident_id);
    });

  if (error && !data) return <ErrorNote error={error} retry={reload} />;
  if (!data) return <Loading />;
  const { stats, groups } = data;

  return (
    <>
      <PageHead
        title="Alerts"
        actions={
          <>
            <Button variant="primary" icon="zap" busy={busy === "storm"} disabled={!can("ingest") || busy !== null} onClick={() => void storm()}>
              Simulate alert storm
            </Button>
            <Button icon="x" busy={busy === "clear"} disabled={!can("create") || busy !== null || !stats.received} onClick={() => void clear()}>
              Clear
            </Button>
          </>
        }
      >
        Raw events are deduplicated by fingerprint (source · service · alert name), then correlated along the service
        graph within a {Math.round(stats.window_s / 60)}-minute window. Each group names its probable origin: the most
        upstream service that is alerting.
      </PageHead>

      <div className={`stack ${loading ? "stale" : ""}`}>
        <div className="stats">
          <Stat label="Raw events" value={stats.received} hint="everything that paged" />
          <Stat label="Distinct alerts" value={stats.unique_alerts} hint={`${stats.deduplicated} re-fires folded in`} />
          <Stat label="Correlated groups" value={stats.groups} hint={`${stats.open_groups} open`} />
          <Stat
            label="Noise removed"
            value={stats.received ? percent(stats.noise_reduction) : "—"}
            hint="events you no longer triage"
          />
        </div>

        {groups.length === 0 ? (
          <Card>
            <Empty
              icon="alerts"
              title="The inbox is quiet"
              action={
                <Button variant="primary" icon="zap" busy={busy === "storm"} disabled={!can("ingest") || busy !== null} onClick={() => void storm()}>
                  Simulate a {STORM_SIZE}-event storm
                </Button>
              }
            >
              Send alerts to <code>POST /v1/alerts</code> — the native shape or a Prometheus Alertmanager webhook, as-is
              — or simulate a storm to see dedup and correlation at work.
            </Empty>
          </Card>
        ) : (
          groups.map((group) => (
            <GroupCard
              key={group.id}
              group={group}
              busy={busy === group.id}
              canCreate={can("create") && busy === null}
              onInvestigate={(g) => void investigate(g)}
            />
          ))
        )}
      </div>
    </>
  );
}
