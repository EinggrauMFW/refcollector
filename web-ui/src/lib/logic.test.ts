import { describe, expect, it } from "vitest";
import type { Citation, Intent, JobStatus } from "../api/types";
import { keyFor } from "./idempotency";
import { jobForPaper, nextPollDelay, toJobKind, type JobState, type LocalJob } from "./job";
import { filtersActive, shelfQuery } from "./shelfQuery";
import { displayStatus, rowRun } from "./status";
import { twoStep } from "./twostep";

const job = (over: Partial<JobStatus> = {}): JobStatus => ({
  job_id: "j1",
  state: "running",
  kind: "download",
  paper_ids: ["p1"],
  terminal: false,
  stages: [],
  warnings: [],
  next_poll_after_ms: 2000,
  ...over,
});

const cite = (over: Partial<Citation> = {}): Citation => ({
  citation_id: "o1",
  order: 1,
  text: "",
  status: "extracted",
  reasons: [],
  title: null,
  authors: [],
  year: null,
  venue: null,
  doi: null,
  can_download: false,
  downloaded_paper_id: null,
  revision: 1,
  ...over,
});

const intent: Intent = { confirmation_token: "t", expires_at: "", description: ["Allow X"], summary: {} };

describe("job polling rules", () => {
  it("continues after next_poll_after_ms, never faster than 500 ms", () => {
    expect(nextPollDelay(job({ next_poll_after_ms: 2000 }))).toBe(2000);
    expect(nextPollDelay(job({ next_poll_after_ms: 50 }))).toBe(500);
  });
  it("stops at a terminal state", () => {
    expect(nextPollDelay(job({ terminal: true, state: "completed" }))).toBeNull();
  });
  it("maps the wire kind", () => {
    expect(toJobKind("find_metadata")).toBe("find");
    expect(toJobKind("extract")).toBe("extract");
    expect(toJobKind(undefined)).toBe("unknown");
  });
});

describe("which job runs for a paper", () => {
  const none = new Set<string>();

  it("finds the job from the card after a reload, with the kind from the polled status", () => {
    const states: Record<string, JobState> = { j1: { status: job({ kind: "download" }), error: null, n: 3 } };
    expect(jobForPaper("p1", "j1", {}, states, none)).toMatchObject({ id: "j1", kind: "download", n: 3 });
  });

  it("finds a collection-wide job through paper_ids when the card has no active_job_id", () => {
    const states: Record<string, JobState> = { j1: { status: job({ paper_ids: ["p1", "p2"] }), error: null, n: 1 } };
    expect(jobForPaper("p2", null, {}, states, none)?.id).toBe("j1");
    expect(jobForPaper("p3", null, {}, states, none)).toBeNull();
  });

  it("falls back to the local kind before the first poll, and keeps the citation scope", () => {
    const local: Record<string, LocalJob> = { p1: { jobId: "j9", kind: "find", citationId: "o4" } };
    expect(jobForPaper("p1", null, local, {}, none)).toMatchObject({ id: "j9", kind: "find", citationId: "o4" });
  });

  it("ignores a finished job even if the card still lists it", () => {
    expect(jobForPaper("p1", "j1", {}, {}, new Set(["j1"]))).toBeNull();
  });
});

describe("working mark", () => {
  it("covers only the started citation when the job has a citation scope", () => {
    const a = cite({ citation_id: "o1", status: "extracted" });
    const b = cite({ citation_id: "o2", status: "extracted" });
    expect(displayStatus(a, "find", "o1")).toBe("working");
    expect(displayStatus(b, "find", "o1")).toBe("extracted");
    expect(displayStatus(b, "find", null)).toBe("working");
  });
  it("marks a failed row as working while a retry runs", () => {
    expect(displayStatus(cite({ status: "failed" }), "find")).toBe("working");
  });
});

describe("row actions", () => {
  it("offers Find metadata, Download or Retry per status", () => {
    expect(rowRun(cite({ status: "extracted" }))).toEqual({ run: "find", label: "Find metadata" });
    expect(rowRun(cite({ status: "pdf_available", can_download: true }))).toEqual({ run: "download", label: "Download" });
    expect(rowRun(cite({ status: "pdf_available", can_download: false }))).toBeNull();
    expect(rowRun(cite({ status: "matched", can_download: false }))).toEqual({ run: "find", label: "Find metadata" });
    expect(rowRun(cite({ status: "failed", can_download: true }))).toEqual({ run: "download", label: "Retry" });
    expect(rowRun(cite({ status: "failed", can_download: false }))).toEqual({ run: "find", label: "Retry" });
    expect(rowRun(cite({ status: "check" }))).toBeNull();
    expect(rowRun(cite({ status: "downloaded" }))).toBeNull();
  });
});

describe("check panel two-step state", () => {
  const start = twoStep<string>(null, { type: "start", target: "accept:k1", key: "key" });

  it("goes start, intent, confirming, and back to nothing", () => {
    expect(start).toMatchObject({ target: "accept:k1", intent: null, confirming: false });
    const ready = twoStep(start, { type: "intent", intent });
    expect(ready?.intent).toBe(intent);
    expect(twoStep(ready, { type: "confirming" })?.confirming).toBe(true);
    expect(twoStep(ready, { type: "reset" })).toBeNull();
  });

  it("never confirms without an intent", () => {
    expect(twoStep(start, { type: "confirming" })).toBe(start);
  });

  it("allows one change at a time and keeps the key of the first", () => {
    expect(twoStep(start, { type: "start", target: "reject:k2", key: "other" })).toBe(start);
  });

  it("ignores a late intent after a reset", () => {
    expect(twoStep(null, { type: "intent", intent })).toBeNull();
  });
});

describe("shelf filters", () => {
  it("leaves empty filters out of the query", () => {
    expect(shelfQuery({ q: "  ", kind: "", status: "" })).toEqual({ q: undefined, kind: undefined, status: undefined });
    expect(shelfQuery({ q: " dune ", kind: "uploaded", status: "check" })).toEqual({ q: "dune", kind: "uploaded", status: "check" });
  });
  it("knows when a filter is on", () => {
    expect(filtersActive({ q: "", kind: "", status: "" })).toBe(false);
    expect(filtersActive({ q: "", kind: "", status: "check" })).toBe(true);
  });
});

describe("idempotency keys", () => {
  it("returns the same key for the same file until it is dropped", () => {
    const keys = new Map<string, string>();
    let n = 0;
    const make = () => `k${++n}`;
    expect(keyFor(keys, "a.pdf|1|2", make)).toBe("k1");
    expect(keyFor(keys, "a.pdf|1|2", make)).toBe("k1");
    expect(keyFor(keys, "b.pdf|1|2", make)).toBe("k2");
    keys.delete("a.pdf|1|2");
    expect(keyFor(keys, "a.pdf|1|2", make)).toBe("k3");
  });
});
