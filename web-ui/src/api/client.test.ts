import { afterEach, describe, expect, it, vi } from "vitest";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function load() {
  vi.resetModules();
  return (await import("./client")).client;
}

describe("client", () => {
  it("sends the CSRF header on mutations and the confirmation token on confirm routes", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) =>
      url === "/api/session" ? json(200, { csrf_token: "tok", principal: "me" }) : json(200, { ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();
    const body = { change: "add_allowed_folder", expected_settings_revision: "r1", idempotency_key: "k" } as const;
    await client.permissionConfirm("c1", body, "confirm-token");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("permissions/confirm"))!;
    const headers = (call[1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok");
    expect(headers["X-Confirmation-Token"]).toBe("confirm-token");
  });

  it("parses the server error body and keeps the recovery hint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(404, { code: "NOT_FOUND", message: "No such paper", retryable: false, recovery_hint: "Refresh the shelf." }),
      ),
    );
    const client = await load();
    const err = await client.paperDetail("c", "p").catch((e: unknown) => e);
    expect(err).toMatchObject({ name: "ApiError", code: "NOT_FOUND", message: "No such paper", recovery_hint: "Refresh the shelf.", status: 404 });
  });

  it("refreshes the session once when a mutation is refused", async () => {
    let sessions = 0;
    let posts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/session") return json(200, { csrf_token: `t${++sessions}`, principal: "me" });
        return ++posts === 1 ? json(403, { code: "NOT_AUTHORIZED", message: "bad csrf" }) : json(200, { job_id: "j", disabled_reason: null });
      }),
    );
    const client = await load();
    await expect(client.findMetadata("c", { paper_id: null, idempotency_key: "k" })).resolves.toMatchObject({ job_id: "j" });
    expect(sessions).toBe(2);
  });

  it("retries a refused mutation once and no more, then shows the error", async () => {
    let sessions = 0;
    let posts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/session") return json(200, { csrf_token: `t${++sessions}`, principal: "me" });
        posts += 1;
        return json(403, { code: "NOT_AUTHORIZED", message: "bad csrf", recovery_hint: "Reload the page." });
      }),
    );
    const client = await load();
    await expect(client.findMetadata("c", { paper_id: null, idempotency_key: "k" })).rejects.toMatchObject({
      code: "NOT_AUTHORIZED",
      recovery_hint: "Reload the page.",
    });
    expect(posts).toBe(2);
    expect(sessions).toBe(2);
  });

  it("fetches the session before the first read, so no read goes out without a cookie", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) =>
      url === "/api/session" ? json(200, { csrf_token: "tok", principal: "me" }) : json(200, { default_root: "C:/Users/me/RefCollector" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();
    expect(await client.defaults()).toEqual({ default_root: "C:/Users/me/RefCollector" });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/api/session", "/api/library/defaults"]);
    const headers = fetchMock.mock.calls[1]![1]!.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("refreshes the session once when a read is refused", async () => {
    let sessions = 0;
    let reads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/session") return json(200, { csrf_token: `t${++sessions}`, principal: "me" });
        return ++reads === 1 ? json(403, { code: "NOT_AUTHORIZED", message: "no session" }) : json(200, []);
      }),
    );
    const client = await load();
    await expect(client.listCollections()).resolves.toEqual([]);
    expect(sessions).toBe(2);
  });
  it("renames a collection with PATCH, sends CSRF and parsed response", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
      if (url === "/api/session") return json(200, { csrf_token: "tok", principal: "me" });
      return json(200, { collection_id: "c1", name: "Renamed", folder: null, paper_count: 0, check_count: 0, permissions: {}, settings_revision: "1" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();
    const res = await client.renameCollection("c1", "Renamed");
    expect(res.name).toBe("Renamed");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/library/collections/c1"))!;
    expect(call[1]?.method).toBe("PATCH");
    expect(JSON.parse(String(call[1]?.body))).toEqual({ name: "Renamed" });
    const headers = (call[1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok");
  });

  it("fetches collection consequences with GET", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/session") return json(200, { csrf_token: "tok", principal: "me" });
      return json(200, { collection_id: "c1", name: "Coll", paper_count: 3, citation_count: 10, file_count: 2, folder_path: "/tmp", message: "msg", has_active_jobs: false });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();
    const res = await client.collectionConsequences("c1");
    expect(res.paper_count).toBe(3);
    expect(res.citation_count).toBe(10);
    expect(res.has_active_jobs).toBe(false);
  });

  it("deletes a collection with DELETE and CSRF token", async () => {
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async (url) => {
      if (url === "/api/session") return json(200, { csrf_token: "tok", principal: "me" });
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();
    await client.deleteCollection("c1");
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/api/library/collections/c1"))!;
    expect(call[1]?.method).toBe("DELETE");
    const headers = (call[1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok");
  });
});
