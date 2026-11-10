// One client interface, two implementations: `liveClient` drives the real
// API; `demoClient` (demo.ts) replays a transcript recorded from it. Views
// only ever see `Client`, so nothing in the UI branches on the mode.
import type {
  AlertsPayload,
  AuditLedger,
  Catalog,
  ChangeRequest,
  ChangeScore,
  Evals,
  Incident,
  IncidentRow,
  Mode,
  OpenApi,
  Postmortem,
  Runbook,
  ScenarioInfo,
  Stats,
  Topology,
  WhoAmI,
} from "./types";
import storm from "./storm.json";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface Client {
  mode: "live" | "demo";
  whoami(): Promise<WhoAmI>;
  stats(): Promise<Stats>;
  listIncidents(): Promise<IncidentRow[]>;
  getIncident(id: string): Promise<Incident>;
  createIncident(scenario?: string): Promise<Incident>;
  decide(incident: Incident, approve: boolean): Promise<Incident>;
  feedback(id: string, verdict: "correct" | "incorrect", note: string): Promise<Incident>;
  audit(id: string): Promise<AuditLedger>;
  postmortem(id: string): Promise<Postmortem>;
  scenarios(): Promise<ScenarioInfo[]>;
  alerts(): Promise<AlertsPayload>;
  ingestStorm(): Promise<AlertsPayload>;
  clearAlerts(): Promise<AlertsPayload>;
  investigateGroup(groupId: string): Promise<Incident>;
  topology(): Promise<Topology>;
  scoreChange(body: ChangeRequest): Promise<ChangeScore>;
  searchRunbooks(query: string): Promise<{ query: string; results: Runbook[] }>;
  evals(): Promise<Evals>;
  catalog(): Promise<Catalog>;
  connectors(): Promise<Record<string, string>>;
  openapi(): Promise<OpenApi>;
}

const TOKEN_KEY = "aetherops.token";
const DEFAULT_TOKEN = "aetherops-dev";

export function getToken(): string {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) ?? DEFAULT_TOKEN;
  } catch {
    return DEFAULT_TOKEN; // storage blocked (private mode): fall back
  }
}

export function setToken(value: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* storage blocked: the token simply lasts for this page view */
  }
  current = value;
}

let current = getToken();

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${current}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "The API is unreachable. Is the server still running?");
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "error" in payload
        ? String((payload as { error: unknown }).error)
        : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

/** LIVE when a backend answers /health with the AetherOps signature; DEMO
 *  (recorded transcript) otherwise — e.g. on GitHub Pages. */
export async function detectMode(): Promise<Mode> {
  try {
    const response = await fetch("/health");
    if (!response.ok) return "demo";
    const body = (await response.json()) as { service?: string };
    return body.service === "aetherops" ? "live" : "demo";
  } catch {
    return "demo";
  }
}

export const liveClient: Client = {
  mode: "live",
  whoami: () => request<WhoAmI>("/v1/whoami"),
  stats: () => request<Stats>("/v1/stats"),
  listIncidents: async () =>
    (await request<{ incidents: IncidentRow[] }>("/v1/incidents?limit=200")).incidents,
  getIncident: (id) => request<Incident>(`/v1/incidents/${encodeURIComponent(id)}`),
  // Async: the POST returns at once and the detail view polls, so a slow
  // live-model run never holds a request open.
  createIncident: (scenario) =>
    request<Incident>("/v1/incidents", "POST", { async: true, ...(scenario ? { scenario } : {}) }),
  decide: (incident, approve) =>
    request<Incident>(`/v1/incidents/${encodeURIComponent(incident.incident_id)}/approvals`, "POST", {
      decision: approve ? "approve" : "deny",
      fence: incident.fence,
    }),
  feedback: (id, verdict, note) =>
    request<Incident>(`/v1/incidents/${encodeURIComponent(id)}/feedback`, "POST", { verdict, note }),
  audit: (id) => request<AuditLedger>(`/v1/incidents/${encodeURIComponent(id)}/audit`),
  postmortem: (id) => request<Postmortem>(`/v1/incidents/${encodeURIComponent(id)}/postmortem`),
  scenarios: async () => (await request<{ scenarios: ScenarioInfo[] }>("/v1/scenarios")).scenarios,
  alerts: () => request<AlertsPayload>("/v1/alerts"),
  ingestStorm: async () => {
    await request("/v1/alerts", "POST", storm);
    return request<AlertsPayload>("/v1/alerts");
  },
  clearAlerts: () => request<AlertsPayload>("/v1/alerts", "DELETE"),
  investigateGroup: (groupId) =>
    request<Incident>(`/v1/alerts/groups/${encodeURIComponent(groupId)}/investigate`, "POST", {
      async: true,
    }),
  topology: () => request<Topology>("/v1/topology"),
  scoreChange: (body) => request<ChangeScore>("/v1/changes/score", "POST", body),
  searchRunbooks: (query) =>
    request<{ query: string; results: Runbook[] }>(`/v1/runbooks/search?q=${encodeURIComponent(query)}`),
  evals: () => request<Evals>("/v1/evals"),
  catalog: () => request<Catalog>("/v1/catalog"),
  connectors: async () =>
    (await request<{ connectors: Record<string, string> }>("/v1/connectors")).connectors,
  openapi: () => request<OpenApi>("/v1/openapi.json"),
};

export const STORM_SIZE = storm.alerts.length;
