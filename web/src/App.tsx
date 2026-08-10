import { useEffect, useState } from "react";
import type { Mode } from "./types";
import { detectMode, setToken } from "./api";
import { IncidentTab } from "./components/Incident";
import { ChangeTab } from "./components/ChangeRisk";
import { RunbookTab } from "./components/Runbooks";
import { EvalTab } from "./components/Evaluation";

type TabId = "incident" | "change" | "runbooks" | "evals";

const TABS: { id: TabId; label: string }[] = [
  { id: "incident", label: "Incident remediation" },
  { id: "change", label: "Change risk" },
  { id: "runbooks", label: "Runbook search" },
  { id: "evals", label: "Evaluation" },
];

export function App() {
  const [mode, setMode] = useState<Mode>("checking");
  const [tab, setTab] = useState<TabId>("incident");
  const [token, setTokenState] = useState("aetherops-dev");

  // LIVE when a backend answers /health (served locally); DEMO otherwise.
  useEffect(() => {
    detectMode().then(setMode);
  }, []);

  const live = mode === "live";
  const badgeClass = mode === "live" ? "live" : mode === "demo" ? "demo" : "";
  const badgeText =
    mode === "checking"
      ? "checking…"
      : mode === "live"
        ? "LIVE — driving the real API"
        : "DEMO — recorded transcript";

  return (
    <>
      <header>
        <h1>AetherOps</h1>
        <span className={`badge ${badgeClass}`}>{badgeText}</span>
        {live && (
          <span className="badge">
            token{" "}
            <input
              value={token}
              size={12}
              onChange={(e) => {
                setTokenState(e.target.value);
                setToken(e.target.value);
              }}
            />
          </span>
        )}
        <span className="sub">
          React + TypeScript operator console for an autonomous
          incident-remediation &amp; change-intelligence platform. Agents
          diagnose from cited evidence; you approve reversible action at a
          policy gate; every claim is auditable.{" "}
          <a href="https://github.com/Divya1S/Aether-Ops">source</a>
        </span>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <div
            key={t.id}
            className={`tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </div>
        ))}
      </nav>

      {tab === "incident" && <IncidentTab live={live} />}
      {tab === "change" && <ChangeTab live={live} />}
      {tab === "runbooks" && <RunbookTab live={live} />}
      {tab === "evals" && <EvalTab live={live} />}
    </>
  );
}
