// Pure formatting helpers — no DOM, unit-tested in lib.test.ts.

/** 1284 -> "1,284"; 12940 -> "12.9K"; 4200000 -> "4.2M". */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${trim(value / 1_000_000)}M`;
  if (abs >= 10_000) return `${trim(value / 1_000)}K`;
  return Math.round(value).toLocaleString("en-US");
}

const trim = (n: number): string => n.toFixed(1).replace(/\.0$/, "");

/** Durations as the smallest readable unit: 0.4 ms, 38 ms, 1.2 s, 3m 05s. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(1)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${trim(ms / 1000)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  // Per-incident model spend is fractions of a cent to a few cents: below a
  // dollar, two decimals would round the signal away.
  return value > 0 && value < 1 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function percent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** Epoch seconds -> "14:02:07" in the viewer's locale-independent 24h form. */
export function clock(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Epoch seconds -> "just now" / "4m ago" / "3h ago" / "2d ago". */
export function ago(epochSeconds: number, nowMs: number = Date.now()): string {
  const seconds = Math.max(0, Math.round(nowMs / 1000 - epochSeconds));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86_400)}d ago`;
}

/** "deploy-regression/memory" -> "Deploy regression · memory". */
export function failureClass(value: string | null | undefined): string {
  if (!value) return "—";
  const [family, detail] = value.split("/");
  const words = family.replace(/-/g, " ");
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  return detail ? `${label} · ${detail}` : label;
}

/** Minutes-of-day from the first HH:MM in a timestamp ("14:05Z",
 *  "2026-08-07T14:02:00Z"); null when there is none. Mirrors
 *  reasoning/signals.py:minute_of_day so the chart orders events exactly as
 *  the Reviewer's temporal-precedence check does. */
export function minuteOfDay(ts: string | null | undefined): number | null {
  const match = /(\d{1,2}):(\d{2})/.exec(ts ?? "");
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

export function hhmm(minute: number): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(Math.floor(minute / 60) % 24)}:${pad(minute % 60)}`;
}

/** Round an axis maximum up to a clean tick step; returns the ticks. */
export function niceTicks(max: number, target = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? rough;
  const ticks: number[] = [];
  for (let value = 0; value < max + step - 1e-9; value += step) {
    ticks.push(Number(value.toFixed(6)));
  }
  return ticks;
}
