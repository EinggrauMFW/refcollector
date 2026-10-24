import type { FileKind } from "../api/types";

// Hash route: #/<collection>?paper=<id>&citation=<id>&pdf=<id>
// A hash keeps the app independent of the server's path fallback, and keeps the expanded paper in the URL.
export interface Route {
  cid: string | null;
  paper: string | null;
  citation: string | null;
  pdf: string | null;
}

// The viewer shows a paper (`pdf=<paper id>`) or a PDF that waits for approval (`pdf=file:<work file id>`).
const FILE_PREFIX = "file:";
const CITATION_FILE_PREFIX = "citation-file:";
export const fileViewerId = (workFileId: string, kind: FileKind = "work_file"): string =>
  (kind === "citation_file" ? CITATION_FILE_PREFIX : FILE_PREFIX) + workFileId;
export const workFileOfViewer = (pdf: string): string | null => (pdf.startsWith(FILE_PREFIX) ? pdf.slice(FILE_PREFIX.length) : null);
export function fileOfViewer(pdf: string): { id: string; kind: FileKind } | null {
  if (pdf.startsWith(CITATION_FILE_PREFIX)) return { id: pdf.slice(CITATION_FILE_PREFIX.length), kind: "citation_file" };
  const id = workFileOfViewer(pdf);
  return id ? { id, kind: "work_file" } : null;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path = "", query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  const cid = decodeURIComponent(path.split("/")[0] ?? "");
  return {
    cid: cid || null,
    paper: params.get("paper"),
    citation: cid ? params.get("citation") : null,
    pdf: params.get("pdf"),
  };
}

export function formatHash(r: Partial<Route>): string {
  if (!r.cid) return "#/";
  const params = new URLSearchParams();
  if (r.paper) params.set("paper", r.paper);
  if (r.paper && r.citation) params.set("citation", r.citation);
  if (r.pdf) params.set("pdf", r.pdf);
  const qs = params.toString();
  return `#/${encodeURIComponent(r.cid)}${qs ? `?${qs}` : ""}`;
}

// Mode switch (D-50): the advanced dashboard reads `#project=<collection id>` on load.
export const ADVANCED_PATH = "/advanced";
export const advancedHref = (cid: string | null): string =>
  cid ? `${ADVANCED_PATH}#project=${encodeURIComponent(cid)}` : ADVANCED_PATH;
