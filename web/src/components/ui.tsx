import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { Client } from "../api";
import type { Outcome, WhoAmI } from "../types";

// ───────────────────────── icons (inline, CSP-safe) ─────────────────────────
const PATHS = {
  overview: "M4 5h7v7H4z M13 5h7v4h-7z M13 11h7v8h-7z M4 14h7v5H4z",
  alerts: "M6 9a6 6 0 1 1 12 0c0 5 2 6 2 7H4c0-1 2-2 2-7Z M10 19a2 2 0 0 0 4 0",
  incidents: "M12 3 4 6.5v5.2c0 4.6 3.3 7.4 8 9.3 4.7-1.9 8-4.7 8-9.3V6.5L12 3Z",
  topology: "M12 4v5 M6 15v-3h12v3 M9.5 4h5v5h-5z M3.5 15h5v5h-5z M15.5 15h5v5h-5z",
  change: "M4 17 10 11l4 4 6-7 M15 8h5v5",
  runbooks: "M5 4h10l4 4v12H5z M15 4v4h4 M8.5 12h7 M8.5 16h7",
  evals: "M4 12l4 4 8-9 M4 20h16",
  platform: "M5 7h14 M5 12h14 M5 17h14 M9 5v4 M15 10v4 M11 15v4",
  check: "M5 12.5 10 17.5 19 7.5",
  x: "M6 6 18 18 M18 6 6 18",
  question: "M9.2 9.3a2.9 2.9 0 1 1 4.3 2.5c-.9.6-1.5 1.2-1.5 2.4 M12 17.6v.2",
  pause: "M9 6v12 M15 6v12",
  play: "M8 5.5v13l10.5-6.5z",
  minus: "M6 12h12",
  chevron: "M9 6l6 6-6 6",
  external: "M14 5h5v5 M19 5l-8 8 M11 7H6v11h11v-5",
  download: "M12 4v11 M7.5 11 12 15.5 16.5 11 M5 19h14",
  copy: "M9 9h10v10H9z M6 15H5V5h10v1",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4 7 17 M17 7l1.4-1.4",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z",
  up: "M7 11v8H4v-8z M7 11l4-7c1.5 0 2.5 1 2.5 2.5L13 10h5.5c1 0 1.7.9 1.5 1.9l-1.2 6c-.2.7-.8 1.1-1.5 1.1H7",
  down: "M7 13V5H4v8z M7 13l4 7c1.5 0 2.5-1 2.5-2.5L13 14h5.5c1 0 1.7-.9 1.5-1.9l-1.2-6C18.6 5.4 18 5 17.3 5H7",
  clock: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z M12 8v4.3l2.8 1.7",
  warning: "M12 4 3 19.5h18L12 4Z M12 10v4.5 M12 17.2v.2",
  search: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Z M15.5 15.5 20 20",
  refresh: "M19 12a7 7 0 1 1-2.1-5 M19 4.5V8h-3.5",
  zap: "M13 3 5 13.5h6L10 21l8-10.5h-6z",
  tool: "M14.5 6.5a3.8 3.8 0 0 0 4.9 4.9L21 13l-8 8-2.5-2.5 8-8 M10.5 13.5 4 7l3-3 6.5 6.5",
  cpu: "M8 8h8v8H8z M5 10h3 M5 14h3 M16 10h3 M16 14h3 M10 5v3 M14 5v3 M10 16v3 M14 16v3",
  flag: "M6 20V5 M6 5h11l-2 3.5 2 3.5H6",
  user: "M12 12a3.6 3.6 0 1 0 0-7.2 3.6 3.6 0 0 0 0 7.2Z M5 19.5c1-3.3 3.6-5 7-5s6 1.7 7 5",
  lock: "M7 11h10v8H7z M9 11V8a3 3 0 0 1 6 0v3",
  link: "M10 14a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 0 0-5.7-5.7L11.5 6.8 M14 10a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 0 0 5.7 5.7L12.5 17.2",
  undo: "M9 7 5 11l4 4 M5 11h9a5 5 0 0 1 0 10h-2",
  book: "M5 5.5C7.5 4.5 10 4.5 12 6c2-1.5 4.5-1.5 7-.5v13c-2.5-1-5-1-7 .5-2-1.5-4.5-1.5-7-.5z M12 6v13",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  return (
    <svg
      className={`icon ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

// ───────────────────────── app context ─────────────────────────
export interface Toast {
  id: number;
  kind: "info" | "error" | "success";
  text: string;
}

export interface AppContext {
  client: Client;
  me: WhoAmI | null;
  can: (grant: string) => boolean;
  notify: (text: string, kind?: Toast["kind"]) => void;
}

const Ctx = createContext<AppContext | null>(null);
export const AppProvider = Ctx.Provider;

export function useApp(): AppContext {
  const value = useContext(Ctx);
  if (!value) throw new Error("useApp outside AppProvider");
  return value;
}

/** Load async data, keeping the previous value on refetch (no skeleton
 *  flash, no layout jump) and exposing a manual reload. */
export function useAsync<T>(
  load: () => Promise<T>,
  deps: unknown[],
): { data: T | null; error: string | null; loading: boolean; reload: () => void; set: (value: T) => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const call = ++latest.current;
    setLoading(true);
    load()
      .then((value) => {
        if (call !== latest.current) return; // a newer request superseded us
        setData(value);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (call !== latest.current) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (call === latest.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload, set: setData };
}

// ───────────────────────── primitives ─────────────────────────
type Tone = "neutral" | "accent" | "good" | "warn" | "serious" | "crit";

export function Badge({
  tone = "neutral",
  icon,
  mono,
  title,
  children,
}: {
  tone?: Tone;
  icon?: IconName;
  mono?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={`badge ${tone === "neutral" ? "" : tone} ${mono ? "mono" : ""}`} title={title}>
      {icon && <Icon name={icon} />}
      {children}
    </span>
  );
}

const OUTCOMES: Record<Outcome, { tone: Tone; icon: IconName; label: string }> = {
  running: { tone: "accent", icon: "refresh", label: "Running" },
  "awaiting-approval": { tone: "warn", icon: "pause", label: "Awaiting approval" },
  remediated: { tone: "good", icon: "check", label: "Remediated" },
  escalated: { tone: "serious", icon: "flag", label: "Escalated" },
  denied: { tone: "neutral", icon: "x", label: "Denied" },
  failed: { tone: "crit", icon: "warning", label: "Failed" },
};

export const outcomeLabel = (outcome: Outcome): string => OUTCOMES[outcome]?.label ?? outcome;

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const spec = OUTCOMES[outcome] ?? OUTCOMES.failed;
  return (
    <Badge tone={spec.tone} icon={spec.icon}>
      {spec.label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const level = severity.toLowerCase();
  const tone: Tone =
    level === "critical" || level === "sev1"
      ? "crit"
      : level === "high" || level === "sev2"
        ? "serious"
        : level === "warning" || level === "sev3"
          ? "warn"
          : "neutral";
  return (
    <Badge tone={tone} mono>
      {severity.toUpperCase()}
    </Badge>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const tone: Tone =
    risk === "CRITICAL" || risk === "HIGH" ? "crit" : risk === "MEDIUM" ? "warn" : risk === "LOW" ? "good" : "neutral";
  return (
    <Badge tone={tone} mono>
      {risk}
    </Badge>
  );
}

export function Card({
  title,
  actions,
  flush,
  foot,
  children,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  foot?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          <h2 className="card-title">{title}</h2>
          {actions && <div className="row tight">{actions}</div>}
        </header>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
      {foot && <footer className="card-foot">{foot}</footer>}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  text,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** The value is words, not a figure: set it smaller so it does not wrap. */
  text?: boolean;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${text ? "text" : ""}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Button({
  variant = "",
  busy,
  icon,
  children,
  ...rest
}: {
  variant?: string;
  busy?: boolean;
  icon?: IconName;
  children?: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" {...rest} className={`btn ${variant}`} disabled={rest.disabled || busy}>
      {busy ? <span className="spinner" /> : icon ? <Icon name={icon} /> : null}
      {children}
    </button>
  );
}

export interface TabSpec {
  id: string;
  label: string;
  count?: number;
}

export function Tabs({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: TabSpec[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const onKey = (event: React.KeyboardEvent): void => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = tabs.findIndex((t) => t.id === value);
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    event.preventDefault();
  };
  return (
    <div className="tabs" role="tablist" aria-label={label} onKeyDown={onKey}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          className="tab"
          aria-selected={tab.id === value}
          tabIndex={tab.id === value ? 0 : -1}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && <span className="nav-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function Note({
  tone,
  icon,
  title,
  children,
  actions,
}: {
  tone: "warn" | "good" | "crit" | "accent";
  icon: IconName;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={`note ${tone}`} role={tone === "crit" ? "alert" : undefined}>
      <Icon name={icon} />
      <div className="note-body">
        <h3>{title}</h3>
        {children}
        {actions && (
          <div className="row" style={{ marginTop: "0.65rem" }}>
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}

export function ErrorNote({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <Note
      tone="crit"
      icon="warning"
      title="That request failed"
      actions={retry && <Button onClick={retry} icon="refresh">Try again</Button>}
    >
      <p>{error}</p>
    </Note>
  );
}

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn quiet sm icon-only"
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1400);
        });
      }}
    >
      <Icon name={copied ? "check" : "copy"} />
    </button>
  );
}

export function PageHead({
  crumbs,
  title,
  children,
  actions,
}: {
  crumbs?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        {crumbs && <div className="crumbs">{crumbs}</div>}
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`}>
          <Icon name={toast.kind === "error" ? "warning" : toast.kind === "success" ? "check" : "zap"} />
          <span>{toast.text}</span>
        </div>
      ))}
    </div>
  );
}
