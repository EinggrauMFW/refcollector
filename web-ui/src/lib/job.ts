import type { ApiError } from "../api/api";
import type { JobKindWire, JobStatus } from "../api/types";
import type { JobKind } from "./status";

export interface LocalJob {
  jobId: string;
  kind: JobKind;
  citationId: string | null; // set when started for one citation
}
export interface JobState {
  status: JobStatus | null;
  error: ApiError | null;
  n: number; // polls seen, used to refresh the open paper
}
export interface PaperJob {
  id: string;
  kind: JobKind;
  citationId: string | null;
  n: number;
  status: JobStatus | null;
  error: ApiError | null;
}

export const ERROR_RETRY_MS = 5000;
export const MIN_POLL_MS = 500;

// Polling rule (contract section 6): poll again after next_poll_after_ms, stop at a terminal state.
export function nextPollDelay(s: JobStatus): number | null {
  return s.terminal ? null : Math.max(MIN_POLL_MS, s.next_poll_after_ms);
}

export function toJobKind(k: JobKindWire | undefined): JobKind {
  switch (k) {
    case "extract":
      return "extract";
    case "find_metadata":
      return "find";
    case "download":
      return "download";
    default:
      return "unknown";
  }
}

// Which job, if any, runs for a paper. Order: the card's own active_job_id (server truth, so it
// survives a reload), then a polled job whose paper_ids lists the paper, then a job this page started.
// The kind comes from the polled status. The local record is only the fallback before the first poll.
export function jobForPaper(
  paperId: string,
  activeJobId: string | null,
  local: Record<string, LocalJob>,
  states: Record<string, JobState>,
  finished: ReadonlySet<string>,
): PaperJob | null {
  const byScope = Object.entries(states).find(([id, s]) => !finished.has(id) && s.status?.paper_ids.includes(paperId))?.[0];
  const id = activeJobId ?? byScope ?? local[paperId]?.jobId;
  if (!id || finished.has(id)) return null;
  const s = states[id];
  const mine = Object.values(local).find((l) => l.jobId === id);
  return {
    id,
    kind: s?.status ? toJobKind(s.status.kind) : (mine?.kind ?? "unknown"),
    citationId: mine?.citationId ?? null,
    n: s?.n ?? 0,
    status: s?.status ?? null,
    error: s?.error ?? null,
  };
}
