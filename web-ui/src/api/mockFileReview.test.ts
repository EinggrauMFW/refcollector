import { describe, expect, it } from "vitest";
import { mockApi } from "./mock";

const CID = "c-coastal";

async function heldCitation(paperId: string, order: number) {
  const d = await mockApi.paperDetail(CID, paperId);
  const c = d.citations.find((x) => x.order === order)!;
  const detail = await mockApi.citationDetail(CID, c.citation_id);
  return { c, detail, file: detail.pending_file! };
}

// The mock follows the file-approval contract (D-46), so panel work against it is valid.
describe("mock file review", () => {
  it("marks held rows as file checks and returns the pending file", async () => {
    const { c, file } = await heldCitation("u-thesis", 15);
    expect(c).toMatchObject({ status: "check", check_kind: "file" });
    expect(file).toMatchObject({ work_file_id: "wf-held-1", size: 254899 });
    expect(file.failed_checks.length).toBeGreaterThan(0);
    const identity = (await mockApi.paperDetail(CID, "u-thesis")).citations.find((x) => x.order === 7)!;
    expect(identity.check_kind).toBe("identity");
    expect((await mockApi.citationDetail(CID, identity.citation_id)).pending_file).toBeNull();
  });

  it("needs the confirmation token from its own intent", async () => {
    const { c, file } = await heldCitation("u-thesis", 15);
    const body = { work_file_id: file.work_file_id, action: "approve" as const, expected_revision: c.revision, idempotency_key: "k" };
    await expect(mockApi.fileReviewConfirm(CID, c.citation_id, body, "tok-forged")).rejects.toMatchObject({ code: "NOT_AUTHORIZED" });
    const intent = await mockApi.fileReviewIntent(CID, c.citation_id, body);
    expect(intent.description.join(" ")).toMatch(/Approve this PDF/);
    // an intent for approve cannot confirm a reject
    await expect(mockApi.fileReviewConfirm(CID, c.citation_id, { ...body, action: "reject" }, intent.confirmation_token)).rejects.toMatchObject({ code: "NOT_AUTHORIZED" });
  });

  it("refuses a stale revision and a file that is not waiting", async () => {
    const { c, file } = await heldCitation("u-thesis", 15);
    const body = { work_file_id: file.work_file_id, action: "approve" as const, expected_revision: c.revision + 5, idempotency_key: "k" };
    await expect(mockApi.fileReviewIntent(CID, c.citation_id, body)).rejects.toMatchObject({ code: "REVISION_CONFLICT" });
    await expect(mockApi.fileReviewIntent(CID, c.citation_id, { ...body, expected_revision: c.revision, work_file_id: "wf-other" })).rejects.toMatchObject({ code: "INVALID_INPUT" });
  });

  it("approve makes the row Downloaded and puts a downloaded paper with cited_by on the shelf", async () => {
    const { c, file } = await heldCitation("u-thesis", 15);
    const body = { work_file_id: file.work_file_id, action: "approve" as const, expected_revision: c.revision, idempotency_key: "k2" };
    const intent = await mockApi.fileReviewIntent(CID, c.citation_id, body);
    const after = await mockApi.fileReviewConfirm(CID, c.citation_id, body, intent.confirmation_token);
    expect(after).toMatchObject({ status: "downloaded", check_kind: null, revision: c.revision + 1 });
    expect(after.downloaded_paper_id).toBeTruthy();
    const shelf = await mockApi.shelf(CID, { kind: "downloaded" });
    const paper = shelf.papers.find((p) => p.paper_id === after.downloaded_paper_id)!;
    expect(paper.cited_by).toContain("u-thesis");
    expect((await mockApi.citationDetail(CID, c.citation_id)).pending_file).toBeNull();
  });

  it("reject makes the row Failed with the user-rejection reason and removes the held file", async () => {
    const { c, file } = await heldCitation("u-surge", 8);
    const body = { work_file_id: file.work_file_id, action: "reject" as const, expected_revision: c.revision, idempotency_key: "k3" };
    const intent = await mockApi.fileReviewIntent(CID, c.citation_id, body);
    const after = await mockApi.fileReviewConfirm(CID, c.citation_id, body, intent.confirmation_token);
    expect(after).toMatchObject({ status: "failed", reasons: ["FILE_REJECTED_BY_USER"], check_kind: null });
    expect((await mockApi.citationDetail(CID, c.citation_id)).pending_file).toBeNull();
  });
});
