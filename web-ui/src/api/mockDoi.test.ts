import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LibraryApi } from "./api";
import type { FileReviewIn } from "./types";

let api: LibraryApi;
const cid = "c-doi";
const pid = "u-doi";
const call = async <T>(promise: Promise<T>): Promise<T> => {
  const settled = promise.then((value) => ({ value }), (error: unknown) => ({ error }));
  await vi.advanceTimersByTimeAsync(500);
  const result = await settled;
  if ("error" in result) throw result.error;
  return result.value;
};
async function citation(order: number) {
  return (await call(api.paperDetail(cid, pid))).citations.find((c) => c.order === order)!;
}
async function download(order: number) {
  const c = await citation(order);
  const start = await call(api.download(cid, { paper_id: pid, citation_id: c.citation_id, use_fallback: true, idempotency_key: "doi" }));
  if (start.job_id) {
    await vi.advanceTimersByTimeAsync(20_000);
    expect((await call(api.job(cid, start.job_id))).terminal).toBe(true);
  }
  return { start, c, after: await citation(order) };
}

describe("DOI-first mock", () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    api = (await import("./mock")).mockApi;
  });
  afterEach(() => vi.useRealTimers());

  it.each([1, 2])("downloads citation %s with pending metadata without a metadata confirmation", async (order) => {
    const c = await citation(order);
    const before = await call(api.citationDetail(cid, c.citation_id));
    const { after } = await download(order);
    expect(after).toMatchObject({ status: "downloaded", metadata_review_pending: true, check_kind: null });
    expect(after.downloaded_paper_id).toMatch(/^cf-/);
    const detail = await call(api.citationDetail(cid, c.citation_id));
    expect(detail.candidates).toEqual(before.candidates);
    expect(detail.citation.reasons).toEqual(before.citation.reasons);
    expect(detail.pending_file).toBeNull();
    const shelf = await call(api.shelf(cid, {}));
    expect(shelf.papers.find((p) => p.paper_id === after.downloaded_paper_id)?.cited_by).toEqual([pid]);
    if (detail.candidates.length) {
      const body = { action: "accept" as const, candidate_id: detail.candidates[0]!.candidate_id, expected_revision: after.revision, idempotency_key: "later-metadata" };
      const intent = await call(api.reviewIntent(cid, c.citation_id, body));
      const reviewed = await call(api.reviewConfirm(cid, c.citation_id, body, intent.confirmation_token));
      expect(reviewed).toMatchObject({ status: "downloaded", downloaded_paper_id: after.downloaded_paper_id, metadata_review_pending: false });
    }
  });

  it("requires only failed PDF checks to use file confirmation, bound to file kind", async () => {
    const { after } = await download(3);
    expect(after).toMatchObject({ status: "check", check_kind: "file", metadata_review_pending: true, downloaded_paper_id: null });
    const detail = await call(api.citationDetail(cid, after.citation_id));
    expect(detail.pending_file).toMatchObject({ file_kind: "citation_file", failed_checks: ["DOI_MISMATCH"] });
    const body: FileReviewIn = { file_kind: "citation_file", work_file_id: detail.pending_file!.work_file_id, action: "approve", expected_revision: after.revision, idempotency_key: "approval" };
    await expect(call(api.fileReviewIntent(cid, after.citation_id, { ...body, file_kind: "work_file" }))).rejects.toMatchObject({ code: "INVALID_INPUT" });
    const intent = await call(api.fileReviewIntent(cid, after.citation_id, body));
    await expect(call(api.fileReviewConfirm(cid, after.citation_id, { ...body, file_kind: "work_file" }, intent.confirmation_token))).rejects.toMatchObject({ code: "NOT_AUTHORIZED" });
    const approved = await call(api.fileReviewConfirm(cid, after.citation_id, body, intent.confirmation_token));
    expect(approved).toMatchObject({ status: "downloaded", metadata_review_pending: true, reasons: ["AUTHOR_CONFLICT"] });
    expect(approved.downloaded_paper_id).toBe(body.work_file_id);
  });

  it.each([4, 5])("disables citation %s with missing or ambiguous DOI", async (order) => {
    const { start, after } = await download(order);
    expect(after.can_download_by_doi).toBe(false);
    expect(start.job_id).toBeNull();
    expect(start.disabled_reason).toBeTruthy();
  });
});
