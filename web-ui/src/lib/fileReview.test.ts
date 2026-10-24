import { afterEach, describe, expect, it, vi } from "vitest";
import { checkWords, formatBytes, hostOf } from "./format";
import { fileViewerId, formatHash, parseHash, workFileOfViewer } from "./route";
import { reviewLabel } from "./status";
import { twoStep } from "./twostep";

describe("file review words", () => {
  it("explains failed checks in plain words, from names or reason codes", () => {
    expect(checkWords("identity")).toMatch(/is this paper/);
    expect(checkWords("VERSION_UNKNOWN_OR_OUTSIDE_POLICY")).toMatch(/version/i);
    expect(checkWords("FILE_VALIDATION_FAILED")).toMatch(/automatic checks/);
  });
  it("falls back to readable text for an unknown check", () => {
    expect(checkWords("SOME_NEW_CHECK")).toBe("Some new check.");
  });
  it("formats size and host", () => {
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(254899)).toBe("249 KB");
    expect(formatBytes(1843200)).toBe("1.8 MB");
    expect(hostOf("https://www.nature.com/articles/x.pdf")).toBe("nature.com");
    expect(hostOf("not a url")).toBeNull();
    expect(hostOf(null)).toBeNull();
  });
});

describe("file viewer route", () => {
  it("tells a work file from a paper and survives the URL", () => {
    expect(workFileOfViewer(fileViewerId("wf-1"))).toBe("wf-1");
    expect(workFileOfViewer("src_123")).toBeNull();
    const r = { cid: "c", paper: "p", citation: "o", pdf: fileViewerId("wf-1") };
    expect(parseHash(formatHash(r))).toEqual(r);
  });
});

describe("review cue", () => {
  it("names what a Check row waits for", () => {
    expect(reviewLabel({ check_kind: "file" })).toBe("Review file");
    expect(reviewLabel({ check_kind: "identity" })).toBe("Review");
    expect(reviewLabel({ check_kind: null })).toBe("Review");
    expect(reviewLabel({})).toBe("Review");
  });
});

describe("approve and reject share one two-step flow", () => {
  it("holds one decision at a time and only confirms after an intent", () => {
    const started = twoStep<"approve" | "reject">(null, { type: "start", target: "approve", key: "k" });
    expect(twoStep(started, { type: "start", target: "reject", key: "k2" })?.target).toBe("approve");
    expect(twoStep(started, { type: "confirming" })?.confirming).toBe(false);
  });
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("client file-review routes", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("posts to the file-review routes with CSRF, and the confirmation token on confirm only", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) =>
      url === "/api/session" ? json(200, { csrf_token: "tok", principal: "me" }) : json(200, {}),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.resetModules();
    const { client } = await import("../api/client");
    const body = { work_file_id: "wf 1", action: "approve" as const, expected_revision: 3, idempotency_key: "k" };
    await client.fileReviewIntent("c1", "o1", body);
    await client.fileReviewConfirm("c1", "o1", body, "confirm-token");
    const calls = fetchMock.mock.calls.filter(([u]) => String(u).includes("file-review"));
    expect(calls.map(([u]) => u)).toEqual([
      "/api/library/collections/c1/citations/o1/file-review/intent",
      "/api/library/collections/c1/citations/o1/file-review/confirm",
    ]);
    const [intentHeaders, confirmHeaders] = calls.map(([, i]) => (i as RequestInit).headers as Record<string, string>);
    expect(intentHeaders!["X-CSRF-Token"]).toBe("tok");
    expect(intentHeaders!["X-Confirmation-Token"]).toBeUndefined();
    expect(confirmHeaders!["X-Confirmation-Token"]).toBe("confirm-token");
    expect(JSON.parse(String((calls[0]![1] as RequestInit).body))).toEqual(body);
  });

  it("builds the work file URL the viewer loads", async () => {
    vi.resetModules();
    const { client } = await import("../api/client");
    expect(client.workFileUrl("c 1", "wf/1")).toBe("/api/library/collections/c%201/files/work_file/wf%2F1");
  });
});
