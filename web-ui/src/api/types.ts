// Mirror of docs/orchestration/library/contract.md section 4 (Pydantic DTOs in
// src/refcollector/web/library_api.py). Names and fields match one to one; the wire is snake_case.
// `datetime` fields arrive as ISO 8601 strings.

export type PaperKind = "uploaded" | "downloaded";

export type CitationStatus =
  | "extracted"
  | "check"
  | "no_match"
  | "matched"
  | "pdf_available"
  | "no_pdf"
  | "downloaded"
  | "failed";

export interface Author {
  display: string;
}

export interface Permissions {
  folder_granted: boolean;
  network_mode: string; // NetworkMode value: "offline" | "metadata" | "retrieval"
  downloads_enabled: boolean;
  downloads_source: "project" | "global" | "none";
  can_download: boolean;
}

export interface Collection {
  collection_id: string;
  name: string;
  folder: string | null;
  paper_count: number;
  check_count: number;
  permissions: Permissions;
  settings_revision: string;
}

export interface CollectionConsequences {
  collection_id: string;
  name: string;
  paper_count: number;
  citation_count: number;
  file_count: number;
  folder_path: string | null;
  message: string;
  has_active_jobs?: boolean;
}

export interface PaperCard {
  paper_id: string;
  kind: PaperKind;
  title: string;
  authors: Author[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  file_name: string;
  folder_path: string | null;
  citation_count: number;
  status_counts: Partial<Record<CitationStatus, number>>;
  cited_by: string[];
  active_job_id: string | null;
  file_url: string;
}

export interface ShelfPage {
  papers: PaperCard[];
  next_cursor: string | null;
  total: number;
}

export interface Citation {
  citation_id: string;
  order: number;
  text: string;
  status: CitationStatus;
  reasons: string[];
  title: string | null;
  authors: Author[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  can_download: boolean;
  downloaded_paper_id: string | null;
  revision: number;
  check_kind?: CheckKind | null; // what a Check row waits for: an identity choice or a file approval
  can_download_by_doi?: boolean;
  metadata_review_pending?: boolean;
}

export type CheckKind = "identity" | "file";
export type FileKind = "work_file" | "citation_file";

export interface PaperDetail {
  paper: PaperCard;
  pages: number | null;
  imported_at: string | null;
  citations: Citation[];
}

export interface Candidate {
  candidate_id: string;
  rank: number;
  title: string | null;
  authors: Author[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  reasons: string[];
  rejected: boolean;
}

// A PDF held at needs_review (decision D-46). Present when citation.check_kind is "file".
export interface PendingFile {
  file_kind?: FileKind;
  work_file_id: string;
  size: number; // bytes
  source_url: string | null;
  failed_checks: string[];
}

export interface CitationDetail {
  citation: Citation;
  candidates: Candidate[];
  pending_file?: PendingFile | null;
}

// Existing contracts.api.StageCounts
export interface StageCounts {
  name: string;
  queued: number;
  running: number;
  succeeded: number;
  failed_retryable: number;
  failed_permanent: number;
  skipped: number;
  retry_wait: number;
  cancelled: number;
}

export type JobKindWire = "extract" | "find_metadata" | "download";

export interface JobStatus {
  job_id: string;
  state: string; // JobState value
  kind: JobKindWire; // contract section 8.6
  paper_ids: string[]; // uploaded papers in scope
  terminal: boolean;
  stages: StageCounts[];
  warnings: string[];
  next_poll_after_ms: number;
}

export interface Intent {
  confirmation_token: string;
  expires_at: string;
  description: string[]; // readable lines for the confirm step (contract section 8.5)
  summary: Record<string, unknown>; // debugging only
}

// GET /api/library/defaults (contract section 8.2)
export interface Defaults {
  default_root: string;
}

export interface CreateCollectionIn {
  name: string; // 1..200 chars
  folder: string; // absolute path
  idempotency_key: string;
}

export type PermissionChange = "add_allowed_folder" | "set_project_network" | "set_project_downloads";

export interface PermissionIntentIn {
  change: PermissionChange;
  expected_settings_revision: string;
  idempotency_key: string;
}

export interface RunIn {
  use_fallback?: boolean;
  paper_id: string | null; // null = whole collection
  citation_id?: string | null; // wins over paper_id (contract section 8.3)
  idempotency_key: string;
}

export interface ReviewIn {
  action: "accept" | "reject";
  candidate_id: string;
  expected_revision: number;
  idempotency_key: string;
}

export interface FileReviewIn {
  file_kind?: FileKind;
  work_file_id: string;
  action: "approve" | "reject";
  expected_revision: number; // occurrence revision
  idempotency_key: string;
}

export interface Accepted {
  job_id: string | null;
  disabled_reason: string | null;
}

// Error body from app.py rc_error_handler.
export interface ErrorBody {
  code: string;
  message: string;
  retryable?: boolean;
  recovery_hint?: string | null;
  details?: unknown;
  retry_after?: number;
}

// Session bootstrap (GET /api/session), see src/refcollector/web/routes.py.
export interface Session {
  csrf_token: string;
  principal: string;
}

// "working" is client-only: rows inside the scope of an active job (contract section 3).
export type DisplayStatus = CitationStatus | "working";

export interface ShelfQuery {
  q?: string;
  kind?: PaperKind;
  status?: CitationStatus;
  cursor?: string;
  limit?: number;
}
