import { useState } from "react";
import { TopologyGraph } from "../components/charts";
import { Badge, Button, Card, ErrorNote, Loading, PageHead, SeverityBadge, useApp, useAsync } from "../components/ui";
import { blastRadius, upstream } from "../lib/graph";
import { href } from "../lib/router";

export function TopologyView() {
  const { client } = useApp();
  const { data, error, loading, reload } = useAsync(() => client.topology(), [client]);
  const [selected, setSelected] = useState<string | null>(null);

  if (error && !data) return <ErrorNote error={error} retry={reload} />;
  if (!data) return <Loading />;

  const node = data.nodes.find((n) => n.id === selected) ?? null;
  const blast = selected ? blastRadius(data, selected) : [];
  const up = selected ? upstream(data, selected) : [];
  const alerting = data.nodes.filter((n) => n.alerts);

  return (
    <>
      <PageHead
        title="Topology"
        actions={
          <Button icon="refresh" variant="quiet" onClick={reload}>
            Refresh
          </Button>
        }
      >
        The declared service graph: an arrow points from a caller to what it calls. Select a service to see its blast
        radius — everything that degrades if it does — which is what alert correlation and change-risk scoring both
        read.
      </PageHead>

      <div className={`grid two ${loading ? "stale" : ""}`}>
        <Card
          title="Service map"
          foot={
            <span className="legend" style={{ marginTop: 0 }}>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--accent)" }} /> selected
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--warn)" }} /> blast radius (its callers)
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--accent-line)" }} /> its dependencies
              </span>
              <span className="legend-item">
                <span className="swatch" style={{ background: "var(--crit)", borderRadius: "50%" }} /> alerting now
              </span>
            </span>
          }
        >
          <TopologyGraph topology={data} selected={selected} blast={blast} upstream={up} onSelect={(id) => setSelected(id === selected ? null : id)} />
        </Card>

        <div className="stack">
          <Card title={node ? node.id : "Select a service"}>
            {node ? (
              <div className="stack" style={{ gap: "0.85rem" }}>
                {!node.known && (
                  <p className="muted small">
                    This service is alerting but is not in the declared graph, so the platform claims no dependencies
                    for it and correlates its alerts only with each other.
                  </p>
                )}
                {node.alerts && (
                  <div className="row">
                    <SeverityBadge severity={node.alerts.severity} />
                    <span>
                      {node.alerts.alerts} open alert{node.alerts.alerts === 1 ? "" : "s"}
                    </span>
                    {node.alerts.origin && (
                      <Badge tone="accent" icon="flag">
                        probable origin
                      </Badge>
                    )}
                  </div>
                )}
                {node.incidents.length > 0 && (
                  <div className="row">
                    <span className="muted small">Open incident</span>
                    {node.incidents.map((id) => (
                      <a key={id} className="chip" href={href("incidents", id)}>
                        {id}
                      </a>
                    ))}
                  </div>
                )}
                <ServiceList title={`Blast radius · ${blast.length}`} empty="Nothing calls it." items={blast} />
                <ServiceList title={`Depends on · ${up.length}`} empty="It calls nothing else." items={up} />
              </div>
            ) : (
              <p className="muted">
                {data.nodes.length} services, {data.edges.length} dependencies. Click a node, or tab to it and press
                Enter.
              </p>
            )}
          </Card>

          <Card title="Alerting now">
            {alerting.length ? (
              <ul className="list-plain">
                {alerting.map((n) => (
                  <li key={n.id} className="row spread">
                    <button type="button" className="chip" onClick={() => setSelected(n.id)}>
                      {n.id}
                    </button>
                    <span className="row tight">
                      {n.alerts?.origin && <Badge tone="accent">origin</Badge>}
                      <SeverityBadge severity={n.alerts?.severity ?? "info"} />
                      <span className="dim small">×{n.alerts?.alerts}</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">
                No open alerts. <a href={href("alerts")}>Simulate a storm</a> to see pressure on the map.
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function ServiceList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div>
      <div className="card-title" style={{ marginBottom: "0.35rem" }}>
        {title}
      </div>
      {items.length ? (
        <div className="row tight">
          {items.map((service) => (
            <span className="chip" key={service}>
              {service}
            </span>
          ))}
        </div>
      ) : (
        <span className="muted small">{empty}</span>
      )}
    </div>
  );
}
