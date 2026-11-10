// The demo client is a small state machine over a recorded transcript; these
// tests hold it to the same lifecycle rules the real API enforces, and check
// that the transcript itself (demo-data.json) is internally consistent — a
// stale recording fails here rather than on the public demo.
import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { CANONICAL, closestRecordedQuery, createDemoClient, DEMO } from "./demo";
import storm from "./storm.json";

const client = () => createDemoClient(DEMO, 0);

describe("recorded transcript", () => {
  it("covers every scenario the API lists", () => {
    expect(Object.keys(DEMO.runs).sort()).toEqual(DEMO.scenarios.map((s) => s.id).sort());
    expect(DEMO.scenarios.length).toBeGreaterThanOrEqual(7);
  });

  it("recorded each scenario's ground-truth outcome", () => {
    for (const scenario of DEMO.scenarios) {
      const run = DEMO.runs[scenario.id];
      if (scenario.expected_outcome === "escalated") {
        expect(run.created.outcome, scenario.id).toBe("escalated");
        expect(run.approved, scenario.id).toBeUndefined();
        expect(run.created.hypotheses?.validated, scenario.id).toBeNull();
      } else {
        expect(run.created.outcome, scenario.id).toBe("awaiting-approval");
        expect(run.approved?.outcome, scenario.id).toBe("remediated");
        expect(run.denied?.outcome, scenario.id).toBe("denied");
        expect(run.postmortem?.markdown.startsWith("# Postmortem"), scenario.id).toBe(true);
      }
    }
  });

  it("has a hypothesis tree that agrees with the pipeline everywhere", () => {
    for (const [id, run] of Object.entries(DEMO.runs)) {
      expect(run.created.hypotheses?.pipeline.agrees, id).toBe(true);
    }
  });

  it("cites only evidence that exists in each bundle", () => {
    for (const [id, run] of Object.entries(DEMO.runs)) {
      const known = new Set((run.created.evidence ?? []).map((e) => e.id));
      for (const hypothesis of run.created.hypotheses?.hypotheses ?? []) {
        for (const criterion of hypothesis.criteria) {
          for (const ref of criterion.evidence) expect(known.has(ref), `${id} ${ref}`).toBe(true);
        }
      }
    }
  });

  it("recorded verified audit chains with no secrets in them", () => {
    const text = JSON.stringify(DEMO);
    expect(text).not.toContain("dd-abc123def456"); // the credential the fake monitor plants
    expect(text).not.toContain('"fence"'); // capability tokens are never recorded
    for (const run of Object.values(DEMO.runs)) {
      expect(run.audit_created.chain_verified).toBe(true);
      expect(run.audit_created.count).toBe(run.audit_created.records.length);
    }
  });

  it("recorded the storm this build ships", () => {
    expect(DEMO.alerts.storm.stats.received).toBe(storm.alerts.length);
    expect(DEMO.alerts.empty.groups).toEqual([]);
  });
});

describe("demo client lifecycle", () => {
  it("runs the canonical incident to the gate, then to a verified remediation", async () => {
    const api = client();
    const created = await api.createIncident();
    expect(created.status).toBe("PAUSED");
    expect(created.scenario).toBe(CANONICAL);
    expect((await api.listIncidents()).map((r) => r.incident_id)).toEqual([created.incident_id]);
    await expect(api.postmortem(created.incident_id)).rejects.toBeInstanceOf(ApiError);

    const resolved = await api.decide(created, true);
    expect(resolved.incident_id).toBe(created.incident_id);
    expect(resolved.outcome).toBe("remediated");
    expect((await api.postmortem(created.incident_id)).follow_ups.length).toBeGreaterThan(0);
    expect((await api.audit(created.incident_id)).count).toBeGreaterThan(
      DEMO.runs[CANONICAL].audit_created.count,
    );

    // gate semantics: a resolved incident cannot be decided again
    await expect(api.decide(resolved, true)).rejects.toMatchObject({ status: 409 });
  });

  it("replaces the paused incident with the recorded denial", async () => {
    const api = client();
    const created = await api.createIncident();
    const denied = await api.decide(created, false);
    expect(denied.outcome).toBe("denied");
    expect(denied.executed).toBeUndefined(); // nothing ran
    const rows = await api.listIncidents();
    expect(rows).toHaveLength(1);
    expect(rows[0].incident_id).toBe(denied.incident_id);
    await expect(api.getIncident(created.incident_id)).rejects.toMatchObject({ status: 404 });
  });

  it("never mutates the transcript it replays", async () => {
    const before = JSON.stringify(DEMO.runs[CANONICAL].created);
    const api = client();
    const created = await api.createIncident();
    await api.feedback(created.incident_id, "incorrect", "it was a leak");
    await api.decide(created, true);
    expect(JSON.stringify(DEMO.runs[CANONICAL].created)).toBe(before);
  });

  it("isolates sessions from each other", async () => {
    const first = client();
    await first.createIncident();
    expect(await client().listIncidents()).toEqual([]);
  });

  it("escalates the adversarial scenarios instead of pausing", async () => {
    const api = client();
    const incident = await api.createIncident("s5-reverted-pool");
    expect(incident.outcome).toBe("escalated");
    expect(incident.hypotheses?.conclusion).toBe("inconclusive");
    await expect(api.decide(incident, true)).rejects.toMatchObject({ status: 409 });
    await expect(api.createIncident("nope")).rejects.toMatchObject({ status: 400 });
  });

  it("records feedback against the incident and its stats", async () => {
    const api = client();
    const created = await api.createIncident();
    const updated = await api.feedback(created.incident_id, "correct", "  matches the deploy  ");
    expect(updated.feedback).toMatchObject({ verdict: "correct", note: "matches the deploy" });
    expect((await api.stats()).feedback).toEqual({ correct: 1, incorrect: 0, acceptance: 1 });
  });
});

describe("demo alert flow", () => {
  it("starts quiet, then shows the recorded storm", async () => {
    const api = client();
    expect((await api.alerts()).groups).toEqual([]);
    const payload = await api.ingestStorm();
    expect(payload.stats.received).toBe(storm.alerts.length);
    expect(payload.stats.groups).toBe(payload.groups.length);
    expect((await api.stats()).alerts.noise_reduction).toBe(payload.stats.noise_reduction);
  });

  it("opens exactly one incident per investigable group", async () => {
    const api = client();
    const { groups } = await api.ingestStorm();
    const checkout = groups.find((g) => g.origin === "checkout-service")!;
    const incident = await api.investigateGroup(checkout.id);
    expect(incident.service).toBe("checkout-service");
    expect(incident.alert_group_id).toBe(checkout.id);

    const claimed = (await api.alerts()).groups.find((g) => g.id === checkout.id)!;
    expect(claimed.status).toBe("investigating");
    expect(claimed.incident_id).toBe(incident.incident_id);
    await expect(api.investigateGroup(checkout.id)).rejects.toMatchObject({ status: 409 });
  });

  it("keeps the group pointed at the incident after a denial", async () => {
    const api = client();
    const { groups } = await api.ingestStorm();
    const checkout = groups.find((g) => g.origin === "checkout-service")!;
    const denied = await api.decide(await api.investigateGroup(checkout.id), false);
    const group = (await api.alerts()).groups.find((g) => g.id === checkout.id)!;
    expect(group.incident_id).toBe(denied.incident_id);
  });

  it("refuses a group with no evidence source, as the API does", async () => {
    const api = client();
    const { groups } = await api.ingestStorm();
    const orphan = groups.find((g) => !g.investigable)!;
    expect(orphan).toBeDefined();
    await expect(api.investigateGroup(orphan.id)).rejects.toMatchObject({ status: 422 });
    await expect(api.investigateGroup("grp_nope")).rejects.toMatchObject({ status: 404 });
  });

  it("clears back to an empty inbox", async () => {
    const api = client();
    await api.ingestStorm();
    expect((await api.clearAlerts()).groups).toEqual([]);
    expect((await api.topology()).nodes.every((n) => n.alerts === null)).toBe(true);
  });

  it("overlays open incidents on the service map", async () => {
    const api = client();
    const incident = await api.createIncident();
    const node = (await api.topology()).nodes.find((n) => n.id === "checkout-service")!;
    expect(node.incidents).toEqual([incident.incident_id]);
    await api.decide(incident, true);
    const after = (await api.topology()).nodes.find((n) => n.id === "checkout-service")!;
    expect(after.incidents).toEqual([]);
  });
});

describe("demo lookups", () => {
  it("answers only the recorded change presets", async () => {
    const api = client();
    const body = { service: "orders-service", title: "", diff: "", peak_window: true, freeze: false };
    const risky = await api.scoreChange({ ...body, sha: "b7e21c9" });
    const benign = await api.scoreChange({ ...body, sha: "a11c3f0" });
    expect(risky.score!).toBeGreaterThan(benign.score!);
    await expect(api.scoreChange({ ...body, sha: "deadbee" })).rejects.toMatchObject({ status: 501 });
  });

  it("maps a free-text search onto the closest recorded query", async () => {
    const available = Object.keys(DEMO.runbooks);
    expect(closestRecordedQuery("pods keep getting OOMKilled", available)).toBe("OOMKilled pods after deploy");
    expect(closestRecordedQuery("certificate expired", available)).toBe(
      "TLS certificate expired handshake failures",
    );
    expect(closestRecordedQuery("", available)).toBe(available[0]);
    const { query, results } = await client().searchRunbooks("how do I roll back a deployment");
    expect(query).toBe("roll back a bad deployment");
    expect(results.length).toBeGreaterThan(0);
  });
});
