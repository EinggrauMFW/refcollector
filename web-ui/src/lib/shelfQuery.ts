import type { CitationStatus, PaperKind, ShelfQuery } from "../api/types";

export interface Filters {
  q: string;
  kind: PaperKind | "";
  status: CitationStatus | "";
}

// Empty filters are left out of the request.
export function shelfQuery(f: Filters): ShelfQuery {
  return { q: f.q.trim() || undefined, kind: f.kind || undefined, status: f.status || undefined };
}

export const filtersActive = (f: Filters): boolean => Boolean(f.q.trim() || f.kind || f.status);
