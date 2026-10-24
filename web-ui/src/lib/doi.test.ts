import { describe, expect, it } from "vitest";
import type { Citation } from "../api/types";
import { client } from "../api/client";
import { displayStatus, downloadedPdf, primaryAction, rowRun } from "./status";
import { fileOfViewer, fileViewerId, formatHash, parseHash } from "./route";

const cite = (extra: Partial<Citation> = {}): Citation => ({
  citation_id: "o-doi", order: 1, text: "Paper. doi:10.5555/cited", status: "check",
  check_kind: "identity", reasons: ["AUTHOR_CONFLICT"], title: null, authors: [], year: null,
  venue: null, doi: "10.5555/cited", can_download: false, downloaded_paper_id: null, revision: 1,
  ...extra,
});

describe("DOI-first state", () => {
  it.each(["extracted", "check", "no_match"] as const)("offers DOI retrieval for %s without normal download eligibility", (status) => {
    expect(rowRun(cite({ status, can_download_by_doi: true }))).toEqual({ run: "download", label: "Download PDF by DOI", useFallback: true });
  });
  it("marks a DOI-only citation Working during its own download", () => {
    const c = cite({ can_download_by_doi: true });
    expect(displayStatus(c, "download", c.citation_id)).toBe("working");
    expect(displayStatus(c, "download", "other")).toBe("check");
  });
  it("opens a verified DOI file despite pending metadata", () => {
    expect(primaryAction(cite({ status: "downloaded", metadata_review_pending: true, downloaded_paper_id: "cf-1" }))).toBe("open");
  });
  it("auto-opens only the requested citation's verified file, never a failed check", () => {
    const c = cite({ status: "downloaded", downloaded_paper_id: "cf-1", metadata_review_pending: true });
    expect(downloadedPdf([c], c.citation_id)).toBe("cf-1");
    expect(downloadedPdf([c], "other")).toBeNull();
    expect(downloadedPdf([{ ...c, status: "check", check_kind: "file" }], c.citation_id)).toBeNull();
  });
  it("keeps failed-file checks as review, even when DOI retrieval is possible", () => {
    const c = cite({ can_download_by_doi: true, metadata_review_pending: true, check_kind: "file" });
    expect(rowRun(c)).toBeNull();
    expect(primaryAction(c)).toBe("check");
  });
  it("does not invent DOI eligibility for missing fields or missing/ambiguous DOI", () => {
    expect(rowRun(cite())).toBeNull();
    expect(rowRun(cite({ can_download_by_doi: false, doi: null }))).toBeNull();
  });
  it("keeps normal accepted-work download unchanged", () => {
    expect(rowRun(cite({ status: "pdf_available", can_download: true, can_download_by_doi: true }))).toEqual({ run: "download", label: "Download" });
  });
  it("keeps the explicit DOI action available on a metadata Check even if normal download is also eligible", () => {
    expect(rowRun(cite({ can_download: true, can_download_by_doi: true }))).toEqual({ run: "download", label: "Download PDF by DOI", useFallback: true });
  });
  it("keeps work-file URLs by default and uses citation-file URLs when requested", () => {
    expect(client.workFileUrl("c 1", "cf/1", "citation_file")).toBe("/api/library/collections/c%201/files/citation_file/cf%2F1");
    expect(client.workFileUrl("c", "wf")).toBe("/api/library/collections/c/files/work_file/wf");
    expect(fileViewerId("cf-1", "citation_file")).not.toBe(fileViewerId("cf-1"));
    const pdf = fileViewerId("cf-1", "citation_file");
    const route = parseHash(formatHash({ cid: "c", paper: "p", pdf }));
    expect(fileOfViewer(route.pdf!)).toEqual({ id: "cf-1", kind: "citation_file" });
    expect(fileOfViewer(fileViewerId("wf-1"))).toEqual({ id: "wf-1", kind: "work_file" });
  });
});
