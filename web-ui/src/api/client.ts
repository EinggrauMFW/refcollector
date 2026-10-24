import { ApiError, newKey, type LibraryApi } from "./api";
import type { ErrorBody, Session } from "./types";

const BASE = "/api/library";
const SESSION = "/api/session";

let csrf: Promise<string> | null = null;

// Session bootstrap: GET /api/session sets the rc_session cookie and returns the CSRF token.
function csrfToken(): Promise<string> {
  csrf ??= request<Session>(SESSION, { method: "GET" }, false).then(
    (s) => s.csrf_token,
    (err: unknown) => {
      csrf = null;
      throw err;
    },
  );
  return csrf;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: Partial<ErrorBody> | null = null;
  try {
    body = (await res.json()) as Partial<ErrorBody>;
  } catch {
    // not JSON
  }
  return new ApiError(
    body?.code ?? `HTTP_${res.status}`,
    body?.message ?? res.statusText ?? "Request failed",
    body?.recovery_hint ?? null,
    res.status,
    body?.retryable ?? false,
  );
}

async function request<T>(
  path: string,
  init: { method: "GET" | "POST" | "PATCH" | "DELETE"; body?: BodyInit; json?: unknown; headers?: Record<string, string> },
  retryAuth = true,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...init.headers };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  // Every other route needs the session cookie, so the session is fetched first, reads included.
  if (path !== SESSION) {
    const token = await csrfToken();
    if (init.method !== "GET") headers["X-CSRF-Token"] = token;
  }

  let res: Response;
  try {
    res = await fetch(path, { method: init.method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(
      "NETWORK",
      "Cannot reach the RefCollector server.",
      "Check that the server is running, then retry.",
    );
  }
  if (!res.ok) {
    const err = await parseError(res);
    // A restarted server forgets the session: fetch a new session and retry once.
    if (retryAuth && path !== SESSION && err.code === "NOT_AUTHORIZED") {
      csrf = null;
      return request<T>(path, init, false);
    }
    throw err;
  }
  return (res.status === 204 ? null : await res.json()) as T;
}

const get = <T>(path: string) => request<T>(BASE + path, { method: "GET" });
const post = <T>(path: string, json: unknown, headers?: Record<string, string>) =>
  request<T>(BASE + path, { method: "POST", json, headers });
const confirmHeader = (token: string) => ({ "X-Confirmation-Token": token });
const enc = encodeURIComponent;

export const client: LibraryApi = {
  defaults: () => get("/defaults"),
  listCollections: () => get("/collections"),
  createCollection: (body) => post("/collections", body),
  renameCollection: (cid, name) => request(`${BASE}/collections/${enc(cid)}`, { method: "PATCH", json: { name } }),
  collectionConsequences: (cid) => get(`/collections/${enc(cid)}/consequences`),
  deleteCollection: (cid) => request(`${BASE}/collections/${enc(cid)}`, { method: "DELETE" }),
  permissionIntent: (cid, body) => post(`/collections/${enc(cid)}/permissions/intent`, body),
  permissionConfirm: (cid, body, token) =>
    post(`/collections/${enc(cid)}/permissions/confirm`, body, confirmHeader(token)),
  shelf: (cid, q) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") params.set(k, String(v));
    const qs = params.toString();
    return get(`/collections/${enc(cid)}/shelf${qs ? `?${qs}` : ""}`);
  },
  uploadPaper: (cid, file, idempotencyKey = newKey()) => {
    const form = new FormData();
    form.append("file", file);
    form.append("idempotency_key", idempotencyKey);
    return request(`${BASE}/collections/${enc(cid)}/papers`, { method: "POST", body: form });
  },
  paperDetail: (cid, pid) => get(`/collections/${enc(cid)}/papers/${enc(pid)}`),
  findMetadata: (cid, body) => post(`/collections/${enc(cid)}/find-metadata`, body),
  download: (cid, body) => post(`/collections/${enc(cid)}/download`, body),
  citationDetail: (cid, oid) => get(`/collections/${enc(cid)}/citations/${enc(oid)}`),
  reviewIntent: (cid, oid, body) => post(`/collections/${enc(cid)}/citations/${enc(oid)}/review/intent`, body),
  reviewConfirm: (cid, oid, body, token) =>
    post(`/collections/${enc(cid)}/citations/${enc(oid)}/review/confirm`, body, confirmHeader(token)),
  fileReviewIntent: (cid, oid, body) => post(`/collections/${enc(cid)}/citations/${enc(oid)}/file-review/intent`, body),
  fileReviewConfirm: (cid, oid, body, token) =>
    post(`/collections/${enc(cid)}/citations/${enc(oid)}/file-review/confirm`, body, confirmHeader(token)),
  workFileUrl: (cid, id, kind = "work_file") => `${BASE}/collections/${enc(cid)}/files/${kind}/${enc(id)}`,
  sync: (cid) => post(`/collections/${enc(cid)}/sync`, {}),
  job: (cid, jid) => get(`/collections/${enc(cid)}/jobs/${enc(jid)}`),
};
