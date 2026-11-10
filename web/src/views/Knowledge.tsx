// Runbook search and the evaluation report.
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorNote,
  PageHead,
  Stat,
  useApp,
} from "../components/ui";
import { compact, failureClass, percent } from "../lib/format";
import type { Evals, Gate, Runbook } from "../types";

const QUERIES = [
  "OOMKilled pods after deploy",
  "TLS certificate expired handshake failures",
  "roll back a bad deployment",
  "p99 latency spike what changed",
];

export function Runbooks() {
  const { client, notify } = useApp();
  const [query, setQuery] = useState(QUERIES[0]);
  const [result, setResult] = useState<{ query: string; asked: string; results: Runbook[] } | null>(null);
  const [busy, setBusy] = useState(false);

  async function search(text: string): Promise<void> {
    if (!text.trim()) return;
    setBusy(true);
    try {
      const response = await client.searchRunbooks(text);
      setResult({ ...response, asked: text });
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason), "error");
    } finally {
      setBusy(false);
    }
  }

  const top = result?.results[0]?.score ?? 1;
  return (
    <>
      <PageHead title="Runbooks">
        Hybrid keyword + vector retrieval over operational runbooks and past postmortems. Every hit carries a{" "}
        <code>rag://doc#offset</code> reference — the same citation agents attach when they use it.
      </PageHead>

      <div className="stack">
        <Card>
          <form
            className="row"
            role="search"
            onSubmit={(event) => {
              event.preventDefault();
              void search(query);
            }}
          >
            <input
              className="input"
              style={{ flex: "1 1 18rem", width: "auto" }}
              value={query}
              aria-label="Search runbooks"
              placeholder="Describe the symptom…"
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button variant="primary" icon="search" busy={busy} type="submit">
              Search
            </Button>
          </form>
          <div className="row" style={{ marginTop: "0.65rem" }}>
            <span className="dim small">Try</span>
            {QUERIES.map((preset) => (
              <button
                key={preset}
                type="button"
                className="chip"
                onClick={() => {
                  setQuery(preset);
                  void search(preset);
                }}
              >
                {preset}
              </button>
            ))}
          </div>
        </Card>

        {result ? (
          <Card
            title={`${result.results.length} passages`}
            flush
            foot={
              result.query !== result.asked
                ? `Recorded demo: showing the transcript for “${result.query}”. Run the API locally to search freely.`
                : undefined
            }
          >
            <div className={`table-wrap ${busy ? "stale" : ""}`}>
              <table>
                <thead>
                  <tr>
                    <th>Runbook</th>
                    <th>Passage</th>
                    <th className="num">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((hit) => (
                    <tr key={hit.ref}>
                      <td style={{ minWidth: "13rem" }}>
                        <strong>{hit.title}</strong>
                        <div>
                          <code>{hit.ref}</code>
                        </div>
                      </td>
                      <td className="muted">{hit.excerpt}</td>
                      <td className="num" style={{ minWidth: "7rem" }}>
                        {hit.score.toFixed(3)}
                        <div className="meter-track" style={{ marginTop: "0.3rem" }} aria-hidden="true">
                          <div className="meter-fill" style={{ width: `${(hit.score / top) * 100}%` }} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : (
          <Card>
            <Empty icon="search" title="Search the knowledge base">
              Describe a symptom the way you would in an incident channel.
            </Empty>
          </Card>
        )}
      </div>
    </>
  );
}

// ───────────────────────── evaluation ─────────────────────────
function GateRow({ gate }: { gate: Gate }) {
  return (
    <li className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
      <Badge tone={gate.passed ? "good" : "crit"} icon={gate.passed ? "check" : "x"}>
        {gate.passed ? "Passed" : "Failed"}
      </Badge>
      <code>{gate.criterion}</code>
    </li>
  );
}

function Report({ evals }: { evals: Evals }) {
  const a = evals.aggregates as Record<string, number | boolean | null>;
  const ratio = (key: string): string => (typeof a[key] === "number" ? percent(a[key] as number) : "—");
  const gates = [evals.release_gate, evals.retrieval.gate, ...(evals.judge_gate ? [evals.judge_gate] : [])];
  const allPassed = gates.every((gate) => gate.passed);
  return (
    <div className="stack">
      <div className="stats">
        <Stat label="RCA precision@1" value={ratio("rca_precision_at_1")} hint="diagnosable scenarios" />
        <Stat label="Escalation correctness" value={ratio("escalation_correctness")} hint="must-escalate scenarios" />
        <Stat label="Plan step accuracy" value={ratio("plan_step_accuracy")} hint="exact Step Catalog match" />
        <Stat label="Citation faithfulness" value={ratio("citation_faithfulness")} hint="no invented [E…] refs" />
        <Stat
          label="Mean calibration error"
          value={typeof a.mean_calibration_error === "number" ? a.mean_calibration_error.toFixed(3) : "—"}
          hint="lower is better"
        />
        <Stat label="Tokens for the suite" value={compact(Number(a.total_tokens ?? 0))} hint={`${a.scenarios} scenarios`} />
      </div>

      <div className="grid two">
        <Card title="Golden scenarios — expected vs. actual" flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Expected</th>
                  <th>Actual</th>
                  <th title="What the Root Cause agent proposed. On an escalated row the pipeline rejected it.">
                    Model proposed
                  </th>
                  <th className="num">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {(evals.scenarios ?? []).map((row) => (
                  <tr key={row.scenario}>
                    <td>
                      {row.name}
                      <div className="dim small mono">{row.scenario}</div>
                    </td>
                    <td className="small">{row.expected_outcome}</td>
                    <td>
                      <Badge tone={row.outcome_correct ? "good" : "crit"} icon={row.outcome_correct ? "check" : "x"}>
                        {row.outcome}
                      </Badge>
                    </td>
                    <td className="small">{failureClass(row.predicted_class)}</td>
                    <td className="num">{percent(row.confidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="stack">
          <Card title="Release gates">
            <p style={{ marginBottom: "0.6rem" }}>
              <Badge tone={allPassed ? "good" : "crit"} icon={allPassed ? "check" : "x"}>
                {allPassed ? "All gates passed — releasable" : "A gate failed — not releasable"}
              </Badge>
            </p>
            <ul className="list-plain">
              {gates.map((gate) => (
                <GateRow key={gate.criterion} gate={gate} />
              ))}
            </ul>
          </Card>
          <Card title="Trust ladder" flush>
            <table>
              <thead>
                <tr>
                  <th>Failure class</th>
                  <th className="num">Episodes</th>
                  <th>Stage</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(evals.trust_ladder).map(([name, entry]) => (
                  <tr key={name}>
                    <td>
                      {failureClass(name)}
                      <div className="dim small">precision {percent(entry.precision)}</div>
                    </td>
                    <td className="num">{entry.episodes}</td>
                    <td className="small">{entry.stage.split(" (")[0]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>

      <Card
        title="Retrieval quality, per chunking strategy"
        flush
        foot="The gate reads the lower bound of the 95% bootstrap interval, so a regression has to move the whole interval rather than hide in small-sample noise."
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="num">Chunks</th>
                <th className="num">P@1</th>
                <th className="num">95% CI</th>
                <th className="num">Paraphrase P@1</th>
                <th className="num">Recall@5</th>
                <th className="num">MRR</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(evals.retrieval.strategies).map(([name, m]) => (
                <tr key={name}>
                  <td>
                    {name} {name === evals.retrieval.best && <Badge tone="accent">best</Badge>}
                  </td>
                  <td className="num">{m.chunks}</td>
                  <td className="num">{m.precision_at_1.toFixed(3)}</td>
                  <td className="num">
                    {m.precision_at_1_ci95 ? `${m.precision_at_1_ci95[0].toFixed(3)} – ${m.precision_at_1_ci95[1].toFixed(3)}` : "—"}
                  </td>
                  <td className="num">{m.paraphrase_precision_at_1?.toFixed(3) ?? "—"}</td>
                  <td className="num">{m.recall_at_5.toFixed(3)}</td>
                  <td className="num">{m.mrr.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

export function Evaluation() {
  const { client } = useApp();
  const [evals, setEvals] = useState<Evals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setEvals(await client.evals());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead
        title="Evaluation"
        actions={
          <Button variant="primary" icon="play" busy={busy} onClick={() => void run()}>
            {evals ? "Run again" : "Run evaluation"}
          </Button>
        }
      >
        The release gates CI enforces: golden scenarios replayed through the real workflow — including adversarial
        ones that must escalate — and retrieval scored against a labeled query set.
      </PageHead>
      {error && <ErrorNote error={error} retry={() => void run()} />}
      {evals ? (
        <div className={busy ? "stale" : ""}>
          <Report evals={evals} />
        </div>
      ) : (
        !error && (
          <Card>
            <Empty icon="evals" title={busy ? "Replaying the golden scenarios…" : "No report yet"}>
              {busy
                ? "Seven scenarios through the full pipeline, then the labeled retrieval set."
                : "Running the suite takes a few seconds: it replays every golden scenario and scores retrieval."}
            </Empty>
          </Card>
        )
      )}
    </>
  );
}
