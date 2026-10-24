import type { Author, JobStatus } from "../api/types";

// "title_match" and "AUTHOR_CONFLICT" -> "Title match", "Author conflict"
export function humanize(reason: string): string {
  const s = reason.replace(/[_-]+/g, " ").trim().toLowerCase();
  return s ? s[0]!.toUpperCase() + s.slice(1) : s;
}

// Reason lists from the server can repeat a code. Show each once, in first-seen order.
export function reasonList(reasons: string[]): string[] {
  return [...new Set(reasons.map(humanize))];
}

// Failed file checks in plain words. The server sends check names ("identity") or reason codes
// ("VERSION_UNKNOWN_OR_OUTSIDE_POLICY"); anything unknown is shown as readable text.
const CHECK_WORDS: Record<string, string> = {
  identity: "The PDF does not clearly show that it is this paper.",
  identity_evidence_unavailable: "The PDF does not clearly show that it is this paper.",
  version: "The version of the paper is unknown or outside the download policy.",
  version_unknown_or_outside_policy: "The version of the paper is unknown or outside the download policy.",
  readable: "The PDF could not be read cleanly.",
  attachment: "The stored file is missing or damaged.",
  record: "No verified record of this file exists yet.",
  file_validation_failed: "The file did not pass the automatic checks.",
  doi_mismatch: "The DOI in the PDF does not match the cited DOI.",
};

export function checkWords(code: string): string {
  const key = code.trim().toLowerCase().replace(/[\s-]+/g, "_");
  return CHECK_WORDS[key] ?? `${humanize(code)}.`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

// Intent summary keys ("add_allowed_folder") as labels.
export function summaryLabel(key: string): string {
  return key.toLowerCase() === "doi" ? "DOI" : key.replace(/_/g, " ");
}

// Authors are "Family, Given" strings: show up to three family names.
export function authorLine(authors: Author[], max = 3): string {
  if (authors.length === 0) return "";
  const family = authors.map((a) => (a.display.split(",")[0] ?? a.display).trim());
  return family.length <= max ? family.join(", ") : `${family.slice(0, max).join(", ")} et al.`;
}

export function metaLine(parts: Array<string | number | null | undefined>): string {
  return parts.filter((p) => p !== null && p !== undefined && p !== "").join(" · ");
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 10);
}

const STAGE_LABELS: Record<string, string> = {
  extract: "Reading citations",
  resolve: "Matching citations",
  discover_access: "Finding PDFs",
  retrieve: "Downloading PDFs",
  export: "Exporting",
};

export interface Progress {
  label: string;
  done: number;
  total: number;
}

// Stage counts -> one label and a done/total pair for the progress bar.
export function jobProgress(job: JobStatus): Progress {
  let done = 0;
  let total = 0;
  let active: string | undefined;
  for (const s of job.stages) {
    const finished = s.succeeded + s.skipped + s.failed_permanent + s.cancelled;
    done += finished;
    total += finished + s.queued + s.running + s.failed_retryable + s.retry_wait;
    if (!active && s.queued + s.running + s.retry_wait + s.failed_retryable > 0) active = s.name;
  }
  const name = active ?? job.stages[job.stages.length - 1]?.name ?? "";
  return { label: STAGE_LABELS[name] ?? "Working", done, total };
}
