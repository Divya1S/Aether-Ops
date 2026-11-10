import { describe, expect, it } from "vitest";
import type { Topology } from "../types";
import { ago, compact, duration, failureClass, hhmm, minuteOfDay, niceTicks, percent, usd } from "./format";
import { blastRadius, layout, NODE_H, NODE_W, upstream } from "./graph";
import { parseHash } from "./router";
import { buildStats } from "./stats";

describe("format", () => {
  it("compacts counts without losing small numbers", () => {
    expect(compact(0)).toBe("0");
    expect(compact(1284)).toBe("1,284");
    expect(compact(9999)).toBe("9,999");
    expect(compact(12_940)).toBe("12.9K");
    expect(compact(4_200_000)).toBe("4.2M");
  });

  it("renders durations in the smallest readable unit", () => {
    expect(duration(0.4)).toBe("0.4 ms");
    expect(duration(38.2)).toBe("38 ms");
    expect(duration(1200)).toBe("1.2 s");
    expect(duration(185_000)).toBe("3m 05s");
    expect(duration(null)).toBe("—");
    expect(duration(undefined)).toBe("—");
  });

  it("formats money and ratios, with a dash for the unknown", () => {
    expect(usd(0.013684)).toBe("$0.0137");
    expect(usd(2.5)).toBe("$2.50");
    expect(usd(0)).toBe("$0.00");
    expect(usd(null)).toBe("—");
    expect(percent(0.812, 1)).toBe("81.2%");
    expect(percent(1)).toBe("100%");
    expect(percent(null)).toBe("—");
  });

  it("describes elapsed time relative to an explicit clock", () => {
    const now = 1_800_000_000_000;
    expect(ago(1_800_000_000 - 5, now)).toBe("just now");
    expect(ago(1_800_000_000 - 240, now)).toBe("4m ago");
    expect(ago(1_800_000_000 - 3 * 3600, now)).toBe("3h ago");
    expect(ago(1_800_000_000 - 2 * 86_400, now)).toBe("2d ago");
    expect(ago(1_800_000_000 + 60, now)).toBe("just now"); // clock skew never goes negative
  });

  it("humanizes failure classes", () => {
    expect(failureClass("deploy-regression/memory")).toBe("Deploy regression · memory");
    expect(failureClass("cert-expiry/tls")).toBe("Cert expiry · tls");
    expect(failureClass(null)).toBe("—");
  });

  it("orders timestamps the way the Reviewer's temporal check does", () => {
    // Same contract as reasoning/signals.py:minute_of_day.
    expect(minuteOfDay("2026-08-07T14:02:00Z")).toBe(842);
    expect(minuteOfDay("14:05Z")).toBe(845);
    expect(minuteOfDay(null)).toBeNull();
    expect(minuteOfDay("no time here")).toBeNull();
    expect(minuteOfDay("2026-08-08T16:12:00Z")! > minuteOfDay("16:05Z")!).toBe(true);
    expect(hhmm(842)).toBe("14:02");
    expect(hhmm(5)).toBe("00:05");
  });

  it("picks clean axis ticks that cover the maximum", () => {
    expect(niceTicks(2412 * 1.08)).toEqual([0, 1000, 2000, 3000]);
    const small = niceTicks(171);
    expect(small[0]).toBe(0);
    expect(small[small.length - 1]).toBeGreaterThanOrEqual(171);
    expect(niceTicks(0)).toEqual([0, 1]);
  });
});

describe("router", () => {
  it("parses view and id from the hash", () => {
    expect(parseHash("")).toEqual({ view: "", id: null });
    expect(parseHash("#/")).toEqual({ view: "", id: null });
    expect(parseHash("#/alerts")).toEqual({ view: "alerts", id: null });
    expect(parseHash("#/incidents/inc_8866fd2be805")).toEqual({ view: "incidents", id: "inc_8866fd2be805" });
    expect(parseHash("#/incidents/a%2Fb")).toEqual({ view: "incidents", id: "a/b" });
  });
});

const node = (id: string, depth: number, known = true): Topology["nodes"][number] => ({
  id,
  known,
  depth,
  dependents: 0,
  dependencies: 0,
  alerts: null,
  incidents: [],
});

// The default service graph (graph/service_graph.py).
const TOPOLOGY: Topology = {
  nodes: [
    node("checkout-service", 1),
    node("mobile-bff", 2),
    node("orders-service", 1),
    node("payments-service", 0),
    node("search-api", 0),
    node("storefront-web", 2),
    node("payments-gateway", 0, false),
  ],
  edges: [
    { from: "checkout-service", to: "payments-service" },
    { from: "checkout-service", to: "search-api" },
    { from: "mobile-bff", to: "orders-service" },
    { from: "orders-service", to: "payments-service" },
    { from: "storefront-web", to: "checkout-service" },
    { from: "storefront-web", to: "orders-service" },
    { from: "storefront-web", to: "search-api" },
  ],
  focus: null,
};

describe("graph", () => {
  it("computes the same blast radius as the backend", () => {
    // tests/test_change_risk.py pins blast_radius(payments-service) == 4.
    expect(blastRadius(TOPOLOGY, "payments-service")).toEqual([
      "checkout-service",
      "mobile-bff",
      "orders-service",
      "storefront-web",
    ]);
    expect(blastRadius(TOPOLOGY, "orders-service")).toEqual(["mobile-bff", "storefront-web"]);
    expect(blastRadius(TOPOLOGY, "storefront-web")).toEqual([]);
    expect(blastRadius(TOPOLOGY, "not-a-service")).toEqual([]);
  });

  it("walks dependencies transitively", () => {
    expect(upstream(TOPOLOGY, "storefront-web")).toEqual([
      "checkout-service",
      "orders-service",
      "payments-service",
      "search-api",
    ]);
    expect(upstream(TOPOLOGY, "payments-service")).toEqual([]);
  });

  it("lays callers above what they call, with unknown services last", () => {
    const placed = layout(TOPOLOGY);
    const y = Object.fromEntries(placed.nodes.map((n) => [n.id, n.y]));
    for (const edge of TOPOLOGY.edges) expect(y[edge.from]).toBeLessThan(y[edge.to]);
    expect(y["payments-gateway"]).toBeGreaterThan(y["payments-service"]);
    expect(placed.nodes).toHaveLength(TOPOLOGY.nodes.length);
  });

  it("never overlaps two nodes and stays inside its canvas", () => {
    const placed = layout(TOPOLOGY);
    for (const a of placed.nodes) {
      expect(a.x).toBeGreaterThanOrEqual(0);
      expect(a.x + NODE_W).toBeLessThanOrEqual(placed.width);
      expect(a.y + NODE_H).toBeLessThanOrEqual(placed.height);
      for (const b of placed.nodes) {
        if (a === b || a.y !== b.y) continue;
        expect(Math.abs(a.x - b.x)).toBeGreaterThanOrEqual(NODE_W);
      }
    }
  });

  it("handles an empty graph", () => {
    const placed = layout({ nodes: [], edges: [], focus: null });
    expect(placed.nodes).toEqual([]);
    expect(placed.width).toBeGreaterThan(0);
  });
});

describe("stats", () => {
  const alerts = {
    received: 16,
    deduplicated: 7,
    unique_alerts: 9,
    groups: 3,
    open_groups: 3,
    noise_reduction: 0.812,
    window_s: 900,
  };
  const row = (over: Record<string, unknown>) =>
    ({
      incident_id: "inc_x",
      title: "t",
      service: "svc",
      severity: "SEV2",
      status: "PAUSED",
      outcome: "awaiting-approval",
      created_at: 1,
      scenario: null,
      alert_group_id: null,
      pending_gate: null,
      archived: false,
      failure_class: null,
      ...over,
    }) as Parameters<typeof buildStats>[0][number];

  it("matches the server's reporting contract (api/views.py:build_stats)", () => {
    const stats = buildStats(
      [
        row({ outcome: "remediated", failure_class: "deploy-regression/memory", time_to_diagnosis_ms: 4, tokens: 100, est_cost_usd: 0.01, feedback: "correct" }),
        row({ outcome: "escalated", time_to_diagnosis_ms: 2, tokens: 50, est_cost_usd: 0.005, feedback: "incorrect" }),
        row({ outcome: "escalated", tokens: 25 }),
        row({ outcome: "running" }),
      ],
      alerts,
      3,
    );
    expect(stats.incidents.total).toBe(4);
    expect(stats.incidents.by_outcome).toEqual({ escalated: 2, remediated: 1, running: 1 });
    expect(Object.keys(stats.incidents.by_outcome)[0]).toBe("escalated"); // most frequent first
    expect(stats.incidents.by_failure_class).toEqual({ "deploy-regression/memory": 1 });
    expect(stats.diagnosis_rate).toBe(0.333); // 1 of 3 finished; the running one is excluded
    expect(stats.mean_time_to_diagnosis_ms).toBe(3);
    expect(stats.tokens).toBe(175);
    expect(stats.est_cost_usd).toBe(0.015);
    expect(stats.feedback).toEqual({ correct: 1, incorrect: 1, acceptance: 0.5 });
    expect(stats.memory.episodes).toBe(3);
  });

  it("reports unknowns as null rather than dividing by zero", () => {
    const stats = buildStats([], alerts, 0);
    expect(stats.diagnosis_rate).toBeNull();
    expect(stats.mean_time_to_diagnosis_ms).toBeNull();
    expect(stats.feedback.acceptance).toBeNull();
  });
});
