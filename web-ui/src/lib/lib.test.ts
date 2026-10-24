import { describe, expect, it } from "vitest";
import type { Citation, JobStatus } from "../api/types";
import { authorLine, humanize, jobProgress, reasonList } from "./format";
import { advancedHref, formatHash, parseHash } from "./route";
import { countCitations, displayStatus, primaryAction } from "./status";

const cite = (over: Partial<Citation>): Citation => ({
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

describe("status", () => {
  it("shows working only for rows in the scope of the active job kind", () => {
    expect(displayStatus(cite({ status: "extracted" }), "find")).toBe("working");
    expect(displayStatus(cite({ status: "matched", can_download: true }), "find")).toBe("matched");
    expect(displayStatus(cite({ status: "pdf_available", can_download: true }), "download")).toBe("working");
    expect(displayStatus(cite({ status: "no_pdf" }), "download")).toBe("no_pdf");
    expect(displayStatus(cite({ status: "extracted" }), null)).toBe("extracted");
  });

  it("maps each status to its primary action", () => {
    expect(primaryAction(cite({ status: "extracted" }))).toBe("find");
    expect(primaryAction(cite({ status: "pdf_available", can_download: true }))).toBe("download");
    expect(primaryAction(cite({ status: "pdf_available", can_download: false }))).toBeNull();
    expect(primaryAction(cite({ status: "check" }))).toBe("check");
    expect(primaryAction(cite({ status: "downloaded", downloaded_paper_id: "w1" }))).toBe("open");
    expect(primaryAction(cite({ status: "failed" }))).toBe("retry");
    expect(primaryAction(cite({ status: "no_pdf" }))).toBeNull();
  });

  it("counts citations", () => {
    expect(countCitations({ downloaded: 2, check: 1, pdf_available: 3, no_pdf: 1 })).toEqual({
      total: 7,
      downloaded: 2,
      check: 1,
      missing: 4,
    });
  });
});

describe("route", () => {
  it("round-trips the expanded paper, citation and viewer", () => {
    const r = { cid: "p 1", paper: "s1", citation: "o9", pdf: "w2" };
    expect(parseHash(formatHash(r))).toEqual(r);
  });
  it("drops the citation when no paper is open, and parses an empty hash", () => {
    expect(formatHash({ cid: "c", citation: "o" })).toBe("#/c");
    expect(parseHash("")).toEqual({ cid: null, paper: null, citation: null, pdf: null });
  });
});

describe("format", () => {
  it("humanizes reason codes", () => {
    expect(humanize("title_match")).toBe("Title match");
    expect(humanize("AUTHOR_CONFLICT")).toBe("Author conflict");
    expect(reasonList(["PROVIDER_DISAGREEMENT", "AUTHOR_CONFLICT", "provider_disagreement"])).toEqual(["Provider disagreement", "Author conflict"]);
  });
  it("shortens author lists to family names", () => {
    const a = (s: string) => ({ display: s });
    expect(authorLine([a("Smith, Jane"), a("Ng, Wei")])).toBe("Smith, Ng");
    expect(authorLine([a("A, x"), a("B, x"), a("C, x"), a("D, x")])).toBe("A, B, C et al.");
  });
  it("reduces stage counts to a label and done/total", () => {
    const zero = { queued: 0, running: 0, succeeded: 0, failed_retryable: 0, failed_permanent: 0, skipped: 0, retry_wait: 0, cancelled: 0 };
    const job: JobStatus = {
      job_id: "j",
      state: "running",
      kind: "find_metadata",
      paper_ids: ["p1"],
      terminal: false,
      warnings: [],
      next_poll_after_ms: 2000,
      stages: [
        { name: "resolve", ...zero, succeeded: 4 },
        { name: "discover_access", ...zero, running: 1, queued: 3 },
      ],
    };
    expect(jobProgress(job)).toEqual({ label: "Finding PDFs", done: 4, total: 8 });
  });
});

describe("advancedHref", () => {
  it("links to the open collection", () => {
    expect(advancedHref("c1")).toBe("/advanced#project=c1");
    expect(advancedHref("a b/c")).toBe("/advanced#project=a%20b%2Fc");
  });
  it("links to the bare dashboard with no collection", () => {
    expect(advancedHref(null)).toBe("/advanced");
    expect(advancedHref("")).toBe("/advanced");
  });
});
