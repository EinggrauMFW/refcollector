import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ApiError } from "../api/api";
import type { CitationStatus, DisplayStatus, Intent, JobStatus } from "../api/types";
import { jobProgress, summaryLabel } from "../lib/format";
import { STATUS_META, STATUS_ORDER } from "../lib/status";
import { Icon } from "./Icon";

// Text plus icon, never colour alone.
export function StatusBadge({ status, count }: { status: DisplayStatus; count?: number }) {
  const m = STATUS_META[status];
  return (
    <span className={`badge badge-${m.tone}`} title={m.hint}>
      <Icon name={m.icon} size={14} spin={status === "working"} />
      <span>{count === undefined ? m.label : `${count} ${m.label.toLowerCase()}`}</span>
    </span>
  );
}

// Coverage of one paper's citations. Drawn as SVG so widths are attributes, not inline styles (CSP).
export function CoverageStrip({ counts, label }: { counts: Partial<Record<CitationStatus, number>>; label: string }) {
  const total = STATUS_ORDER.reduce((n, s) => n + (counts[s] ?? 0), 0);
  if (total === 0) return null;
  const W = 96;
  const present = STATUS_ORDER.filter((s) => (counts[s] ?? 0) > 0);
  const segs = present.map((s, i) => {
    const before = present.slice(0, i).reduce((n, t) => n + (counts[t] ?? 0), 0);
    return { s, x: (before / total) * W, w: Math.max(1.5, ((counts[s] ?? 0) / total) * W - 1) };
  });
  return (
    <svg className="strip" width={W} height="6" viewBox={`0 0 ${W} 6`} role="img" aria-label={label}>
      {segs.map(({ s, x, w }) => (
        <rect key={s} className={`seg seg-${s}`} x={x} y="0" width={w} height="6" />
      ))}
    </svg>
  );
}

export function JobBar({ status, error }: { status: JobStatus | null; error: ApiError | null }) {
  const p = status ? jobProgress(status) : { label: "Starting", done: 0, total: 0 };
  const determinate = p.total > 1;
  return (
    <div className="jobbar" role="status">
      {determinate ? <progress max={p.total} value={p.done} aria-label={p.label} /> : <progress aria-label={p.label} />}
      <span className="jobbar-text">
        {p.label}
        {determinate && <span className="num"> · {p.done} of {p.total}</span>}
      </span>
      {error && <span className="jobbar-err">Lost contact with the server. Retrying.</span>}
    </div>
  );
}

export function ErrorBlock({ error, onRetry, title }: { error: ApiError; onRetry?: () => void; title?: string }) {
  return (
    <div className="block-error" role="alert">
      <Icon name="alert" />
      <div className="block-error-text">
        <p>
          {title ? `${title} ` : ""}
          {error.message}
        </p>
        {error.recovery_hint && <p className="muted">{error.recovery_hint}</p>}
      </div>
      {onRetry && (
        <button type="button" className="btn btn-text" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({ tone = "neutral", children, onDismiss }: { tone?: "neutral" | "warning" | "negative"; children: ReactNode; onDismiss?: () => void }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "negative" ? "alert" : "status"}>
      <Icon name={tone === "negative" ? "x-circle" : "alert"} />
      <div className="notice-body">{children}</div>
      {onDismiss && (
        <button type="button" className="icon-btn" onClick={onDismiss} aria-label="Dismiss">
          <Icon name="close" />
        </button>
      )}
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skel-row" key={i}>
          <span className="skel skel-title" />
          <span className="skel skel-meta" />
        </div>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy path", disabledReason }: { text: string | null; label?: string; disabledReason?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const disabled = !text;
  const copy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("Copy this path:", text);
    }
  };
  return (
    <button type="button" className="btn" aria-disabled={disabled} title={disabled ? disabledReason : undefined} onClick={disabled ? undefined : copy}>
      <Icon name={copied ? "check" : "copy"} />
      <span>{copied ? "Copied" : label}</span>
    </button>
  );
}

// The readable lines of a confirm step. `summary` is only a fallback for a server without `description`.
export function IntentLines({ intent }: { intent: Intent }) {
  const lines = intent.description.length > 0 ? intent.description : Object.entries(intent.summary).map(([k, v]) => `${summaryLabel(k)}: ${String(v)}`);
  return (
    <ul className="intent-lines" aria-label="What this change does">
      {lines.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  );
}
