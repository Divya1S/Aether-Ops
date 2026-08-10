import type { Change, Evals, Incident, Mode, Runbook } from "./types";
import { DEMO } from "./demo";

// The two canonical change examples the console scores (live and recorded).
export const CHANGE_BODIES = {
  risky: {
    service: "orders-service",
    sha: "b7e21c9",
    title: "Raise DB connection pool max_size 25 -> 250",
    diff: "- max_size: 25\n+ max_size: 250",
    peak_window: true,
  },
  benign: {
    service: "orders-service",
    sha: "a11c3f0",
    title: "Update README copy",
    diff: "- old\n+ new",
    peak_window: true,
  },
} as const;

let token = "aetherops-dev";
export const setToken = (value: string): void => {
  token = value;
};

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as T;
}

/** LIVE when a backend answers /health; DEMO (recorded transcript) otherwise. */
export async function detectMode(): Promise<Mode> {
  try {
    const response = await fetch("/health");
    return response.ok ? "live" : "demo";
  } catch {
    return "demo";
  }
}

export async function triggerIncident(live: boolean): Promise<Incident> {
  if (live) return api<Incident>("/v1/incidents", "POST", {});
  await delay(600);
  return DEMO.created;
}

export async function decide(
  live: boolean,
  incident: Incident,
  approve: boolean,
): Promise<Partial<Incident>> {
  if (live) {
    return api<Partial<Incident>>(
      `/v1/incidents/${incident.incident_id}/approvals`,
      "POST",
      { decision: approve ? "approve" : "deny", fence: incident.fence },
    );
  }
  await delay(500);
  return approve ? DEMO.resolved : { status: "FAILED", error: "approval denied" };
}

export async function scoreChange(
  live: boolean,
  kind: "risky" | "benign",
): Promise<Change> {
  if (live) return api<Change>("/v1/changes/score", "POST", CHANGE_BODIES[kind]);
  await delay(300);
  return DEMO.changes[kind];
}

export async function searchRunbooks(
  live: boolean,
  query: string,
): Promise<Runbook[]> {
  if (live) {
    const result = await api<{ results: Runbook[] }>(
      `/v1/runbooks/search?q=${encodeURIComponent(query)}`,
    );
    return result.results;
  }
  await delay(300);
  return DEMO.runbooks;
}

export async function runEvals(live: boolean): Promise<Evals> {
  if (live) return api<Evals>("/v1/evals");
  await delay(400);
  return DEMO.evals;
}
