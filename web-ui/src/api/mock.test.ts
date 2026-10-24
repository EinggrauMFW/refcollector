import { describe, expect, it } from "vitest";
import { mockApi } from "./mock";

// The mock follows contract section 8. These tests keep it honest, so UI work against it is valid.
describe("mock follows the amended contract", () => {
  it("returns the default root", async () => {
    expect(await mockApi.defaults()).toEqual({ default_root: expect.stringMatching(/RefCollector$/) });
  });

  it("status filter: an uploaded paper matches when any citation has the status, a downloaded one only matches downloaded", async () => {
    const check = await mockApi.shelf("c-coastal", { status: "check" });
    expect(check.papers.map((p) => p.paper_id).sort()).toEqual(["u-surge", "u-thesis"]);
    const downloaded = await mockApi.shelf("c-coastal", { status: "downloaded" });
    expect(downloaded.papers.some((p) => p.kind === "downloaded")).toBe(true);
    const failed = await mockApi.shelf("c-coastal", { status: "failed" });
    expect(failed.papers.map((p) => p.paper_id)).toEqual(["u-thesis"]);
  });

  it("intents carry readable description lines", async () => {
    const col = (await mockApi.listCollections()).find((c) => c.collection_id === "c-attn")!;
    const intent = await mockApi.permissionIntent("c-attn", {
      change: "set_project_network",
      expected_settings_revision: col.settings_revision,
      idempotency_key: "k",
    });
    expect(intent.description.length).toBeGreaterThan(0);
    expect(intent.description[0]).toMatch(/network mode/i);
  });

  it("a citation-scoped run reports kind and paper_ids and sets active_job_id", async () => {
    const detail = await mockApi.paperDetail("c-coastal", "u-dune");
    const target = detail.citations[0]!;
    const accepted = await mockApi.findMetadata("c-coastal", { paper_id: "u-dune", citation_id: target.citation_id, idempotency_key: "k" });
    expect(accepted.job_id).toBeTruthy();
    const status = await mockApi.job("c-coastal", accepted.job_id!);
    expect(status).toMatchObject({ kind: "find_metadata", paper_ids: ["u-dune"], terminal: false });
    const card = (await mockApi.paperDetail("c-coastal", "u-dune")).paper;
    expect(card.active_job_id).toBe(accepted.job_id);
  });

  it("a whole-collection run sets active_job_id on every uploaded paper in scope", async () => {
    const accepted = await mockApi.download("c-coastal", { paper_id: null, idempotency_key: "k2" });
    const status = await mockApi.job("c-coastal", accepted.job_id!);
    expect(status.kind).toBe("download");
    expect(status.paper_ids).toEqual(expect.arrayContaining(["u-thesis", "u-surge"]));
    const shelf = await mockApi.shelf("c-coastal", { kind: "uploaded" });
    for (const id of status.paper_ids) expect(shelf.papers.find((p) => p.paper_id === id)?.active_job_id).toBe(accepted.job_id);
  });
});
