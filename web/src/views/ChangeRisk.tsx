import { useState } from "react";
import { Badge, Button, Card, Empty, Note, PageHead, Stat, useApp } from "../components/ui";
import type { ChangeRequest, ChangeScore } from "../types";

// Published scoring caps (workflows/change_risk.py) — each component is a
// ratio against its own limit, so they read as a meter list.
const COMPONENTS: { key: string; label: string; cap: number; hint: string }[] = [
  { key: "failure_signature", label: "Failure signature", cap: 40, hint: "matches a verified past incident" },
  { key: "blast_radius", label: "Blast radius", cap: 30, hint: "transitive callers of the service" },
  { key: "deploy_window", label: "Deploy window", cap: 15, hint: "shipping during peak traffic" },
  { key: "service_history", label: "Service history", cap: 15, hint: "prior incidents on this service" },
];

const PRESETS: Record<string, { label: string; body: ChangeRequest }> = {
  risky: {
    label: "Raise DB pool 25 → 250",
    body: {
      service: "orders-service",
      sha: "b7e21c9",
      title: "Raise DB connection pool max_size 25 -> 250",
      diff: "- max_size: 25\n+ max_size: 250",
      peak_window: true,
      freeze: false,
    },
  },
  benign: {
    label: "Update README copy",
    body: {
      service: "orders-service",
      sha: "a11c3f0",
      title: "Update README copy",
      diff: "- old\n+ new",
      peak_window: true,
      freeze: false,
    },
  },
};

const SERVICES = ["orders-service", "checkout-service", "payments-service", "search-api", "storefront-web", "mobile-bff"];
const BAND_TONE: Record<string, "crit" | "warn" | "good"> = { HIGH: "crit", MEDIUM: "warn", LOW: "good" };

function Result({ change, title }: { change: ChangeScore; title: string }) {
  const blocked = change.status === "FAILED";
  const score = change.score ?? 0;
  return (
    <div className="stack">
      {blocked && (
        <Note tone="crit" icon="lock" title="Blocked by policy">
          <p>{change.error}</p>
        </Note>
      )}
      <div className="stats">
        <Stat
          label="Risk band"
          value={
            <Badge tone={BAND_TONE[change.band ?? "LOW"] ?? "good"} mono>
              {change.band ?? "—"}
            </Badge>
          }
          hint={title}
        />
        <Stat label="Score" value={`${score} / 100`} hint="HIGH ≥ 70 · MEDIUM ≥ 40" />
        <Stat text label="Canary" value={change.canary_required ? "Required" : "Not required"} hint="staged rollout" />
        <Stat text label="Approval" value={change.requires_approval ? "Required" : "Not required"} hint={change.policy_rule ?? "policy"} />
      </div>

      <Card title="Why it scored this way" foot={change.policy_reason}>
        <div
          className="scoreline"
          role="img"
          aria-label={`Risk score ${score} out of 100; medium at 40, high at 70`}
        >
          <div className="scoreline-fill" style={{ width: `${Math.min(100, score)}%` }} />
          <div className="scoreline-mark" style={{ left: "40%" }}>
            <span>MEDIUM 40</span>
          </div>
          <div className="scoreline-mark" style={{ left: "70%" }}>
            <span>HIGH 70</span>
          </div>
        </div>
        <div className="meters">
          {COMPONENTS.map((component) => {
            const value = change.components?.[component.key] ?? 0;
            return (
              <MeterRow key={component.key} label={component.label} hint={component.hint} value={value} cap={component.cap} />
            );
          })}
        </div>
        {change.rationale && (
          <p className="muted small" style={{ marginTop: "0.9rem" }}>
            {change.rationale}
          </p>
        )}
      </Card>

      <div className="grid half">
        <Card title={`Callers that would feel it · ${change.dependents.length}`}>
          {change.dependents.length ? (
            <div className="row tight">
              {change.dependents.map((service) => (
                <span className="chip" key={service}>
                  {service}
                </span>
              ))}
            </div>
          ) : (
            <p className="muted small">Nothing in the service graph calls {change.service}.</p>
          )}
        </Card>
        <Card title={`Matching past incidents · ${change.matched_episodes.length}`}>
          {change.matched_episodes.length ? (
            <ul className="list-plain">
              {change.matched_episodes.map((episode) => (
                <li key={episode.ref}>
                  {episode.summary}
                  <div>
                    <code>{episode.ref}</code>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No verified incident has this change's signature.</p>
          )}
        </Card>
      </div>
    </div>
  );
}

function MeterRow({ label, hint, value, cap }: { label: string; hint: string; value: number; cap: number }) {
  return (
    <>
      <span className="meter-name">
        {label}
        <div className="dim small">{hint}</div>
      </span>
      <span className="meter-track" role="img" aria-label={`${label}: ${value} of ${cap} points`}>
        <span className="meter-fill" style={{ display: "block", width: `${(value / cap) * 100}%` }} />
      </span>
      <span className="meter-value">
        {value} / {cap}
      </span>
    </>
  );
}

export function ChangeRisk() {
  const { client, can, notify } = useApp();
  const demo = client.mode === "demo";
  const [form, setForm] = useState<ChangeRequest>(PRESETS.risky.body);
  const [result, setResult] = useState<{ change: ChangeScore; title: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const patch = (fields: Partial<ChangeRequest>): void => setForm({ ...form, ...fields });

  async function score(body: ChangeRequest): Promise<void> {
    setBusy(true);
    try {
      setResult({ change: await client.scoreChange(body), title: body.title || "untitled change" });
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Change risk">
        Catch the outage before it ships. A proposed change is scored with fixed, published weights against incident
        memory, blast radius and the deploy window — the model writes only the rationale.
      </PageHead>

      <div className="grid two">
        <div className="stack">
          {result ? (
            <div className={busy ? "stale" : ""}>
              <Result change={result.change} title={result.title} />
            </div>
          ) : (
            <Card>
              <Empty icon="change" title="Score a change">
                Pick a preset or describe a change. The pool raise matches what the platform learned from the checkout
                incident; the README edit does not.
              </Empty>
            </Card>
          )}
        </div>

        <Card title="Proposed change">
          <div className="stack" style={{ gap: "0.75rem" }}>
            <div className="row">
              {Object.entries(PRESETS).map(([key, preset]) => (
                <Button
                  key={key}
                  variant="sm"
                  disabled={busy || !can("create")}
                  onClick={() => {
                    setForm(preset.body);
                    void score(preset.body);
                  }}
                >
                  {preset.label}
                </Button>
              ))}
            </div>
            <label className="field">
              Service
              <select className="select" value={form.service} disabled={demo} onChange={(e) => patch({ service: e.target.value })}>
                {SERVICES.map((service) => (
                  <option key={service}>{service}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Title
              <input className="input" value={form.title} disabled={demo} maxLength={300} onChange={(e) => patch({ title: e.target.value })} />
            </label>
            <label className="field">
              Diff
              <textarea className="textarea" value={form.diff} disabled={demo} spellCheck={false} onChange={(e) => patch({ diff: e.target.value })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={form.peak_window} disabled={demo} onChange={(e) => patch({ peak_window: e.target.checked })} />
              Shipping during the peak traffic window
            </label>
            <label className="check">
              <input type="checkbox" checked={form.freeze} disabled={demo} onChange={(e) => patch({ freeze: e.target.checked })} />
              A change freeze is in effect
            </label>
            {demo ? (
              <p className="muted small">
                The recorded demo scores the two presets. Run the API locally (<code>make serve</code>) to score your
                own diff.
              </p>
            ) : (
              <Button variant="primary" icon="change" busy={busy} disabled={!can("create")} onClick={() => void score(form)}>
                Score this change
              </Button>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
