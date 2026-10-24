import { useState, type Ref } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Citation, Collection, PaperCard } from "../api/types";
import { formatDate, metaLine } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { downloadBlockReason } from "../lib/permissions";
import type { PaperJob } from "../lib/job";
import { STATUS_ORDER } from "../lib/status";
import { CitationRow } from "./CitationPanel";
import { Icon } from "./Icon";
import { CopyButton, ErrorBlock, SkeletonRows, StatusBadge } from "./ui";

export interface PaperActions {
  onCitation: (oid: string | null) => void;
  onOpenPdf: (paperId: string) => void;
  onOpenPaper: (paperId: string) => void;
  onRunStarted: (kind: "find" | "download", jobId: string, paperId: string, citationId: string | null, openDoiPdf?: boolean) => void;
  onChanged: () => void;
}

export function PaperBody({
  id,
  labelledBy,
  collection,
  card,
  job,
  citationId,
  refreshKey,
  titleOf,
  actions,
  regionRef,
}: {
  id: string;
  labelledBy: string;
  collection: Collection;
  card: PaperCard;
  job: PaperJob | null;
  citationId: string | null;
  refreshKey: number;
  titleOf: (paperId: string) => string | null;
  actions: PaperActions;
  regionRef: Ref<HTMLDivElement>;
}) {
  const cid = collection.collection_id;
  const detail = useAsync(() => api.paperDetail(cid, card.paper_id), `${cid}|${card.paper_id}`, `${refreshKey}.${job?.n ?? 0}`);
  const [busy, setBusy] = useState<"find" | "download" | "sync" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [verifiedCitations, setVerifiedCitations] = useState<Set<string>>(() => new Set());

  const handleCitationAccepted = (acceptedCitationId: string) => {
    setVerifiedCitations((prev) => new Set(prev).add(acceptedCitationId));
    actions.onCitation(null);
    detail.reload();
    actions.onChanged();
    requestAnimationFrame(() => {
      const toggleBtn = document.getElementById(`cite-toggle-${acceptedCitationId}`);
      toggleBtn?.focus();
    });
  };

  const uploaded = card.kind === "uploaded";
  const blockReason = downloadBlockReason(collection.permissions);
  const running = job !== null;

  // Paper-wide, or for one citation (contract section 8.3). The server wins ties: citation, then paper.
  const run = async (kind: "find" | "download", citation: Citation | null = null, useFallback = false) => {
    setMessage(null);
    setError(null);
    setBusy(kind);
    try {
      const body = { paper_id: card.paper_id, citation_id: citation?.citation_id ?? null, use_fallback: useFallback, idempotency_key: newKey() };
      const r = kind === "find" ? await api.findMetadata(cid, body) : await api.download(cid, body);
      if (r.job_id) actions.onRunStarted(kind, r.job_id, card.paper_id, citation?.citation_id ?? null, useFallback);
      else setMessage(r.disabled_reason ?? "Nothing to do for this paper.");
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const retrySync = async () => {
    setBusy("sync");
    setError(null);
    try {
      await api.sync(cid);
      // The sync answer is the collection, not the paper: read the paper again to see whether it landed.
      const again = await api.paperDetail(cid, card.paper_id);
      if (!again.paper.folder_path) setMessage("The file is still not in the collection folder. Check the folder access under Permissions, then retry.");
      actions.onChanged();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  const d = detail.data;
  const citations = d?.citations ?? [];
  const present = STATUS_ORDER.filter((s) => citations.some((c) => c.status === s));

  return (
    <div id={id} ref={regionRef} className="paper-body" role="region" aria-labelledby={labelledBy} tabIndex={-1}>
      <div className="toolbar-row">
        <button type="button" className="btn" onClick={() => actions.onOpenPdf(card.paper_id)}>
          <Icon name="file" />
          <span>Open PDF</span>
        </button>
        <CopyButton text={card.folder_path} disabledReason="This file is not in the collection folder yet." />
        {uploaded && (
          <>
            <button type="button" className="btn" onClick={() => run("find")} disabled={running || busy !== null}>
              <Icon name="search" />
              <span>{busy === "find" ? "Starting…" : "Find metadata"}</span>
            </button>
            <button
              type="button"
              className="btn"
              onClick={blockReason ? undefined : () => run("download")}
              aria-disabled={Boolean(blockReason) || running || busy !== null}
              disabled={running || busy !== null}
              aria-describedby={blockReason ? `${id}-dl-reason` : undefined}
            >
              <Icon name="download" />
              <span>{busy === "download" ? "Starting…" : "Download all"}</span>
            </button>
            <button
              type="button"
              className="btn"
              onClick={blockReason ? undefined : () => run("download", null, true)}
              aria-disabled={Boolean(blockReason) || running || busy !== null}
              disabled={running || busy !== null}
              aria-describedby={blockReason ? `${id}-dl-reason` : undefined}
              title="Download by the DOI in each citation, before metadata review. Only PDFs that fail checks need confirmation."
            >
              <Icon name="download" />
              <span>Download PDFs by DOI</span>
            </button>
          </>
        )}
        {!card.folder_path && (
          <span className="folder-state">
            <span className="muted">Not in folder</span>
            <button type="button" className="btn btn-text" onClick={retrySync} disabled={busy !== null}>
              {busy === "sync" ? "Syncing…" : "Retry sync"}
            </button>
          </span>
        )}
      </div>
      {uploaded && blockReason && (
        <p id={`${id}-dl-reason`} className="muted reason">
          Download all is unavailable. {blockReason} Grant access under Permissions.
        </p>
      )}
      {uploaded && <p className="muted reason">DOI downloads use the cited DOI before metadata review. Only PDFs that fail checks need confirmation.</p>}
      {message && (
        <p className="muted reason" role="status">
          {message}
        </p>
      )}
      {error && <ErrorBlock error={error} />}

      <dl className="facts">
        {card.authors.length > 0 && (
          <div>
            <dt>Authors</dt>
            <dd>{card.authors.map((a) => a.display).join("; ")}</dd>
          </div>
        )}
        <div>
          <dt>Published</dt>
          <dd>{metaLine([card.venue, card.year]) || "—"}</dd>
        </div>
        <div>
          <dt>DOI</dt>
          <dd>
            {card.doi ? (
              <a href={`https://doi.org/${card.doi}`} target="_blank" rel="noopener noreferrer" className="mono">
                {card.doi}
              </a>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div>
          <dt>File</dt>
          <dd className="mono">{card.file_name}</dd>
        </div>
        {uploaded && (
          <>
            <div>
              <dt>Pages</dt>
              <dd className="num">{d?.pages ?? "—"}</dd>
            </div>
            <div>
              <dt>Added</dt>
              <dd className="num">{formatDate(d?.imported_at ?? null)}</dd>
            </div>
          </>
        )}
        {card.folder_path && (
          <div>
            <dt>In folder</dt>
            <dd className="mono">{card.folder_path}</dd>
          </div>
        )}
      </dl>

      {!uploaded && (
        <section aria-label="Cited by" className="cited-by">
          <h4 className="section-title-sm">Cited by</h4>
          {card.cited_by.length === 0 ? (
            <p className="muted">No uploaded paper cites this one.</p>
          ) : (
            <ul className="plain-list">
              {card.cited_by.map((pid) => (
                <li key={pid}>
                  <button type="button" className="link serif" onClick={() => actions.onOpenPaper(pid)}>
                    {titleOf(pid) ?? "Uploaded paper"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {uploaded && (
        <section aria-labelledby={`${id}-cites`} className="cites">
          <div className="cites-head">
            <h4 id={`${id}-cites`} className="section-title-sm">
              Citations <span className="num muted">{citations.length || card.citation_count}</span>
            </h4>
            <div className="cites-summary" aria-label="Citation statuses">
              {present.map((s) => (
                <StatusBadge key={s} status={s} count={citations.filter((c) => c.status === s).length} />
              ))}
            </div>
          </div>
          {detail.loading && !d && <SkeletonRows rows={4} />}
          {detail.error && <ErrorBlock error={detail.error} onRetry={detail.reload} title="Could not load the citations." />}
          {d && citations.length === 0 && (
            <p className="muted empty-line">{running ? "Reading the citations from this paper." : "No citations were found in this PDF."}</p>
          )}
          {citations.length > 0 && (
            <ol className="cite-list">
              {citations.map((c) => (
                <CitationRow
                  key={c.citation_id}
                  cid={cid}
                  citation={c}
                  expanded={citationId === c.citation_id}
                  job={job}
                  busy={busy !== null || running}
                  downloadReason={blockReason}
                  verified={verifiedCitations.has(c.citation_id)}
                  onToggle={actions.onCitation}
                  onOpenPdf={actions.onOpenPdf}
                  onRun={(c, kind, useFallback) => run(kind, c, useFallback)}
                  onChanged={() => {
                    detail.reload();
                    actions.onChanged();
                  }}
                  onAccepted={handleCitationAccepted}
                />
              ))}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}
