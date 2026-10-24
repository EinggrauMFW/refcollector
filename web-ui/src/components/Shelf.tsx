import { useEffect, useId, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Collection, CitationStatus, JobStatus, PaperCard, PaperKind } from "../api/types";
import { fileFingerprint, keyFor } from "../lib/idempotency";
import { jobForPaper, type JobState, type LocalJob } from "../lib/job";
import { navigate, useAsync, useDebounced, useJob } from "../lib/hooks";
import { downloadBlockReason } from "../lib/permissions";
import { filtersActive, shelfQuery } from "../lib/shelfQuery";
import { fileOfViewer, parseHash, type Route } from "../lib/route";
import { downloadedPdf, type JobKind } from "../lib/status";
import { Icon } from "./Icon";
import type { PaperActions } from "./PaperBody";
import { PaperRow, type RowJob } from "./PaperRow";
import { PdfViewer } from "./PdfViewer";
import { PermissionPanel } from "./PermissionPanel";
import { ErrorBlock, JobBar, Notice, SkeletonRows } from "./ui";

const KINDS: Array<{ v: PaperKind | ""; label: string }> = [
  { v: "", label: "All" },
  { v: "uploaded", label: "Uploaded" },
  { v: "downloaded", label: "Downloaded" },
];
const STATUSES: Array<{ v: CitationStatus | ""; label: string }> = [
  { v: "", label: "Any status" },
  { v: "check", label: "Needs check" },
  { v: "no_pdf", label: "Missing PDF" },
  { v: "pdf_available", label: "PDF available" },
  { v: "downloaded", label: "Downloaded" },
  { v: "failed", label: "Failed" },
];

interface NoticeItem {
  id: number;
  tone: "neutral" | "warning" | "negative";
  text: string;
}

function JobPoller({
  cid,
  jobId,
  onStatus,
  onError,
  onDone,
}: {
  cid: string;
  jobId: string;
  onStatus: (id: string, s: JobStatus) => void;
  onError: (id: string, e: ApiError | null) => void;
  onDone: (id: string, s: JobStatus) => void;
}) {
  useJob(cid, jobId, {
    onStatus: (s) => onStatus(jobId, s),
    onError: (e) => onError(jobId, e),
    onDone: (s) => onDone(jobId, s),
  });
  return null;
}

let noticeSeq = 0;

export function Shelf({
  collection,
  route,
  onCollectionChange,
  onCollectionsStale,
}: {
  collection: Collection;
  route: Route;
  onCollectionChange: (c: Collection) => void;
  onCollectionsStale: () => void;
}) {
  const cid = collection.collection_id;
  const searchId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const uploadKeys = useRef(new Map<string, string>());
  const doiJobs = useRef(new Map<string, { paperId: string; citationId: string }>());

  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const [kind, setKind] = useState<PaperKind | "">("");
  const [status, setStatus] = useState<CitationStatus | "">("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [extra, setExtra] = useState<{ key: string; papers: PaperCard[]; cursor: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [local, setLocal] = useState<Record<string, LocalJob>>({});
  const [states, setStates] = useState<Record<string, JobState>>({});
  const [finished, setFinished] = useState<ReadonlySet<string>>(new Set());
  const [notices, setNotices] = useState<NoticeItem[]>([]);
  const [uploading, setUploading] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [permOpen, setPermOpen] = useState<boolean | null>(null);
  const [runBusy, setRunBusy] = useState<"find" | "download" | null>(null);

  const filters = { q, kind, status };
  const filtersOn = filtersActive(filters);
  const shelfKey = `${cid}|${q}|${kind}|${status}`;
  const query = shelfQuery(filters);
  const shelf = useAsync(() => api.shelf(cid, query), shelfKey, refreshKey);

  const extraKey = `${shelfKey}#${refreshKey}`;
  const more = extra && extra.key === extraKey ? extra : null;
  const papers = [...(shelf.data?.papers ?? []), ...(more?.papers ?? [])];
  const cursor = more ? more.cursor : (shelf.data?.next_cursor ?? null);

  // A deep link to a paper that is not on the loaded pages: fetch it by id and show it first.
  const wanted = route.paper;
  const missing = Boolean(wanted && shelf.data && !papers.some((p) => p.paper_id === wanted));
  const pinned = useAsync(async () => (missing && wanted ? (await api.paperDetail(cid, wanted)).paper : null), `pin|${cid}|${missing ? wanted : ""}`, refreshKey);
  const shown = pinned.data ? [pinned.data, ...papers] : papers;

  const addNotice = (tone: NoticeItem["tone"], text: string) => setNotices((n) => [...n, { id: ++noticeSeq, tone, text }]);
  const bump = () => {
    setRefreshKey((k) => k + 1);
    onCollectionsStale();
  };

  // "/" focuses search unless the user is typing somewhere.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ----- jobs -----
  const collectionJob = local["*"] ?? null;
  const jobIds = [...new Set([...shown.map((p) => p.active_job_id), ...Object.values(local).map((l) => l.jobId)])].filter(
    (id): id is string => Boolean(id) && !finished.has(id as string),
  );
  const onStatus = (id: string, s: JobStatus) => setStates((p) => ({ ...p, [id]: { status: s, error: null, n: (p[id]?.n ?? 0) + 1 } }));
  const onError = (id: string, e: ApiError | null) => setStates((p) => ({ ...p, [id]: { status: p[id]?.status ?? null, error: e, n: p[id]?.n ?? 0 } }));
  const onDone = (id: string, s: JobStatus) => {
    const doi = doiJobs.current.get(id);
    doiJobs.current.delete(id);
    if (doi && s.state !== "failed" && s.state !== "cancelled") {
      void api.paperDetail(cid, doi.paperId).then((detail) => {
        const current = parseHash(location.hash);
        const pdf = downloadedPdf(detail.citations, doi.citationId);
        if (pdf && current.cid === cid && current.paper === doi.paperId && !current.pdf) navigate({ ...current, citation: doi.citationId, pdf });
      }).catch((e) => addNotice("negative", toApiError(e).message));
    }
    setFinished((f) => new Set(f).add(id));
    setLocal((p) => Object.fromEntries(Object.entries(p).filter(([, l]) => l.jobId !== id)));
    setStates((p) => Object.fromEntries(Object.entries(p).filter(([k]) => k !== id)));
    if (s.state === "failed") addNotice("negative", "A job failed. Open the paper to see which citations need attention.");
    else if (s.state === "cancelled") addNotice("neutral", "A job was cancelled.");
    for (const w of s.warnings) addNotice("warning", w);
    bump();
  };

  const jobFor = (card: PaperCard): RowJob | null => {
    const job = jobForPaper(card.paper_id, card.active_job_id, local, states, finished);
    return job && { ...job, bar: job.id !== collectionJob?.jobId };
  };

  const startRun = (kind: JobKind, jobId: string, key: string, citationId: string | null = null) =>
    setLocal((p) => ({ ...p, [key]: { jobId, kind, citationId } }));

  const runAll = async (kind: "find" | "download", useFallback = false) => {
    setRunBusy(kind);
    try {
      const body = { paper_id: null, idempotency_key: newKey(), use_fallback: useFallback };
      const r = kind === "find" ? await api.findMetadata(cid, body) : await api.download(cid, body);
      if (r.job_id) startRun(kind, r.job_id, "*");
      else addNotice("neutral", r.disabled_reason ?? "Nothing to do for this collection.");
    } catch (e) {
      addNotice("negative", `${toApiError(e).message} ${toApiError(e).recovery_hint ?? ""}`.trim());
    } finally {
      setRunBusy(null);
    }
  };

  // ----- upload -----
  const upload = async (files: File[]) => {
    for (const [i, file] of files.entries()) {
      setUploading(`Uploading ${file.name} (${i + 1} of ${files.length})`);
      if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
        addNotice("negative", `${file.name}: only PDF files can be added.`);
        continue;
      }
      try {
        // The same file keeps its key until it succeeds, so a retry cannot create a second paper.
        const fp = fileFingerprint(file);
        const card = await api.uploadPaper(cid, file, keyFor(uploadKeys.current, fp, newKey));
        uploadKeys.current.delete(fp);
        if (card.active_job_id) startRun("extract", card.active_job_id, card.paper_id);
      } catch (e) {
        const err = toApiError(e);
        addNotice("negative", `${file.name}: ${err.message} ${err.recovery_hint ?? ""}`.trim());
      }
    }
    setUploading(null);
    bump();
  };

  const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  const dragProps = {
    onDragEnter: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    },
    onDragOver: (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    },
    onDragLeave: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      void upload(Array.from(e.dataTransfer.files));
    },
  };

  // ----- list -----
  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await api.shelf(cid, { ...query, cursor });
      setExtra({ key: extraKey, papers: [...(more?.papers ?? []), ...page.papers], cursor: page.next_cursor });
    } catch (e) {
      addNotice("negative", toApiError(e).message);
    } finally {
      setLoadingMore(false);
    }
  };

  const onListKey = (e: KeyboardEvent<HTMLUListElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const t = e.target as HTMLElement;
    if (!t.matches("[data-paper-toggle]")) return;
    const all = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[data-paper-toggle]"));
    const i = all.indexOf(t);
    const next = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : e.key === "ArrowDown" ? i + 1 : i - 1;
    const el = all[Math.max(0, Math.min(all.length - 1, next))];
    if (el) {
      e.preventDefault();
      el.focus();
    }
  };

  const actions: PaperActions = {
    onCitation: (oid) => navigate({ cid, paper: route.paper, citation: oid }),
    onOpenPdf: (pid) => navigate({ cid, paper: route.paper, citation: route.citation, pdf: pid }),
    onOpenPaper: (pid) => navigate({ cid, paper: pid }),
    onRunStarted: (kind, jobId, pid, citationId, openDoiPdf) => {
      if (openDoiPdf && citationId) doiJobs.current.set(jobId, { paperId: pid, citationId });
      startRun(kind, jobId, pid, citationId);
    },
    onChanged: bump,
  };
  const titleOf = (pid: string) => shown.find((p) => p.paper_id === pid)?.title ?? null;
  const toggle = (pid: string | null) => navigate({ cid, paper: pid });

  const p = collection.permissions;
  const allGranted = p.can_download;
  const showPerms = permOpen ?? !allGranted;
  const blockReason = downloadBlockReason(p);
  const empty = !shelf.loading && !shelf.error && shelf.data && shown.length === 0;

  return (
    <div className="shelf-page" {...dragProps}>
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <p>Drop PDFs to add them to {collection.name}</p>
        </div>
      )}

      <header className="title-row">
        <div className="title-text">
          <h1>{collection.name}</h1>
          <p className="subline">
            <span className="num">{collection.paper_count}</span> {collection.paper_count === 1 ? "paper" : "papers"}
            {" · "}
            <span className="num">{collection.check_count}</span> {collection.check_count === 1 ? "citation" : "citations"} to check
            {collection.check_count > 0 && (
              <>
                {" · "}
                <button type="button" className="link" onClick={() => setStatus("check")}>
                  Show papers to check
                </button>
              </>
            )}
          </p>
          {collection.folder && <p className="folder-line mono">{collection.folder}</p>}
        </div>
        <div className="title-actions">
          <button type="button" className="btn" onClick={() => runAll("find")} disabled={runBusy !== null || collectionJob !== null}>
            <Icon name="search" />
            <span>Find metadata</span>
          </button>
          <button
            type="button"
            className="btn"
            onClick={blockReason ? undefined : () => runAll("download")}
            aria-disabled={Boolean(blockReason)}
            disabled={runBusy !== null || collectionJob !== null}
            aria-describedby={blockReason ? "dl-reason" : undefined}
          >
            <Icon name="download" />
            <span>Download all</span>
          </button>
          <button
            type="button"
            className="btn"
            onClick={blockReason ? undefined : () => runAll("download", true)}
            aria-disabled={Boolean(blockReason)}
            disabled={runBusy !== null || collectionJob !== null}
            aria-describedby={blockReason ? "dl-reason" : undefined}
            title="Download by the DOI in each citation, before metadata review. Only PDFs that fail checks need confirmation."
          >
            <Icon name="download" />
            <span>Download PDFs by DOI</span>
          </button>
          <button type="button" className="btn btn-primary" onClick={() => fileRef.current?.click()}>
            <Icon name="upload" />
            <span>Upload PDFs</span>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf"
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void upload(files);
            }}
          />
        </div>
      </header>
      <p className="muted reason">DOI downloads use the cited DOI before metadata review. Only PDFs that fail checks need confirmation.</p>
      {blockReason && (
        <p id="dl-reason" className="muted reason">
          Download all is unavailable. {blockReason}
        </p>
      )}

      {showPerms ? (
        <PermissionPanel collection={collection} onChange={onCollectionChange} />
      ) : (
        <p className="perm-line">
          <Icon name="check" />
          <span>Folder, network and downloads are allowed for this collection.</span>
          <button type="button" className="link" onClick={() => setPermOpen(true)}>
            Review permissions
          </button>
        </p>
      )}

      {jobIds.map((id) => (
        <JobPoller key={id} cid={cid} jobId={id} onStatus={onStatus} onError={onError} onDone={onDone} />
      ))}
      {collectionJob && (
        <div className="collection-job">
          <JobBar status={states[collectionJob.jobId]?.status ?? null} error={states[collectionJob.jobId]?.error ?? null} />
        </div>
      )}
      {notices.map((n) => (
        <Notice key={n.id} tone={n.tone} onDismiss={() => setNotices((all) => all.filter((x) => x.id !== n.id))}>
          {n.text}
        </Notice>
      ))}
      <p className="sr-only" role="status">
        {uploading}
      </p>
      {uploading && (
        <p className="muted upload-line" aria-hidden="true">
          {uploading}
        </p>
      )}

      <section aria-label="Shelf">
        <div className="filters">
          <div className="search">
            <Icon name="search" />
            <label htmlFor={searchId} className="sr-only">
              Search by title, author, year, venue or DOI
            </label>
            <input
              id={searchId}
              ref={searchRef}
              type="search"
              className="input search-input"
              placeholder="Search papers"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && search) {
                  setSearch("");
                  e.stopPropagation();
                }
              }}
              autoComplete="off"
            />
            <kbd className="kbd" aria-hidden="true">
              /
            </kbd>
          </div>
          <div className="seg-control seg-text" role="radiogroup" aria-label="Kind">
            {KINDS.map((k) => (
              <button key={k.v} type="button" role="radio" aria-checked={kind === k.v} className="seg-btn" onClick={() => setKind(k.v)}>
                {k.label}
              </button>
            ))}
          </div>
          <label className="select">
            <span className="sr-only">Citation status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value as CitationStatus | "")}>
              {STATUSES.map((s) => (
                <option key={s.v} value={s.v}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {shelf.data && (
          <p className="count-line muted" role="status">
            Showing <span className="num">{papers.length}</span> of <span className="num">{shelf.data.total}</span>
            {filtersOn && (
              <>
                {" · "}
                <button
                  type="button"
                  className="link"
                  onClick={() => {
                    setSearch("");
                    setKind("");
                    setStatus("");
                  }}
                >
                  Clear filters
                </button>
              </>
            )}
          </p>
        )}

        {shelf.loading && !shelf.data && <SkeletonRows />}
        {shelf.error && <ErrorBlock error={shelf.error} onRetry={shelf.reload} title="Could not load the shelf." />}

        {empty && filtersOn && (
          <div className="empty">
            <p className="empty-title">No papers match these filters.</p>
            <p className="muted">Try a different search, or clear the filters.</p>
          </div>
        )}
        {empty && !filtersOn && (
          <div className="empty empty-drop">
            <p className="empty-title">This shelf is empty.</p>
            <p className="muted">Drop a PDF here. RefCollector reads its citations and lists them under the paper.</p>
            <button type="button" className="btn btn-primary" onClick={() => fileRef.current?.click()}>
              <Icon name="upload" />
              <span>Choose PDFs</span>
            </button>
          </div>
        )}

        {pinned.error && <ErrorBlock error={pinned.error} onRetry={pinned.reload} title="Could not open the linked paper." />}

        {shown.length > 0 && (
          <ul className="paper-list" onKeyDown={onListKey}>
            {shown.map((card) => (
              <PaperRow
                key={card.paper_id}
                collection={collection}
                card={card}
                expanded={route.paper === card.paper_id}
                citationId={route.paper === card.paper_id ? route.citation : null}
                job={jobFor(card)}
                refreshKey={refreshKey}
                titleOf={titleOf}
                onToggle={toggle}
                actions={actions}
              />
            ))}
          </ul>
        )}

        {cursor && (
          <p className="more">
            <button type="button" className="btn" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Show more papers"}
            </button>
          </p>
        )}

        {shown.length > 0 && (
          <button type="button" className="slot" onClick={() => fileRef.current?.click()}>
            <Icon name="upload" />
            <span>Drop PDFs here, or choose files</span>
          </button>
        )}
      </section>

      {route.pdf && (
        <PdfViewer
          key={route.pdf}
          cid={cid}
          paperId={fileOfViewer(route.pdf) ? undefined : route.pdf}
          fileId={fileOfViewer(route.pdf)?.id}
          fileKind={fileOfViewer(route.pdf)?.kind}
          onClose={() => {
            navigate({ cid, paper: route.paper, citation: route.citation });
            if (route.citation) document.getElementById(`cite-toggle-${route.citation}`)?.focus();
          }}
        />
      )}
    </div>
  );
}
