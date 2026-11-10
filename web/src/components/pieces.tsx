// Building blocks of the incident view: the pipeline stepper, the hypothesis
// tree, evidence (with [En] cross-highlighting), the timeline and markdown.
import { Fragment, useState } from "react";
import type { ReactNode } from "react";
import { duration } from "../lib/format";
import type {
  Criterion,
  Evidence,
  Hypothesis,
  HypothesisTree as Tree,
  Stage,
  TimelineEvent,
} from "../types";
import { Badge, Icon } from "./ui";
import type { IconName } from "./ui";

// ───────────────────────── stepper ─────────────────────────
const STEP_ICON: Record<Stage["state"], IconName> = {
  done: "check",
  paused: "pause",
  failed: "x",
  denied: "x",
  pending: "minus",
  skipped: "minus",
};
// Kept to one short word: twelve steps share the row, and a caption that
// wraps runs into its neighbours.
const STEP_WORD: Record<Stage["state"], string> = {
  done: "done",
  paused: "waiting",
  failed: "stopped",
  denied: "denied",
  pending: "pending",
  skipped: "not run",
};

export function Stepper({ stages, reveal }: { stages: Stage[]; reveal?: boolean }) {
  return (
    <ol className="stepper" aria-label="Pipeline stages">
      {stages.map((stage, index) => (
        <li
          key={stage.id}
          className={`step ${stage.state} ${reveal ? "reveal" : ""}`}
          style={reveal ? { animationDelay: `${index * 70}ms` } : undefined}
          title={stage.error}
        >
          <span className="step-dot">
            <Icon name={stage.gate && stage.state === "pending" ? "lock" : STEP_ICON[stage.state]} />
          </span>
          <span className="step-label">{stage.label}</span>
          <span className="step-meta">
            {stage.state === "done" && stage.duration_ms !== undefined
              ? duration(stage.duration_ms)
              : stage.state === "done"
                ? "passed"
                : STEP_WORD[stage.state]}
          </span>
        </li>
      ))}
    </ol>
  );
}

// ───────────────────────── evidence refs ─────────────────────────
export interface RefControl {
  lit: string | null;
  focus: (ref: string) => void;
}

export function ERef({ id, control }: { id: string; control: RefControl }) {
  return (
    <button
      type="button"
      className={`eref ${control.lit === id ? "lit" : ""}`}
      onClick={() => control.focus(id)}
      aria-label={`Show evidence ${id}`}
    >
      {id}
    </button>
  );
}

/** Prose with [E3]-style citations turned into buttons that light up the
 *  matching evidence row. */
export function Cited({ text, control }: { text: string; control: RefControl }) {
  const parts = text.split(/(\[E\d+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const match = /^\[(E\d+)\]$/.exec(part);
        return match ? <ERef key={i} id={match[1]} control={control} /> : <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

// ───────────────────────── hypothesis tree ─────────────────────────
const H_STATUS: Record<Hypothesis["status"], { tone: "good" | "crit" | "neutral"; icon: IconName; label: string }> = {
  VALIDATED: { tone: "good", icon: "check", label: "Validated" },
  INVALIDATED: { tone: "crit", icon: "x", label: "Ruled out" },
  INCONCLUSIVE: { tone: "neutral", icon: "question", label: "Inconclusive" },
};
const C_ICON: Record<Criterion["status"], IconName> = {
  supported: "check",
  refuted: "x",
  unknown: "question",
};
const C_WORD: Record<Criterion["status"], string> = {
  supported: "Supported",
  refuted: "Refuted",
  unknown: "Unknown",
};

function HypothesisCard({
  hypothesis,
  control,
  startOpen,
}: {
  hypothesis: Hypothesis;
  control: RefControl;
  startOpen: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const status = H_STATUS[hypothesis.status];
  const panel = `criteria-${hypothesis.id}`;
  return (
    <div className={`hyp ${hypothesis.status}`}>
      <button
        type="button"
        className="hyp-head"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen(!open)}
      >
        <Icon name="chevron" className={`chev ${open ? "open" : ""}`} />
        <span className="hyp-id">{hypothesis.id}</span>
        <span className="hyp-title">
          {hypothesis.title}
          <small>{hypothesis.failure_class}</small>
        </span>
        <span className="hyp-score">
          {hypothesis.supported}/{hypothesis.required} criteria
        </span>
        <Badge tone={status.tone} icon={status.icon}>
          {status.label}
        </Badge>
      </button>
      {open && (
        <ul className="criteria" id={panel}>
          {hypothesis.criteria.map((criterion) => (
            <li key={criterion.id} className={`criterion ${criterion.status}`}>
              <Icon name={C_ICON[criterion.status]} />
              <div>
                <div className="criterion-claim">
                  <span className="sr-only">{C_WORD[criterion.status]}: </span>
                  {criterion.claim}{" "}
                  {criterion.evidence.map((ref) => (
                    <ERef key={ref} id={ref} control={control} />
                  ))}
                  {!criterion.required && (
                    <span className="dim small" title="Advisory context never decides a diagnosis">
                      {" "}
                      · advisory
                    </span>
                  )}
                </div>
                <div className="criterion-note">
                  <b>{C_WORD[criterion.status]}</b> — {criterion.note}
                </div>
                {criterion.gather && <div className="criterion-gather">To settle it: gather {criterion.gather}.</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function HypothesisTree({ tree, control }: { tree: Tree; control: RefControl }) {
  return (
    <div>
      <p className="muted" style={{ marginBottom: "0.75rem" }}>
        {tree.summary}
      </p>
      {tree.hypotheses.map((hypothesis, index) => (
        <HypothesisCard
          key={hypothesis.id}
          hypothesis={hypothesis}
          control={control}
          // Open the one that matters: the validated cause, or — when nothing
          // validated — the leading candidate, so the reason is one glance away.
          startOpen={hypothesis.status === "VALIDATED" || (tree.validated === null && index === 0)}
        />
      ))}
      {tree.gather_next.length > 0 && (
        <div className="note warn" style={{ marginTop: "0.75rem" }}>
          <Icon name="search" />
          <div className="note-body">
            <h3>Evidence that would settle it</h3>
            <ul style={{ paddingLeft: "1.1rem", marginTop: "0.2rem" }}>
              {tree.gather_next.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {!tree.pipeline.agrees && (
        <div className="note crit" style={{ marginTop: "0.75rem" }} role="alert">
          <Icon name="warning" />
          <div className="note-body">
            <h3>The tree disagrees with the model's diagnosis</h3>
            <p>
              The model diagnosed <code>{tree.pipeline.failure_class ?? "nothing"}</code>, but the deterministic
              criteria validate <code>{tree.validated ?? "nothing"}</code>. Treat the diagnosis as unconfirmed.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ───────────────────────── evidence table ─────────────────────────
function classification(value: string): ReactNode {
  if (value === "QUARANTINED") {
    return (
      <Badge tone="crit" icon="lock" title="Suspected prompt injection — withheld from every model prompt">
        Quarantined
      </Badge>
    );
  }
  if (value === "CONFIDENTIAL" || value === "RESTRICTED") {
    return (
      <Badge tone="warn" icon="lock" title="Above the model's clearance — withheld from prompts, visible to you">
        {value.charAt(0) + value.slice(1).toLowerCase()}
      </Badge>
    );
  }
  return <span className="dim small">{value.toLowerCase()}</span>;
}

export function EvidenceTable({ evidence, control }: { evidence: Evidence[]; control: RefControl }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Ref</th>
            <th>Kind</th>
            <th>What it says</th>
            <th>Class</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((item) => (
            <tr
              key={item.id}
              id={`evidence-${item.id}`}
              className={control.lit === item.id ? "lit" : ""}
            >
              <td>
                <ERef id={item.id} control={control} />
              </td>
              <td className="nowrap">
                {item.kind}
                <div className="dim small">{item.source}</div>
              </td>
              <td>
                {item.excerpt}
                <div>
                  <code>{item.ref}</code>
                </div>
              </td>
              <td>{classification(item.classification)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ───────────────────────── timeline ─────────────────────────
const TL_ICON: Record<string, IconName> = {
  workflow: "play",
  tool: "tool",
  write: "zap",
  model: "cpu",
  decision: "search",
  stage: "check",
  gate: "pause",
  denied: "x",
  failed: "warning",
  fallback: "undo",
  retry: "refresh",
  compensation: "undo",
  human: "user",
  other: "minus",
};

export const TIMELINE_FILTERS: { id: string; label: string; kinds: string[] }[] = [
  { id: "all", label: "Everything", kinds: [] },
  { id: "decisions", label: "Decisions", kinds: ["decision", "gate", "human", "denied"] },
  { id: "tools", label: "Tool calls", kinds: ["tool", "write"] },
  { id: "model", label: "Model calls", kinds: ["model", "fallback"] },
  { id: "stages", label: "Stages", kinds: ["workflow", "stage", "failed", "retry", "compensation"] },
];

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.seq} className={`tl-item ${event.kind}`}>
          <span className="tl-time">+{duration(event.t_ms)}</span>
          <span className="tl-dot">
            <Icon name={TL_ICON[event.kind] ?? "minus"} />
          </span>
          <div>
            <div className="tl-title">
              {event.title}
              <span className="tl-actor">{event.actor}</span>
            </div>
            {event.detail && <div className="tl-detail">{event.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ───────────────────────── markdown (postmortem) ─────────────────────────
function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let n = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    out.push(
      token.startsWith("**") ? (
        <strong key={`${keyBase}-${n}`}>{token.slice(2, -2)}</strong>
      ) : (
        <code key={`${keyBase}-${n}`}>{token.slice(1, -1)}</code>
      ),
    );
    last = match.index + token.length;
    n += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (line: string): string[] =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.trim());

/** A deliberately small markdown renderer for the postmortem the platform
 *  generates (headings, lists, task items, tables, bold, code). It builds
 *  React elements, never HTML strings, so document content cannot inject
 *  markup. */
export function Markdown({ source }: { source: string }) {
  const lines = source.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (!line.trim()) {
      i += 1;
    } else if (line.startsWith("# ")) {
      blocks.push(<h1 key={key}>{inline(line.slice(2), key)}</h1>);
      i += 1;
    } else if (line.startsWith("## ")) {
      blocks.push(<h2 key={key}>{inline(line.slice(3), key)}</h2>);
      i += 1;
    } else if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const body = rows.filter((r) => !/^\|[\s|:-]+\|$/.test(r.trim()));
      const [head, ...rest] = body;
      blocks.push(
        <div className="table-wrap" key={key}>
          <table>
            <thead>
              <tr>{cells(head).map((c, n) => <th key={n}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rest.map((row, r) => (
                <tr key={r}>{cells(row).map((c, n) => <td key={n}>{inline(c, `${key}-${r}-${n}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (line.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) items.push(lines[i++].slice(2));
      blocks.push(
        <ul key={key} style={items[0].startsWith("[ ] ") ? { listStyle: "none", paddingLeft: 0 } : undefined}>
          {items.map((item, n) =>
            item.startsWith("[ ] ") ? (
              <li key={n}>
                <label>
                  <input type="checkbox" />
                  {inline(item.slice(4), `${key}-${n}`)}
                </label>
              </li>
            ) : (
              <li key={n}>{inline(item, `${key}-${n}`)}</li>
            ),
          )}
        </ul>,
      );
    } else {
      blocks.push(<p key={key}>{inline(line, key)}</p>);
      i += 1;
    }
  }
  return <div className="md">{blocks}</div>;
}
