import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { detectMode, getToken, liveClient, setToken } from "./api";
import type { Client } from "./api";
import { AppProvider, Badge, Button, Card, Icon, Loading, Note, Toasts } from "./components/ui";
import type { IconName, Toast } from "./components/ui";
import { createDemoClient, DEMO } from "./demo";
import { href, useRoute } from "./lib/router";
import type { Mode, Stats, WhoAmI } from "./types";
import { Alerts } from "./views/Alerts";
import { ChangeRisk } from "./views/ChangeRisk";
import { IncidentDetail } from "./views/IncidentDetail";
import { Incidents } from "./views/Incidents";
import { Evaluation, Runbooks } from "./views/Knowledge";
import { Overview } from "./views/Overview";
import { Platform } from "./views/Platform";
import { TopologyView } from "./views/Topology";

type Theme = "system" | "light" | "dark";

const NAV: { label: string; items: { view: string; label: string; icon: IconName }[] }[] = [
  {
    label: "Respond",
    items: [
      { view: "", label: "Overview", icon: "overview" },
      { view: "alerts", label: "Alerts", icon: "alerts" },
      { view: "incidents", label: "Incidents", icon: "incidents" },
    ],
  },
  {
    label: "Prevent",
    items: [
      { view: "changes", label: "Change risk", icon: "change" },
      { view: "topology", label: "Topology", icon: "topology" },
    ],
  },
  {
    label: "Know",
    items: [
      { view: "runbooks", label: "Runbooks", icon: "runbooks" },
      { view: "evals", label: "Evaluation", icon: "evals" },
      { view: "platform", label: "Governance", icon: "platform" },
    ],
  },
];

function readTheme(): Theme {
  try {
    const stored = window.localStorage.getItem("aetherops.theme");
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

const systemDark = (): boolean => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;

export function App() {
  const route = useRoute();
  const [mode, setMode] = useState<Mode>("checking");
  const [token, setTokenState] = useState(getToken);
  const [me, setMe] = useState<WhoAmI | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [nav, setNav] = useState<Stats | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const nextToast = useRef(1);

  // LIVE when a backend answers /health (served by the API); DEMO otherwise.
  useEffect(() => {
    void detectMode().then(setMode);
  }, []);

  const demo = useMemo(() => createDemoClient(), []);
  const client: Client | null = mode === "live" ? liveClient : mode === "demo" ? demo : null;

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") window.localStorage.removeItem("aetherops.theme");
      else window.localStorage.setItem("aetherops.theme", theme);
    } catch {
      /* storage blocked: the choice lasts for this page view */
    }
  }, [theme]);

  const notify = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = nextToast.current++;
    setToasts((list) => [...list.slice(-3), { id, kind, text }]);
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4200);
  }, []);

  // Who am I? Re-asked whenever the token changes, so the console's buttons
  // always reflect what this credential is actually allowed to do.
  useEffect(() => {
    if (!client) return;
    let alive = true;
    client
      .whoami()
      .then((who) => {
        if (!alive) return;
        setMe(who);
        setAuthError(null);
      })
      .catch((reason: unknown) => {
        if (!alive) return;
        setMe(null);
        setAuthError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      alive = false;
    };
  }, [client, token]);

  // Sidebar counters: refreshed on every navigation.
  useEffect(() => {
    if (!client || !me?.grants.includes("read")) return;
    let alive = true;
    client
      .stats()
      .then((stats) => alive && setNav(stats))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [client, me, route.view, route.id]);

  const context = useMemo(
    () =>
      client && {
        client,
        me,
        can: (grant: string) => Boolean(me?.grants.includes(grant)),
        notify,
      },
    [client, me, notify],
  );

  const dark = theme === "dark" || (theme === "system" && systemDark());
  const waiting = nav?.incidents.by_outcome["awaiting-approval"] ?? 0;
  const openGroups = nav?.alerts.open_groups ?? 0;
  const counts: Record<string, number> = { incidents: waiting, alerts: openGroups };

  function view() {
    switch (route.view) {
      case "":
        return <Overview />;
      case "alerts":
        return <Alerts />;
      case "incidents":
        return route.id ? <IncidentDetail id={route.id} /> : <Incidents />;
      case "changes":
        return <ChangeRisk />;
      case "topology":
        return <TopologyView />;
      case "runbooks":
        return <Runbooks />;
      case "evals":
        return <Evaluation />;
      case "platform":
        return <Platform />;
      default:
        return (
          <Card>
            <Note tone="accent" icon="question" title="There is no page here">
              <p>
                <a href={href("")}>Back to the overview</a>
              </p>
            </Note>
          </Card>
        );
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href={href("")}>
          <span className="brand-mark">
            {/* concentric aperture — the control plane closing the loop */}
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="1.6" strokeOpacity="0.85" />
              <path d="M12 3a9 9 0 0 1 7.8 13.5L12 12Z" fill="#fff" fillOpacity="0.9" />
              <circle cx="12" cy="12" r="2.4" fill="#fff" />
            </svg>
          </span>
          <span>
            <div className="brand-name">AetherOps</div>
            <div className="brand-tag">Incident remediation</div>
          </span>
        </a>

        <nav className="nav-groups stack" style={{ gap: "0.9rem" }} aria-label="Primary">
          {NAV.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => (
                <a
                  key={item.view}
                  className="nav-item"
                  href={href(item.view)}
                  aria-current={route.view === item.view ? "page" : undefined}
                >
                  <Icon name={item.icon} />
                  {item.label}
                  {counts[item.view] > 0 && (
                    <span className={`nav-count ${item.view === "incidents" ? "hot" : ""}`}>{counts[item.view]}</span>
                  )}
                </a>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className={`mode ${mode === "checking" ? "" : mode}`} role="status">
            <span className="mode-dot" />
            {mode === "checking" ? "CONNECTING…" : mode === "live" ? "LIVE · REAL API" : "DEMO · RECORDED"}
          </div>
          {mode === "live" && (
            <label className="token-field">
              API token
              <input
                className="input"
                type="password"
                value={token}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setToken(event.target.value);
                  setTokenState(event.target.value);
                }}
              />
            </label>
          )}
          <div className="foot-row">
            {me ? <Badge icon="user">{me.role}</Badge> : <span className="dim">not signed in</span>}
            <Button
              variant="quiet sm icon-only"
              aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
              title={dark ? "Light theme" : "Dark theme"}
              onClick={() => setTheme(dark ? "light" : "dark")}
            >
              <Icon name={dark ? "sun" : "moon"} />
            </Button>
          </div>
          <a href="https://github.com/Divya1S/Aether-Ops" rel="noreferrer">
            Source on GitHub ↗
          </a>
        </div>
      </aside>

      <main className="main">
        <div className="main-inner">
          {mode === "demo" && (
            <div className="demo-banner">
              <Icon name="play" />
              <span>
                <b>Recorded demo.</b> No backend is running here, so the console replays a transcript captured from the
                real API (v{DEMO.version}, {DEMO.recorded_at.slice(0, 10)}). Run <code>make serve</code> to drive it
                live.
              </span>
            </div>
          )}
          {!context ? (
            <Loading label="Connecting…" />
          ) : authError ? (
            <Note tone="crit" icon="lock" title="This token was not accepted">
              <p>
                {authError}. Enter a valid API token in the sidebar — for a local <code>make serve</code> that is{" "}
                <code>aetherops-dev</code>.
              </p>
            </Note>
          ) : !me ? (
            <Loading label="Checking access…" />
          ) : me.grants.includes("read") ? (
            // Re-mount the view when the credential changes so nothing stale
            // from a more privileged token stays on screen.
            <AppProvider value={context} key={`${mode}:${token}`}>
              {view()}
            </AppProvider>
          ) : (
            <Note tone="warn" icon="lock" title={`The ${me.role} role cannot read the console`}>
              <p>This credential is scoped to alert intake only. Use a viewer, operator, approver or admin token.</p>
            </Note>
          )}
        </div>
      </main>
      <Toasts toasts={toasts} />
    </div>
  );
}
