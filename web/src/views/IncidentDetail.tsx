import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { SignalChart } from "../components/charts";
import {
  Cited,
  EvidenceTable,
  HypothesisTree,
  Markdown,
  Stepper,
  Timeline,
  TIMELINE_FILTERS,
} from "../components/pieces";
import type { RefControl } from "../components/pieces";
import {
  Badge,
  Button,
  Card,
  CopyButton,
  Empty,
  ErrorNote,
  Icon,
  Loading,
  Note,
  OutcomeBadge,
  PageHead,
  RiskBadge,
  SeverityBadge,
  Stat,
  Tabs,
  useApp,
  useAsync,
} from "../components/ui";
import { ago, clock, compact, duration, failureClass, percent, usd } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { Incident } from "../types";

// Incidents opened in this session animate their stepper once, so a run
// that just finished reads as a sequence rather than a wall of ticks.
const fresh = new Set<string>();
export const markFresh = (id: string): void => void fresh.add(id);

const message = (reason: unknown): string => (reason instanceof Error ? reason.message : String(reason));

export function IncidentDetail({ id }: { id: string }) {
  const { client, can, me, notify } = useApp();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("investigation");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [lit, setLit] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const reveal = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    reveal.current = fresh.delete(id);
    setIncident(null);
    setError(null);
    setTab("investigation");
    setLit(null);
    const load = async (): Promise<void> => {
      try {
        const doc = await client.getIncident(id);
        if (!alive) return;
        setIncident(doc);
        // An async run is still executing: keep reading its stage progress.
        if (doc.status === "RUNNING") timer = window.setTimeout(() => void load(), 450);
      } catch (reason) {
        if (alive) setError(message(reason));
      }
    };
    void load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [client, id, attempt]);

  if (error) {
    return (
      <>
        <PageHead crumbs={<a href={href("incidents")}>Incidents</a>} title="Incident unavailable" />
        <ErrorNote error={error} retry={() => setAttempt((n) => n + 1)} />
      </>
    );
  }
  if (!incident) return <Loading label="Loading incident…" />;

  const control: RefControl = {
    lit,
    focus: (ref) => {
      setLit(ref);
      setTab("investigation");
      window.requestAnimationFrame(() =>
        document.getElementById(`evidence-${ref}`)?.scrollIntoView({ block: "center", behavior: "smooth" }),
      );
    },
  };

  async function decide(approve: boolean): Promise<void> {
    if (!incident) return;
    setBusy(approve ? "approve" : "deny");
    try {
      const next = await client.decide(incident, approve);
      notify(
        approve ? "Approved — remediation executed and recovery verified." : "Denied — nothing was executed.",
        approve ? "success" : "info",
      );
      if (next.incident_id !== incident.incident_id) navigate("incidents", next.incident_id);
      else setIncident(next);
    } catch (reason) {
      notify(message(reason), "error");
      // 409 = our view was stale (someone else decided): re-read it.
      if (reason instanceof ApiError && reason.status === 409) setAttempt((n) => n + 1);
    } finally {
      setBusy(null);
    }
  }

  const running = incident.status === "RUNNING";
  const tabs = [
    { id: "investigation", label: "Investigation" },
    { id: "plan", label: "Plan & gate", count: incident.plan?.length },
    { id: "timeline", label: "Timeline", count: incident.timeline.length },
    { id: "postmortem", label: "Postmortem" },
    { id: "audit", label: "Audit ledger", count: incident.audit?.count },
  ];

  return (
    <>
      <PageHead
        crumbs={
          <>
            <a href={href("incidents")}>Incidents</a> / <span className="mono">{incident.incident_id}</span>
            <CopyButton value={incident.incident_id} label="Copy incident id" />
          </>
        }
        title={incident.title}
        actions={
          <Button icon="refresh" variant="quiet" onClick={() => setAttempt((n) => n + 1)}>
            Refresh
          </Button>
        }
      >
        <span className="row" style={{ marginTop: "0.45rem" }}>
          <SeverityBadge severity={incident.severity} />
          <OutcomeBadge outcome={incident.outcome} />
          <Badge mono>{incident.service}</Badge>
          {incident.scenario && <Badge title="Replays this golden scenario's frozen evidence">snapshot · {incident.scenario}</Badge>}
          {incident.alert_group_id && (
            <a href={href("alerts")}>
              <Badge tone="accent" icon="alerts">
                from alert group
              </Badge>
            </a>
          )}
          {incident.archived && <Badge icon="lock">archived</Badge>}
          <span className="dim small">started {ago(incident.created_at)}</span>
        </span>
      </PageHead>

      <div className="stack">
        <Card title="Pipeline">
          <Stepper stages={incident.stages} reveal={reveal.current} />
        </Card>

        <Banner incident={incident} busy={busy} decide={decide} canApprove={can("approve")} role={me?.role ?? "unknown"} />

        {running ? (
          <Card title="Live progress">
            <Timeline events={incident.timeline} />
          </Card>
        ) : (
          <>
            <div className="stats">
              <DiagnosisStats incident={incident} />
              <Stat label="Evidence" value={incident.evidence_count ?? 0} hint="cited artifacts" />
              <Stat label="Time to diagnosis" value={duration(incident.time_to_diagnosis_ms)} hint="alert to root cause" />
              <Stat
                label="Model usage"
                value={`${compact(incident.tokens ?? 0)} tok`}
                hint={`${usd(incident.est_cost_usd)} at production prices`}
              />
            </div>

            <div>
              <Tabs tabs={tabs} value={tab} onChange={setTab} label="Incident sections" />
              {tab === "investigation" && <Investigation incident={incident} control={control} setIncident={setIncident} />}
              {tab === "plan" && <PlanAndGate incident={incident} />}
              {tab === "timeline" && <TimelinePanel incident={incident} />}
              {tab === "postmortem" && <PostmortemPanel incident={incident} />}
              {tab === "audit" && <AuditPanel incident={incident} />}
            </div>
          </>
        )}
      </div>
    </>
  );
}

function DiagnosisStats({ incident }: { incident: Incident }) {
  const diagnosis = incident.diagnosis;
  const proposed = diagnosis?.status === "diagnosed";
  const standing = diagnosis?.standing ?? proposed;
  if (proposed && !standing) {
    // The model proposed a cause and the platform rejected it: say so,
    // rather than showing a rejected class as "the diagnosis".
    return (
      <>
        <Stat
          text
          label="Diagnosis"
          value="Rejected in review"
          hint={`model proposed ${failureClass(diagnosis?.failure_class)}`}
        />
        <Stat label="Calibrated confidence" value="—" hint="no diagnosis stands" />
      </>
    );
  }
  return (
    <>
      <Stat
        text
        label="Diagnosis"
        value={standing ? failureClass(diagnosis?.failure_class) : "Insufficient evidence"}
        hint={
          diagnosis?.suspect_commit
            ? `suspect commit ${diagnosis.suspect_commit}`
            : standing
              ? "no change implicated"
              : "the platform declined to guess"
        }
      />
      <Stat
        label="Calibrated confidence"
        value={standing && diagnosis ? percent(diagnosis.confidence) : "—"}
        hint={standing ? "self-estimate × evidence coverage" : "no diagnosis stands"}
      />
    </>
  );
}

// ───────────────────────── status banner ─────────────────────────
function Banner({
  incident,
  busy,
  decide,
  canApprove,
  role,
}: {
  incident: Incident;
  busy: "approve" | "deny" | null;
  decide: (approve: boolean) => Promise<void>;
  canApprove: boolean;
  role: string;
}) {
  const steps = incident.plan ?? [];
  switch (incident.outcome) {
    case "running":
      return (
        <Note tone="accent" icon="refresh" title="Investigating…">
          <p>Agents are gathering and screening evidence. This page updates as each stage completes.</p>
        </Note>
      );
    case "awaiting-approval":
      return (
        <Note
          tone="warn"
          icon="pause"
          title={`Paused at the approval gate — tier ${incident.approval_tier ?? "?"}`}
          actions={
            incident.archived ? (
              <span className="muted small">
                Restored from the archive after a restart: readable, but its paused workflow state is gone. Re-trigger
                the incident to act on it.
              </span>
            ) : canApprove ? (
              <>
                <Button variant="approve" icon="check" busy={busy === "approve"} disabled={busy !== null} onClick={() => void decide(true)}>
                  Approve and execute
                </Button>
                <Button variant="danger" icon="x" busy={busy === "deny"} disabled={busy !== null} onClick={() => void decide(false)}>
                  Deny
                </Button>
              </>
            ) : (
              <span className="muted small">
                Your role (<b>{role}</b>) cannot decide gates. An approver or admin token is required.
              </span>
            )
          }
        >
          <p>
            Nothing has executed. Approving runs{" "}
            {steps.map((step, i) => (
              <span key={step.action}>
                {i > 0 && ", then "}
                <code>{step.action}</code> ({step.risk})
              </span>
            ))}{" "}
            against <b>{incident.service}</b>, then verifies recovery before anything is learned.
          </p>
        </Note>
      );
    case "remediated":
      return (
        <Note tone="good" icon="check" title="Remediated and verified">
          <p>
            {incident.verification
              ? `Recovery measured at p99 ${incident.verification.p99_ms} ms. `
              : ""}
            The episode was written to memory{incident.episode_id ? ` (${incident.episode_id})` : ""} and the postmortem
            was generated from the audit ledger.
          </p>
        </Note>
      );
    case "escalated":
      return (
        <Note tone="warn" icon="flag" title="Escalated to a human — the platform declined to act">
          <p>{incident.error}</p>
          <p style={{ marginTop: "0.3rem" }}>
            Escalating on evidence it cannot ground is the correct outcome, not a failure. The hypothesis tree below
            shows what ruled each cause out.
          </p>
        </Note>
      );
    case "denied":
      return (
        <Note tone="accent" icon="x" title="Denied at the gate — nothing was executed">
          <p>The remediation plan was rejected by a human approver. The diagnosis and evidence remain on record.</p>
        </Note>
      );
    default:
      return (
        <Note tone="crit" icon="warning" title="The workflow failed">
          <p>{incident.error ?? "No error was recorded."}</p>
        </Note>
      );
  }
}

// ───────────────────────── investigation tab ─────────────────────────
function Investigation({
  incident,
  control,
  setIncident,
}: {
  incident: Incident;
  control: RefControl;
  setIncident: (incident: Incident) => void;
}) {
  return (
    <div className="grid two">
      <div className="stack">
        {incident.hypotheses && (
          <Card title="Hypothesis tree — what was considered, and what decided it">
            <HypothesisTree tree={incident.hypotheses} control={control} />
          </Card>
        )}
        {incident.hypothesis && (
          <Card
            title="Root-cause narrative"
            foot="Written by the model. Every [E…] reference is checked against the bundle — a citation to evidence that does not exist fails the run."
          >
            <p className="prose">
              <Cited text={incident.hypothesis} control={control} />
            </p>
          </Card>
        )}
        <Card title={`Evidence bundle · ${incident.evidence?.length ?? 0} cited artifacts`} flush>
          <EvidenceTable evidence={incident.evidence ?? []} control={control} />
        </Card>
      </div>

      <div className="stack">
        {incident.signals && (
          <Card title="Symptom">
            <SignalChart signals={incident.signals} />
          </Card>
        )}
        <Card title="Agents" flush>
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Model tier</th>
                <th className="num">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {(incident.agents ?? []).map((agent) => (
                <tr key={agent.name}>
                  <td>{agent.name}</td>
                  <td className="dim small mono">{agent.model === "n/a" ? "deterministic" : agent.model}</td>
                  <td className="num">{percent(agent.confidence)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Blast radius">
          {incident.blast_radius?.length ? (
            <>
              <p className="muted small" style={{ marginBottom: "0.5rem" }}>
                Services that call {incident.service}, directly or transitively:
              </p>
              <div className="row tight">
                {incident.blast_radius.map((service) => (
                  <span className="chip" key={service}>
                    {service}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <p className="muted small">No known callers in the service graph.</p>
          )}
        </Card>
        {incident.similar_incidents && incident.similar_incidents.length > 0 && (
          <Card title="Similar past incidents">
            <ul className="list-plain">
              {incident.similar_incidents.map((item) => (
                <li key={item.ref}>
                  {item.summary}
                  <div className="row tight" style={{ marginTop: "0.2rem" }}>
                    <code>{item.ref}</code>
                    <span className="dim small">cited as</span>
                    <button type="button" className="eref" onClick={() => control.focus(item.evidence)}>
                      {item.evidence}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}
        <FeedbackCard incident={incident} setIncident={setIncident} />
      </div>
    </div>
  );
}

function FeedbackCard({ incident, setIncident }: { incident: Incident; setIncident: (incident: Incident) => void }) {
  const { client, can, notify } = useApp();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  if (!incident.diagnosis || incident.archived) return null;

  async function send(verdict: "correct" | "incorrect"): Promise<void> {
    setBusy(true);
    try {
      setIncident({ ...(await client.feedback(incident.incident_id, verdict, note)), fence: incident.fence });
      setNote("");
      notify("Feedback recorded in the audit ledger.", "success");
    } catch (reason) {
      notify(message(reason), "error");
    } finally {
      setBusy(false);
    }
  }

  const given = incident.feedback;
  return (
    <Card title="Was this diagnosis right?">
      {given && (
        <p style={{ marginBottom: "0.6rem" }}>
          <Badge tone={given.verdict === "correct" ? "good" : "crit"} icon={given.verdict === "correct" ? "up" : "down"}>
            Marked {given.verdict}
          </Badge>{" "}
          <span className="dim small">
            by {given.role} at {clock(given.at)}
            {given.memory_updated &&
              (given.verdict === "incorrect"
                ? " · the learned episode no longer counts as precedent"
                : " · the learned episode stands as precedent")}
          </span>
        </p>
      )}
      {can("feedback") ? (
        <>
          <label className="field">
            Note (optional)
            <input
              className="input"
              value={note}
              maxLength={500}
              placeholder="What was the real cause?"
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <div className="row" style={{ marginTop: "0.6rem" }}>
            <Button icon="up" disabled={busy} onClick={() => void send("correct")}>
              Correct
            </Button>
            <Button icon="down" disabled={busy} onClick={() => void send("incorrect")}>
              Incorrect
            </Button>
          </div>
          <p className="dim small" style={{ marginTop: "0.5rem" }}>
            Recorded in the hash-chained ledger. A diagnosis marked incorrect stops being used as precedent.
          </p>
        </>
      ) : (
        <p className="muted small">Your role cannot record feedback.</p>
      )}
    </Card>
  );
}

// ───────────────────────── plan & gate tab ─────────────────────────
function PlanAndGate({ incident }: { incident: Incident }) {
  if (!incident.plan) {
    return (
      <Card>
        <Empty icon="flag" title="No remediation plan">
          The pipeline stopped before planning: there is no diagnosis to plan against. Nothing was proposed, so nothing
          could be approved.
        </Empty>
      </Card>
    );
  }
  const rejected = (incident.review_checks ?? []).filter((check) => !check.passed);
  return (
    <div className="grid two">
      <div className="stack">
        {rejected.length > 0 && (
          <Note tone="warn" icon="flag" title="This plan was rejected in review and never reached the gate">
            <p>
              Failed check{rejected.length === 1 ? "" : "s"}:{" "}
              {rejected.map((check, i) => (
                <span key={check.name}>
                  {i > 0 && ", "}
                  <code>{check.name}</code>
                </span>
              ))}
              . It is shown for the record; nothing here could be approved or executed.
            </p>
          </Note>
        )}
        <Card title="Remediation plan — Step Catalog actions only" flush foot={incident.plan_rationale}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Action</th>
                  <th>Risk</th>
                  <th>Arguments</th>
                  <th>Undo</th>
                </tr>
              </thead>
              <tbody>
                {incident.plan.map((step, index) => (
                  <tr key={step.action}>
                    <td className="dim">{index + 1}</td>
                    <td>
                      <code>{step.action}</code>
                    </td>
                    <td>
                      <RiskBadge risk={step.risk} />
                    </td>
                    <td>
                      {Object.entries(step.args).map(([key, value]) => (
                        <div key={key} className="small">
                          <span className="dim">{key}</span> <code>{String(value)}</code>
                        </div>
                      ))}
                    </td>
                    <td className="small muted">{step.compensable ? "compensable" : "not compensable"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {incident.executed && (
          <Card title="Execution record" flush>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Action</th>
                    <th>Mode</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {incident.executed.map((record) => (
                    <tr key={record.action}>
                      <td>
                        <code>{record.action}</code>
                      </td>
                      <td>
                        <Badge title="Connector writes are dry-run in this deployment">
                          {record.dry_run ? "dry-run" : "applied"}
                        </Badge>
                      </td>
                      <td>
                        {Object.entries(record.result).map(([key, value]) => (
                          <div key={key} className="small">
                            <span className="dim">{key}</span> <code>{String(value)}</code>
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      <div className="stack">
        <Card title="Independent review">
          {incident.review_checks?.length ? (
            <ul className="list-plain">
              {incident.review_checks.map((check) => (
                <li key={check.name} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
                  <Badge tone={check.passed ? "good" : "crit"} icon={check.passed ? "check" : "x"}>
                    {check.passed ? "Pass" : "Fail"}
                  </Badge>
                  <span>
                    <code>{check.name}</code>
                    <div className="muted small">{check.note}</div>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">The Reviewer did not run.</p>
          )}
        </Card>
        <Card title="Policy admission">
          {incident.policy ? (
            <>
              <p style={{ marginBottom: "0.6rem" }}>
                <Badge tone={incident.policy.requires_approval ? "warn" : "good"} icon={incident.policy.requires_approval ? "lock" : "check"}>
                  {incident.policy.requires_approval
                    ? `Human approval required · tier ${incident.policy.approval_tier}`
                    : "Auto-approved by policy"}
                </Badge>
              </p>
              <ul className="list-plain">
                {incident.policy.decisions.map((decision) => (
                  <li key={decision.action}>
                    <code>{decision.rule_id}</code>
                    <div className="muted small">{decision.reason}</div>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted small">The plan never reached policy admission.</p>
          )}
        </Card>
      </div>
    </div>
  );
}

// ───────────────────────── timeline tab ─────────────────────────
function TimelinePanel({ incident }: { incident: Incident }) {
  const [filter, setFilter] = useState("all");
  const kinds = TIMELINE_FILTERS.find((f) => f.id === filter)?.kinds ?? [];
  const events = kinds.length ? incident.timeline.filter((e) => kinds.includes(e.kind)) : incident.timeline;
  return (
    <Card
      title="Investigation, step by step"
      actions={TIMELINE_FILTERS.map((option) => (
        <button
          key={option.id}
          type="button"
          className={`chip ${filter === option.id ? "active" : ""}`}
          aria-pressed={filter === option.id}
          onClick={() => setFilter(option.id)}
        >
          {option.label}
        </button>
      ))}
      foot="Reconstructed from the audit ledger — offsets are measured from the workflow's first record."
    >
      {events.length ? <Timeline events={events} /> : <p className="muted">Nothing of that kind in this run.</p>}
    </Card>
  );
}

// ───────────────────────── postmortem tab ─────────────────────────
function PostmortemPanel({ incident }: { incident: Incident }) {
  const { client } = useApp();
  const available = Boolean(incident.postmortem_available) && !incident.archived;
  const { data, error, loading, reload } = useAsync(
    () => (available ? client.postmortem(incident.incident_id) : Promise.resolve(null)),
    [client, incident.incident_id, available],
  );

  if (!available) {
    return (
      <Card>
        <Empty icon="runbooks" title="No postmortem yet">
          {incident.archived
            ? "The archive keeps the incident record; the full postmortem lives with the running process."
            : "A postmortem is generated only after a remediation executes and recovery is verified."}
        </Empty>
      </Card>
    );
  }
  if (error) return <ErrorNote error={error} retry={reload} />;
  if (loading || !data) return <Loading label="Loading postmortem…" />;

  const download = (): void => {
    const url = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `postmortem-${incident.incident_id}.md`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card
      title="Generated postmortem"
      actions={
        <Button variant="sm" icon="download" onClick={download}>
          Download .md
        </Button>
      }
      foot="Assembled from the workflow's own record. The model wrote only the summary paragraph."
    >
      <Markdown source={data.markdown} />
    </Card>
  );
}

// ───────────────────────── audit tab ─────────────────────────
function AuditPanel({ incident }: { incident: Incident }) {
  const { client } = useApp();
  const { data, error, loading, reload } = useAsync(
    () => client.audit(incident.incident_id),
    [client, incident.incident_id, incident.audit?.count],
  );
  const [open, setOpen] = useState<number | null>(null);
  if (error) return <ErrorNote error={error} retry={reload} />;
  if (!data) return <Loading label="Loading ledger…" />;
  return (
    <Card
      title={`Audit ledger · ${data.count} records`}
      flush
      actions={
        <Badge tone={data.chain_verified ? "good" : "crit"} icon={data.chain_verified ? "link" : "warning"}>
          {data.chain_verified ? "Hash chain verified" : "Hash chain BROKEN"}
        </Badge>
      }
      foot="Each record's hash covers the previous record's hash, so an in-place edit or reorder breaks verification from that point on."
    >
      <div className={`table-wrap ${loading ? "stale" : ""}`}>
        <table>
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Time</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {data.records.map((record) => (
              <AuditRow
                key={record.seq}
                record={record}
                open={open === record.seq}
                toggle={() => setOpen(open === record.seq ? null : record.seq)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AuditRow({
  record,
  open,
  toggle,
}: {
  record: { seq: number; ts: number; actor: string; action: string; payload: Record<string, unknown>; hash: string; prev_hash: string };
  open: boolean;
  toggle: () => void;
}) {
  return (
    <>
      <tr className="clickable" onClick={toggle}>
        <td className="num dim">{record.seq}</td>
        <td className="mono small nowrap">{clock(record.ts)}</td>
        <td className="small">{record.actor}</td>
        <td>
          <button type="button" className="chip" aria-expanded={open} onClick={(event) => { event.stopPropagation(); toggle(); }}>
            <Icon name="chevron" className={`chev ${open ? "open" : ""}`} />
            {record.action}
          </button>
        </td>
        <td className="hash" title={record.hash}>
          {record.hash.slice(0, 12)}…
        </td>
      </tr>
      {open && (
        <tr>
          <td />
          <td colSpan={4}>
            <pre className="block">{JSON.stringify(record.payload, null, 2)}</pre>
            <div className="hash" style={{ marginTop: "0.35rem" }}>
              prev {record.prev_hash.slice(0, 24)}… → this {record.hash.slice(0, 24)}…
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
