import { useEffect, useState } from "react";
import type { Mode } from "./types";
import { detectMode, setToken } from "./api";
import { IncidentTab } from "./components/Incident";
import { ChangeTab } from "./components/ChangeRisk";
import { RunbookTab } from "./components/Runbooks";
import { EvalTab } from "./components/Evaluation";

type TabId = "incident" | "change" | "runbooks" | "evals";

type IconProps = { d: string };
const Icon = ({ d }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

// Inline, CSP-safe icons (no icon font / no external assets).
const ICONS: Record<TabId, string> = {
  incident: "M12 2 4 6v6c0 5 3.4 7.6 8 10 4.6-2.4 8-5 8-10V6l-8-4Z", // shield
  change: "M4 17 10 11l4 4 6-7 M14 6h6v6", // trend line
  runbooks: "M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z M14 3v5h5", // doc
  evals: "M9 11l3 3 7-7 M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9", // check
};

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
  const statusClass = mode === "live" ? "live" : mode === "demo" ? "demo" : "";
  const statusText =
    mode === "checking"
      ? "connecting…"
      : mode === "live"
        ? "LIVE · driving the real API"
        : "DEMO · recorded transcript";

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            {/* concentric aperture — the control plane closing the loop */}
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="1.6"
                      strokeOpacity="0.85" />
              <path d="M12 3a9 9 0 0 1 7.8 13.5L12 12Z" fill="#fff"
                    fillOpacity="0.9" />
              <circle cx="12" cy="12" r="2.4" fill="#fff" />
            </svg>
          </span>
          <span className="brand-text">
            <h1>AetherOps</h1>
            <span className="tag">Autonomous incident remediation</span>
          </span>
        </div>

        <span className={`status ${statusClass}`}>
          <span className="status-dot" />
          {statusText}
        </span>
        {live && (
          <label className="token-field">
            token
            <input
              value={token}
              onChange={(e) => {
                setTokenState(e.target.value);
                setToken(e.target.value);
              }}
            />
          </label>
        )}

        <p className="app-desc">
          Operator console for an autonomous incident-remediation &amp;
          change-intelligence platform. Agents diagnose from cited evidence;
          you approve reversible action at a policy gate; every claim is
          auditable. <a href="https://github.com/Divya1S/Aether-Ops">Source ↗</a>
        </p>
      </header>

      <nav className="nav">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`nav-item ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id ? "page" : undefined}
          >
            <Icon d={ICONS[t.id]} />
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "incident" && <IncidentTab live={live} />}
      {tab === "change" && <ChangeTab live={live} />}
      {tab === "runbooks" && <RunbookTab live={live} />}
      {tab === "evals" && <EvalTab live={live} />}
    </>
  );
}
