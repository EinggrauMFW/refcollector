import type { Citation, CitationStatus, DisplayStatus } from "../api/types";

export type Tone = "neutral" | "positive" | "warning" | "negative";
export type IconName =
  | "text"
  | "spinner"
  | "link"
  | "alert"
  | "search-off"
  | "download"
  | "file-off"
  | "file-check"
  | "x-circle";

// Status is always shown as text plus an icon, never colour alone (spec section 8).
export const STATUS_META: Record<DisplayStatus, { label: string; tone: Tone; icon: IconName; hint: string }> = {
  extracted: { label: "Extracted", tone: "neutral", icon: "text", hint: "Citation text found. No metadata yet." },
  working: { label: "Working", tone: "neutral", icon: "spinner", hint: "A job is processing this citation." },
  matched: { label: "Matched", tone: "neutral", icon: "link", hint: "Work found and accepted." },
  check: { label: "Check", tone: "warning", icon: "alert", hint: "Needs your decision." },
  no_match: { label: "No match", tone: "neutral", icon: "search-off", hint: "No acceptable work was found." },
  pdf_available: { label: "PDF available", tone: "neutral", icon: "download", hint: "A PDF copy was found. Not downloaded yet." },
  no_pdf: { label: "No PDF", tone: "neutral", icon: "file-off", hint: "No eligible PDF copy was found." },
  downloaded: { label: "Downloaded", tone: "positive", icon: "file-check", hint: "A verified PDF is in the collection folder." },
  failed: { label: "Failed", tone: "negative", icon: "x-circle", hint: "The last step failed." },
};

// Order of segments in the coverage strip and of counts in summaries.
export const STATUS_ORDER: CitationStatus[] = [
  "downloaded",
  "pdf_available",
  "matched",
  "check",
  "no_pdf",
  "no_match",
  "failed",
  "extracted",
];

export type JobKind = "extract" | "find" | "download" | "unknown";

// Contract section 3: `working` is client-only, for rows in the scope of an active job.
// `scopeCitation` is set when the job was started for one citation.
export function isInJobScope(
  kind: JobKind,
  c: Pick<Citation, "citation_id" | "status" | "can_download" | "can_download_by_doi">,
  scopeCitation: string | null = null,
): boolean {
  if (scopeCitation && c.citation_id !== scopeCitation) return false;
  switch (kind) {
    case "extract":
      return false; // the citation list does not exist yet
    case "find":
      return c.status === "extracted" || c.status === "failed" || (c.status === "matched" && !c.can_download);
    case "download":
      return c.can_download || Boolean(c.can_download_by_doi);
    case "unknown":
      return c.status === "extracted" || c.can_download || Boolean(c.can_download_by_doi);
  }
}

export function displayStatus(c: Citation, jobKind: JobKind | null, scopeCitation: string | null = null): DisplayStatus {
  return jobKind && isInJobScope(jobKind, c, scopeCitation) ? "working" : c.status;
}

// What the Review cue on a Check row says: a file waits for approval, or an identity waits for a choice.
export const reviewLabel = (c: Pick<Citation, "check_kind">): string => (c.check_kind === "file" ? "Review file" : "Review");

// The run a citation row offers (contract section 8.3): Find metadata, Download, or Retry of either.
export function rowRun(c: Pick<Citation, "status" | "can_download" | "can_download_by_doi" | "check_kind">): { run: "find" | "download"; label: string; useFallback?: boolean } | null {
  if (c.check_kind === "file") return null;
  if (c.can_download_by_doi && c.status !== "downloaded" && (!c.can_download || c.status === "check" || c.status === "extracted" || c.status === "no_match")) {
    return { run: "download", label: "Download PDF by DOI", useFallback: true };
  }
  switch (c.status) {
    case "extracted":
      return { run: "find", label: "Find metadata" };
    case "matched":
      return c.can_download ? { run: "download", label: "Download" } : { run: "find", label: "Find metadata" };
    case "pdf_available":
      return c.can_download ? { run: "download", label: "Download" } : null;
    case "failed":
      return { run: c.can_download ? "download" : "find", label: "Retry" };
    default:
      return null;
  }
}

// Only a verified file can auto-open. Pending metadata does not add a file gate.
export function downloadedPdf(citations: Citation[], citationId: string): string | null {
  const c = citations.find((c) => c.citation_id === citationId);
  return c?.status === "downloaded" ? c.downloaded_paper_id : null;
}

export type RowAction = "find" | "download" | "check" | "open" | "retry" | null;

// Primary action per status (spec section 5).
export function primaryAction(c: Citation): RowAction {
  switch (c.status) {
    case "extracted":
      return "find";
    case "matched":
    case "pdf_available":
      return c.can_download ? "download" : null;
    case "check":
    case "no_match":
      return "check";
    case "downloaded":
      return c.downloaded_paper_id ? "open" : null;
    case "failed":
      return "retry";
    case "no_pdf":
      return null;
  }
}

export function countCitations(counts: Partial<Record<CitationStatus, number>>) {
  const n = (s: CitationStatus) => counts[s] ?? 0;
  const total = STATUS_ORDER.reduce((sum, s) => sum + n(s), 0);
  return { total, downloaded: n("downloaded"), check: n("check"), missing: n("pdf_available") + n("no_pdf") };
}
