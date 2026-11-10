// DEMO mode: a client that replays the transcript in demo-data.json, which
// tools/record_demo.py captures from the real API. Every document served
// here is a response the server actually returned; this file only decides
// WHICH recorded document answers each call, and keeps the small amount of
// session state (which incidents you opened, whether the storm was ingested)
// that makes the console navigable without a backend.
import type { Client } from "./api";
import { ApiError } from "./api";
import { buildStats, toRow } from "./lib/stats";
import type {
  AlertsPayload,
  AuditLedger,
  Catalog,
  ChangeScore,
  Evals,
  Incident,
  OpenApi,
  Postmortem,
  Runbook,
  ScenarioInfo,
  Topology,
  WhoAmI,
} from "./types";
import recorded from "./demo-data.json";

interface DemoRun {
  created: Incident;
  audit_created: AuditLedger;
  approved?: Incident;
  audit_approved?: AuditLedger;
  postmortem?: Postmortem;
  denied?: Incident;
  audit_denied?: AuditLedger;
}

export interface DemoData {
  recorded_at: string;
  version: string;
  whoami: WhoAmI;
  scenarios: ScenarioInfo[];
  catalog: Catalog;
  connectors: Record<string, string>;
  openapi: OpenApi;
  evals: Evals;
  runbooks: Record<string, Runbook[]>;
  changes: { risky: ChangeScore; benign: ChangeScore };
  alerts: { empty: AlertsPayload; storm: AlertsPayload };
  topology: { idle: Topology; storm: Topology };
  runs: Record<string, DemoRun>;
}

export const DEMO = recorded as unknown as DemoData;
export const CANONICAL = "s1-checkout-pool";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** The recorded query sharing the most words with `query`. */
export function closestRecordedQuery(query: string, available: string[]): string {
  const words = new Set(query.toLowerCase().match(/[a-z0-9]+/g) ?? []);
  let best = available[0];
  let bestScore = -1;
  for (const candidate of available) {
    const score = (candidate.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => words.has(w)).length;
    if (score > bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  return best;
}

interface Session {
  incidents: Map<string, Incident>; // id -> current document
  audits: Map<string, AuditLedger>;
  postmortems: Map<string, Postmortem>;
  runOf: Map<string, string>; // incident id -> scenario key
  groupIncident: Map<string, string>; // alert group id -> incident id
  stormIngested: boolean;
}

export function createDemoClient(data: DemoData = DEMO, latencyMs = 320): Client {
  const session: Session = {
    incidents: new Map(),
    audits: new Map(),
    postmortems: new Map(),
    runOf: new Map(),
    groupIncident: new Map(),
    stormIngested: false,
  };
  const wait = (): Promise<void> => (latencyMs ? pause(latencyMs) : Promise.resolve());

  function open(scenarioKey: string, groupId: string | null): Incident {
    const run = data.runs[scenarioKey];
    if (!run) throw new ApiError(400, `unknown scenario '${scenarioKey}'`);
    const doc = clone(run.created);
    doc.alert_group_id = groupId;
    doc.created_at = Date.now() / 1000;
    session.incidents.set(doc.incident_id, doc);
    session.audits.set(doc.incident_id, run.audit_created);
    session.runOf.set(doc.incident_id, scenarioKey);
    if (groupId) session.groupIncident.set(groupId, doc.incident_id);
    return clone(doc);
  }

  function alertsNow(): AlertsPayload {
    const payload = clone(session.stormIngested ? data.alerts.storm : data.alerts.empty);
    for (const group of payload.groups) {
      const incidentId = session.groupIncident.get(group.id);
      if (incidentId) {
        group.incident_id = incidentId;
        group.status = "investigating";
      }
    }
    return payload;
  }

  function must(id: string): Incident {
    const doc = session.incidents.get(id);
    if (!doc) throw new ApiError(404, "unknown incident");
    return doc;
  }

  return {
    mode: "demo",
    whoami: async () => data.whoami,
    scenarios: async () => data.scenarios,
    catalog: async () => data.catalog,
    connectors: async () => data.connectors,
    openapi: async () => data.openapi,

    evals: async () => {
      await wait();
      return data.evals;
    },

    stats: async () =>
      buildStats([...session.incidents.values()].map(toRow), alertsNow().stats, 1),

    listIncidents: async () =>
      [...session.incidents.values()].map(toRow).sort((a, b) => b.created_at - a.created_at),

    getIncident: async (id) => clone(must(id)),

    createIncident: async (scenario) => {
      await wait();
      return open(scenario ?? CANONICAL, null);
    },

    decide: async (incident, approve) => {
      await wait();
      const key = session.runOf.get(incident.incident_id);
      const run = key ? data.runs[key] : undefined;
      const current = must(incident.incident_id);
      if (!run || current.status !== "PAUSED") {
        throw new ApiError(409, "incident is not awaiting approval");
      }
      // Approval was recorded on this incident. The denial was recorded on
      // a second run of the same scenario, so it arrives with its own id.
      const next = approve ? run.approved : run.denied;
      const ledger = approve ? run.audit_approved : run.audit_denied;
      if (!next || !ledger) throw new ApiError(409, "no recorded decision for this incident");
      const doc = clone(next);
      doc.alert_group_id = current.alert_group_id;
      doc.created_at = current.created_at;
      if (doc.incident_id !== current.incident_id) {
        session.incidents.delete(current.incident_id);
        session.runOf.delete(current.incident_id);
        if (current.alert_group_id) session.groupIncident.set(current.alert_group_id, doc.incident_id);
      }
      session.incidents.set(doc.incident_id, doc);
      session.runOf.set(doc.incident_id, key as string);
      session.audits.set(doc.incident_id, ledger);
      if (approve && run.postmortem) session.postmortems.set(doc.incident_id, run.postmortem);
      return clone(doc);
    },

    feedback: async (id, verdict, note) => {
      const doc = must(id);
      doc.feedback = {
        verdict,
        note: note.trim().slice(0, 500),
        role: data.whoami.role,
        at: Date.now() / 1000,
        memory_updated: Boolean(doc.episode_id),
      };
      return clone(doc);
    },

    audit: async (id) => {
      const ledger = session.audits.get(id);
      if (!ledger) throw new ApiError(404, "unknown incident");
      return ledger;
    },

    postmortem: async (id) => {
      const doc = session.postmortems.get(id);
      if (!doc) throw new ApiError(404, "no postmortem: one is generated only after a verified remediation");
      return doc;
    },

    alerts: async () => alertsNow(),

    ingestStorm: async () => {
      await wait();
      session.stormIngested = true;
      return alertsNow();
    },

    clearAlerts: async () => {
      session.stormIngested = false;
      session.groupIncident.clear();
      return alertsNow();
    },

    investigateGroup: async (groupId) => {
      await wait();
      const group = alertsNow().groups.find((g) => g.id === groupId);
      if (!group) throw new ApiError(404, "unknown alert group");
      if (group.incident_id) throw new ApiError(409, "alert group is already under investigation");
      const scenario = data.scenarios.find((s) => s.service === group.origin);
      if (!scenario) {
        throw new ApiError(422, `no evidence source for service '${group.origin}'`);
      }
      return open(scenario.id, groupId);
    },

    topology: async () => {
      const topology = clone(session.stormIngested ? data.topology.storm : data.topology.idle);
      const open_ = [...session.incidents.values()].filter(
        (i) => i.outcome === "awaiting-approval" || i.outcome === "running",
      );
      for (const node of topology.nodes) {
        node.incidents = open_.filter((i) => i.service === node.id).map((i) => i.incident_id);
      }
      return topology;
    },

    scoreChange: async (body) => {
      await wait();
      // Only the two recorded presets can be answered without a backend;
      // they are told apart by commit sha (see tools/record_demo.py).
      const recordedBy: Record<string, ChangeScore> = {
        b7e21c9: data.changes.risky,
        a11c3f0: data.changes.benign,
      };
      const score = recordedBy[body.sha];
      if (!score) throw new ApiError(501, "Custom changes need the live API — run `make serve`.");
      return clone(score);
    },

    searchRunbooks: async (query) => {
      await wait();
      const matched = closestRecordedQuery(query, Object.keys(data.runbooks));
      return { query: matched, results: data.runbooks[matched] };
    },
  };
}
