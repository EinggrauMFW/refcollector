import type {
  Accepted,
  Citation,
  CitationDetail,
  Collection,
  CollectionConsequences,
  CreateCollectionIn,
  FileReviewIn,
  FileKind,
  Defaults,
  Intent,
  JobStatus,
  PaperCard,
  PaperDetail,
  PermissionIntentIn,
  ReviewIn,
  RunIn,
  ShelfPage,
  ShelfQuery,
} from "./types";

// One method per route of contract section 5. The real client and the mock both implement it.
export interface LibraryApi {
  defaults(): Promise<Defaults>;
  listCollections(): Promise<Collection[]>;
  createCollection(body: CreateCollectionIn): Promise<Collection>;
  renameCollection(cid: string, name: string): Promise<Collection>;
  collectionConsequences(cid: string): Promise<CollectionConsequences>;
  deleteCollection(cid: string): Promise<void>;
  permissionIntent(cid: string, body: PermissionIntentIn): Promise<Intent>;
  permissionConfirm(cid: string, body: PermissionIntentIn, token: string): Promise<Collection>;
  shelf(cid: string, query: ShelfQuery): Promise<ShelfPage>;
  uploadPaper(cid: string, file: File, idempotencyKey: string): Promise<PaperCard>;
  paperDetail(cid: string, pid: string): Promise<PaperDetail>;
  findMetadata(cid: string, body: RunIn): Promise<Accepted>;
  download(cid: string, body: RunIn): Promise<Accepted>;
  citationDetail(cid: string, oid: string): Promise<CitationDetail>;
  reviewIntent(cid: string, oid: string, body: ReviewIn): Promise<Intent>;
  reviewConfirm(cid: string, oid: string, body: ReviewIn, token: string): Promise<Citation>;
  fileReviewIntent(cid: string, oid: string, body: FileReviewIn): Promise<Intent>;
  fileReviewConfirm(cid: string, oid: string, body: FileReviewIn, token: string): Promise<Citation>;
  // URL of a work file's PDF for the viewer (GET /files/work_file/{id}).
  workFileUrl(cid: string, workFileId: string, kind?: FileKind): string;
  sync(cid: string): Promise<Collection>;
  job(cid: string, jid: string): Promise<JobStatus>;
}

// Error shape the UI shows: server `message` plus `recovery_hint` (spec section 8).
export class ApiError extends Error {
  readonly code: string;
  readonly recovery_hint: string | null;
  readonly status: number;
  readonly retryable: boolean;

  constructor(code: string, message: string, recovery_hint: string | null = null, status = 0, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.recovery_hint = recovery_hint;
    this.status = status;
    this.retryable = retryable;
  }
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  const message = err instanceof Error ? err.message : String(err);
  return new ApiError("UNKNOWN", message);
}

export const newKey = (): string => crypto.randomUUID();
