// Charts are hand-rolled SVG: the console ships as one CSP-locked file, so a
// charting library would be both a large dependency and inline-script risk.
// Marks follow one spec — 2px lines, >=8px markers with a surface ring,
// solid hairline grid, labels in ink (never the series colour).
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { hhmm, minuteOfDay, niceTicks } from "../lib/format";
import { layout, NODE_H, NODE_W } from "../lib/graph";
import type { Signals, Topology } from "../types";
import { Button } from "./ui";

/** The rendered width of an element, tracked across resizes. Charts draw at
 *  their real pixel size instead of scaling a fixed viewBox, so 11px axis
 *  text is 11px in a narrow column and in a wide one. */
function useWidth(fallback: number): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = (): void => setWidth(Math.max(280, Math.round(element.clientWidth)));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// ───────────────────────── signal chart ─────────────────────────
const H = 240;
const M = { left: 44, right: 14, top: 34, bottom: 44 };

interface Tip {
  x: number;
  y: number;
  value: string;
  lines: string[];
}

export function SignalChart({ signals }: { signals: Signals }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const [table, setTable] = useState(false);
  const [frame, W] = useWidth(520);

  const model = useMemo(() => {
    const points = signals.series
      .map((p) => ({ minute: minuteOfDay(p.ts), value: p.value, ts: p.ts }))
      .filter((p): p is { minute: number; value: number; ts: string } => p.minute !== null);
    const deployMinute = minuteOfDay(signals.deploy?.ts);
    const events = signals.events
      .map((e) => ({ ...e, minute: minuteOfDay(e.ts) }))
      .filter((e): e is typeof e & { minute: number } => e.minute !== null);
    const minutes = [
      ...points.map((p) => p.minute),
      ...events.map((e) => e.minute),
      ...(deployMinute !== null ? [deployMinute] : []),
    ];
    const x0 = Math.min(...minutes) - 1;
    const x1 = Math.max(...minutes) + 1;
    const ticks = niceTicks(Math.max(...points.map((p) => p.value)) * 1.08);
    const yMax = ticks[ticks.length - 1];
    const sx = (minute: number): number => M.left + ((minute - x0) / (x1 - x0)) * (W - M.left - M.right);
    const sy = (value: number): number => H - M.bottom - (value / yMax) * (H - M.top - M.bottom);
    return { points, deployMinute, events, ticks, sx, sy, baseline: points[0]?.value ?? 0 };
  }, [signals, W]);

  const { points, deployMinute, events, ticks, sx, sy, baseline } = model;
  if (points.length < 2) return null;

  const path = points.map((p, i) => `${i ? "L" : "M"}${sx(p.minute).toFixed(1)},${sy(p.value).toFixed(1)}`).join(" ");
  const floor = H - M.bottom;
  const area = `${path} L${sx(points[points.length - 1].minute).toFixed(1)},${floor} L${sx(points[0].minute).toFixed(1)},${floor} Z`;
  const onsetMinute = minuteOfDay(signals.onset);
  const threshold = baseline * 2;
  const last = points[points.length - 1];
  const reasonCounts = events.reduce<Record<string, number>>((acc, e) => {
    acc[e.reason] = (acc[e.reason] ?? 0) + 1;
    return acc;
  }, {});

  const show = (x: number, y: number, value: string, lines: string[]) => (): void =>
    setTip({ x, y, value, lines });
  const hide = (): void => setTip(null);

  return (
    <div ref={frame}>
      <div className="row spread" style={{ marginBottom: "0.4rem", flexWrap: "nowrap", alignItems: "flex-start" }}>
        <span className="muted small">
          {signals.metric} ({signals.unit}), with the deploy and pod events on the same clock
        </span>
        <Button variant="quiet sm" onClick={() => setTable(!table)} aria-pressed={table}>
          {table ? "Chart" : "Table"}
        </Button>
      </div>

      {table ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>What</th>
                <th className="num">Value</th>
              </tr>
            </thead>
            <tbody>
              {[
                ...points.map((p) => ({
                  minute: p.minute,
                  what: `${signals.metric}${p.minute === onsetMinute ? " — first breach" : ""}`,
                  value: `${p.value.toLocaleString("en-US")} ${signals.unit}`,
                })),
                ...(deployMinute !== null && signals.deploy
                  ? [{ minute: deployMinute, what: `deploy ${signals.deploy.revision}`, value: "—" }]
                  : []),
                ...events.map((e) => ({ minute: e.minute, what: `${e.reason} · ${e.pod}`, value: "—" })),
              ]
                .sort((a, b) => a.minute - b.minute)
                .map((row, i) => (
                  <tr key={i}>
                    <td className="mono">{hhmm(row.minute)}</td>
                    <td>{row.what}</td>
                    <td className="num">{row.value}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="viz" onMouseLeave={hide}>
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${signals.metric} over the incident window`}>
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={M.left} x2={W - M.right} y1={sy(tick)} y2={sy(tick)} stroke="var(--grid)" strokeWidth={1} />
                <text className="tick" x={M.left - 8} y={sy(tick) + 3.5} textAnchor="end">
                  {tick.toLocaleString("en-US")}
                </text>
              </g>
            ))}
            <line x1={M.left} x2={W - M.right} y1={floor} y2={floor} stroke="var(--axis)" strokeWidth={1} />

            {threshold > 0 && sy(threshold) > M.top && (
              <g>
                <line
                  x1={M.left}
                  x2={W - M.right}
                  y1={sy(threshold)}
                  y2={sy(threshold)}
                  stroke="var(--axis)"
                  strokeWidth={1}
                />
                <text className="label" x={W - M.right} y={sy(threshold) - 5} textAnchor="end">
                  2× baseline · {threshold.toLocaleString("en-US")} {signals.unit}
                </text>
              </g>
            )}

            {deployMinute !== null && signals.deploy && (
              <g>
                <line
                  x1={sx(deployMinute)}
                  x2={sx(deployMinute)}
                  y1={M.top - 6}
                  y2={floor}
                  stroke="var(--fg-muted)"
                  strokeWidth={1.5}
                />
                <text
                  className="label strong"
                  x={sx(deployMinute) + (sx(deployMinute) > W * 0.6 ? -6 : 6)}
                  y={M.top - 12}
                  textAnchor={sx(deployMinute) > W * 0.6 ? "end" : "start"}
                >
                  deploy {signals.deploy.revision} · {hhmm(deployMinute)}
                </text>
              </g>
            )}

            <path d={area} fill="var(--series-1)" opacity={0.1} />
            <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

            {points.map((p) => {
              const cx = sx(p.minute);
              const cy = sy(p.value);
              const isOnset = p.minute === onsetMinute;
              const label = `${p.value.toLocaleString("en-US")} ${signals.unit}`;
              const detail = [hhmm(p.minute), isOnset ? "first breach of 2× baseline" : signals.metric];
              return (
                <g key={p.ts}>
                  <text className="tick" x={cx} y={floor + 30} textAnchor="middle">
                    {hhmm(p.minute)}
                  </text>
                  <circle cx={cx} cy={cy} r={isOnset ? 5.5 : 4.5} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
                  {isOnset && (
                    <text className="label strong" x={cx - 9} y={cy - 9} textAnchor="end">
                      first breach
                    </text>
                  )}
                  <circle
                    cx={cx}
                    cy={cy}
                    r={14}
                    fill="transparent"
                    tabIndex={0}
                    role="img"
                    aria-label={`${hhmm(p.minute)}: ${label}${isOnset ? ", first breach" : ""}`}
                    onMouseEnter={show(cx, cy, label, detail)}
                    onFocus={show(cx, cy, label, detail)}
                    onBlur={hide}
                  />
                </g>
              );
            })}
            <text className="label strong" x={sx(last.minute) - 9} y={sy(last.value) + 16} textAnchor="end">
              {last.value.toLocaleString("en-US")} {signals.unit}
            </text>

            {events.map((e, i) => {
              const cx = sx(e.minute);
              const cy = floor + 9;
              return (
                <g key={i}>
                  <path
                    d={`M${cx},${cy - 5} L${cx + 5},${cy + 4} L${cx - 5},${cy + 4} Z`}
                    fill="var(--crit)"
                    stroke="var(--surface-1)"
                    strokeWidth={1.5}
                  />
                  <circle
                    cx={cx}
                    cy={cy}
                    r={12}
                    fill="transparent"
                    tabIndex={0}
                    role="img"
                    aria-label={`${hhmm(e.minute)}: ${e.reason} on ${e.pod}`}
                    onMouseEnter={show(cx, cy, e.reason, [hhmm(e.minute), e.pod])}
                    onFocus={show(cx, cy, e.reason, [hhmm(e.minute), e.pod])}
                    onBlur={hide}
                  />
                </g>
              );
            })}
          </svg>
          {tip && (
            <div className="viz-tip" style={{ left: tip.x, top: tip.y }}>
              <b>{tip.value}</b>
              {tip.lines.map((line) => (
                <span key={line}>{line}</span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="legend">
        <span className="legend-item">
          <span className="swatch line" style={{ background: "var(--series-1)" }} />
          {signals.metric}
        </span>
        {signals.deploy && (
          <span className="legend-item">
            <span className="swatch line" style={{ background: "var(--fg-muted)" }} />
            deploy
          </span>
        )}
        {events.length > 0 && (
          <span className="legend-item">
            <svg width="11" height="10" viewBox="0 0 11 10" aria-hidden="true">
              <path d="M5.5,0.5 L10.5,9.5 L0.5,9.5 Z" fill="var(--crit)" />
            </svg>
            pod events:{" "}
            {Object.entries(reasonCounts)
              .map(([reason, count]) => `${reason} ×${count}`)
              .join(", ")}
          </span>
        )}
        {signals.recovered && (
          <span className="legend-item">
            after remediation: <b style={{ color: "var(--fg)" }}>{signals.recovered.value} {signals.unit}</b>
          </span>
        )}
      </div>
    </div>
  );
}

// ───────────────────────── service map ─────────────────────────
export function TopologyGraph({
  topology,
  selected,
  blast,
  upstream,
  onSelect,
}: {
  topology: Topology;
  selected: string | null;
  blast: string[];
  upstream: string[];
  onSelect: (service: string) => void;
}) {
  const placed = useMemo(() => layout(topology), [topology]);
  const at = new Map(placed.nodes.map((n) => [n.id, n]));
  const inBlast = new Set(blast);
  const inUp = new Set(upstream);

  const edgeClass = (from: string, to: string): string => {
    if (!selected) return "";
    const hot = (to === selected || inBlast.has(to)) && inBlast.has(from);
    const cool = (from === selected || inUp.has(from)) && inUp.has(to);
    return hot ? "hot" : cool ? "cool" : "faded";
  };
  const nodeClass = (id: string, known: boolean): string =>
    [
      "topo-node",
      known ? "" : "unknown",
      id === selected ? "selected" : inBlast.has(id) ? "blast" : inUp.has(id) ? "upstream" : selected ? "faded" : "",
    ].join(" ");

  return (
    <div className="topo">
      <svg
        width={placed.width}
        height={placed.height}
        viewBox={`0 0 ${placed.width} ${placed.height}`}
        role="group"
        aria-label="Service dependency map"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0.5 L7,4 L0,7.5 Z" fill="var(--axis)" />
          </marker>
        </defs>
        {topology.edges.map((edge) => {
          const a = at.get(edge.from);
          const b = at.get(edge.to);
          if (!a || !b) return null;
          const y1 = a.y + NODE_H;
          const y2 = b.y - 3;
          let d: string;
          if (b.y - a.y > NODE_H * 2.5) {
            // Skips a row: leave from the right shoulder and bow out into
            // the gutter beside the column, so the edge never runs through
            // a node that sits between its two ends.
            const x1 = a.x + NODE_W - 22;
            const x2 = b.x + NODE_W - 22;
            const gutter = Math.max(a.x, b.x) + NODE_W + 36;
            d = `M${x1},${y1} C${gutter},${y1 + 46} ${gutter},${y2 - 46} ${x2},${y2}`;
          } else {
            const x1 = a.x + NODE_W / 2;
            const x2 = b.x + NODE_W / 2;
            const mid = (y1 + y2) / 2;
            d = `M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`;
          }
          return (
            <path
              key={`${edge.from}->${edge.to}`}
              className={`topo-edge ${edgeClass(edge.from, edge.to)}`}
              d={d}
              markerEnd="url(#arrow)"
            />
          );
        })}
        {placed.nodes.map((node) => {
          const alerts = node.alerts;
          const sub = alerts
            ? `${alerts.alerts} alert${alerts.alerts === 1 ? "" : "s"}${alerts.origin ? " · probable origin" : ""}`
            : node.known
              ? `${node.dependents} caller${node.dependents === 1 ? "" : "s"}`
              : "not in the service graph";
          return (
            <g
              key={node.id}
              className={nodeClass(node.id, node.known)}
              transform={`translate(${node.x},${node.y})`}
              tabIndex={0}
              role="button"
              aria-pressed={node.id === selected}
              aria-label={`${node.id}, ${sub}`}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(node.id);
                }
              }}
            >
              <rect width={NODE_W} height={NODE_H} rx={8} />
              {alerts && (
                <circle
                  cx={NODE_W - 14}
                  cy={14}
                  r={5}
                  fill={alerts.severity === "critical" ? "var(--crit)" : alerts.severity === "high" ? "var(--serious)" : "var(--warn)"}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
              )}
              <text x={12} y={22}>
                {node.id}
              </text>
              <text className="sub" x={12} y={38}>
                {sub}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
