import { useEffect, useReducer, useRef, useState } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Candidate, Citation } from "../api/types";
import { authorLine, metaLine, reasonList } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { displayStatus, primaryAction, reviewLabel, rowRun, type JobKind } from "../lib/status";
import { twoStep } from "../lib/twostep";
import { FilePanel } from "./FilePanel";
import { Icon } from "./Icon";
import { ErrorBlock, IntentLines, SkeletonRows, StatusBadge } from "./ui";

export function CitationRow({
  cid,
  citation: c,
  expanded,
  job,
  busy,
  downloadReason,
  verified = false,
  onToggle,
  onOpenPdf,
  onRun,
  onChanged,
  onAccepted,
}: {
  cid: string;
  citation: Citation;
  expanded: boolean;
  job: { kind: JobKind; citationId: string | null } | null;
  busy: boolean;
  downloadReason: string | null;
  verified?: boolean;
  onToggle: (oid: string | null) => void;
  onOpenPdf: (paperId: string) => void;
  onRun: (c: Citation, run: "find" | "download", useFallback?: boolean) => void;
  onChanged: () => void;
  onAccepted?: (citationId: string) => void;
}) {
  const status = displayStatus(c, job?.kind ?? null, job?.citationId ?? null);
  const action = primaryAction(c);
  const run = rowRun(c);
  const headline = c.title ?? c.text;
  const meta = metaLine([authorLine(c.authors), c.year, c.venue]);
  const panelId = `cite-panel-${c.citation_id}`;
  const toggleId = `cite-toggle-${c.citation_id}`;
  const label = action === "check" ? reviewLabel(c) : "Details";

  return (
    <li className="cite" data-expanded={expanded || undefined}>
      <div className="cite-head">
        <span className="cite-num num" aria-hidden="true">
          {c.order}
        </span>
        {action === "open" ? (
          <>
            <button type="button" className="cite-main cite-open" onClick={() => onOpenPdf(c.downloaded_paper_id!)} title="Open the PDF">
              <span className="cite-title serif">{headline}</span>
              {meta && <span className="cite-meta">{meta}</span>}
            </button>
            {verified ? (
              <span className="badge badge-positive" role="status">
                <Icon name="check" size={14} />
                <span>Metadata verified</span>
              </span>
            ) : (
              <StatusBadge status={status} />
            )}
            <button
              id={toggleId}
              type="button"
              className="icon-btn cite-chevron"
              aria-expanded={expanded}
              aria-controls={panelId}
              aria-label={`${label} for citation ${c.order}`}
              data-cite-toggle
              onClick={() => onToggle(expanded ? null : c.citation_id)}
            >
              <Icon name="chevron" />
            </button>
          </>
        ) : (
          <button
            id={toggleId}
            type="button"
            className="cite-toggle"
            aria-expanded={expanded}
            aria-controls={panelId}
            data-cite-toggle
            onClick={() => onToggle(expanded ? null : c.citation_id)}
          >
            <span className="cite-main">
              <span className="cite-title serif">{headline}</span>
              {meta && <span className="cite-meta">{meta}</span>}
            </span>
            {verified ? (
              <span className="badge badge-positive" role="status">
                <Icon name="check" size={14} />
                <span>Metadata verified</span>
              </span>
            ) : (
              <StatusBadge status={status} />
            )}
            {action === "check" && <span className="cite-cta">{label}</span>}
            <Icon name="chevron" />
          </button>
        )}
        {run && (
          <button type="button" className="btn btn-sm" onClick={() => onRun(c, run.run, run.useFallback)} disabled={busy || status === "working" || (run.run === "download" && Boolean(downloadReason))} title={run.run === "download" ? downloadReason ?? undefined : undefined}>
            {run.label}
          </button>
        )}
        {c.can_download_by_doi === false && !c.can_download && c.status !== "downloaded" && c.check_kind !== "file" && (
          <button type="button" className="btn btn-sm" disabled title="A single cited DOI and a configured DOI download service are required.">Download PDF by DOI</button>
        )}
      </div>
      {c.can_download_by_doi === false && !c.can_download && c.status !== "downloaded" && c.check_kind !== "file" && <p className="cite-meta">DOI download unavailable: a single cited DOI and a configured service are required.</p>}
      {c.metadata_review_pending && <p className="cite-meta">Metadata is unconfirmed. PDF download and access do not require metadata approval.</p>}
      {expanded && (
        <CitationPanel
          id={panelId}
          labelledBy={toggleId}
          cid={cid}
          citation={c}
          onOpenPdf={onOpenPdf}
          onChanged={onChanged}
          onAccepted={onAccepted}
        />
      )}
    </li>
  );
}

interface ReviewTarget {
  candidateId: string;
  action: "accept" | "reject";
}

// The Check panel: the citation as written, what was found, and candidates to accept or reject.
// Accept and reject are two steps: an intent summary first, then an explicit confirmation.
function CitationPanel({
  id,
  labelledBy,
  cid,
  citation: c,
  onOpenPdf,
  onChanged,
  onAccepted,
}: {
  id: string;
  labelledBy: string;
  cid: string;
  citation: Citation;
  onOpenPdf: (paperId: string) => void;
  onChanged: () => void;
  onAccepted?: (citationId: string) => void;
}) {
  const detail = useAsync(() => api.citationDetail(cid, c.citation_id), `${cid}|${c.citation_id}|${c.revision}`);
  const [pending, dispatch] = useReducer(twoStep<ReviewTarget>, null);
  const [error, setError] = useState<ApiError | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (pending?.intent) confirmRef.current?.focus();
  }, [pending?.intent]);

  const start = async (candidate: Candidate, action: "accept" | "reject") => {
    const key = newKey();
    setError(null);
    dispatch({ type: "start", target: { candidateId: candidate.candidate_id, action }, key });
    try {
      const intent = await api.reviewIntent(cid, c.citation_id, {
        action,
        candidate_id: candidate.candidate_id,
        expected_revision: c.revision,
        idempotency_key: key,
      });
      dispatch({ type: "intent", intent });
    } catch (e) {
      dispatch({ type: "reset" });
      setError(toApiError(e));
    }
  };

  const confirm = async () => {
    if (!pending?.intent) return;
    const { target, key, intent } = pending;
    dispatch({ type: "confirming" });
    try {
      await api.reviewConfirm(
        cid,
        c.citation_id,
        { action: target.action, candidate_id: target.candidateId, expected_revision: c.revision, idempotency_key: key },
        intent.confirmation_token,
      );
      dispatch({ type: "reset" });
      if (target.action === "accept") {
        onAccepted?.(c.citation_id);
      } else {
        onChanged();
        rootRef.current?.focus();
      }
    } catch (e) {
      dispatch({ type: "reset" });
      setError(toApiError(e));
    }
  };

  const fileCheck = c.check_kind === "file";
  const pendingFile = detail.data?.pending_file ?? null;
  const candidates = fileCheck ? [] : (detail.data?.candidates ?? []);

  return (
    <div id={id} ref={rootRef} className="cite-panel" role="region" aria-labelledby={labelledBy} tabIndex={-1}>
      <figure className="cite-as">
        <figcaption className="small-label">Cited as</figcaption>
        <blockquote className="serif">{c.text}</blockquote>
      </figure>

      {(c.title || c.doi) && (
        <dl className="facts">
          {c.title && (
            <div>
              <dt>{c.metadata_review_pending ? "Metadata suggestion" : "Matched to"}</dt>
              <dd className="serif">{c.title}</dd>
            </div>
          )}
          {c.authors.length > 0 && (
            <div>
              <dt>Authors</dt>
              <dd>{c.authors.map((a) => a.display).join("; ")}</dd>
            </div>
          )}
          {(c.year || c.venue) && (
            <div>
              <dt>Published</dt>
              <dd>{metaLine([c.venue, c.year])}</dd>
            </div>
          )}
          {c.doi && (
            <div>
              <dt>DOI</dt>
              <dd>
                <a href={`https://doi.org/${c.doi}`} target="_blank" rel="noopener noreferrer" className="mono">
                  {c.doi}
                </a>
              </dd>
            </div>
          )}
        </dl>
      )}

      {c.reasons.length > 0 && (
        <div className="reasons">
          <p className="small-label">{c.metadata_review_pending ? "Metadata warnings" : c.status === "failed" ? "What went wrong" : "Why"}</p>
          <ul className="chips">
            {reasonList(c.reasons).map((r) => (
              <li key={r} className="chip">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {c.status === "downloaded" && c.downloaded_paper_id && (
        <p>
          <button type="button" className="btn" onClick={() => onOpenPdf(c.downloaded_paper_id!)}>
            <Icon name="file" />
            <span>Open PDF</span>
          </button>
        </p>
      )}

      {error && <ErrorBlock error={error} onRetry={detail.reload} />}
      {fileCheck && pendingFile && <FilePanel cid={cid} citation={c} file={pendingFile} onOpenPdf={onOpenPdf} onChanged={onChanged} />}
      {fileCheck && !pendingFile && !detail.loading && !detail.error && (
        <p className="muted">The PDF waiting for approval is not available. Reload the paper and try again.</p>
      )}
      {detail.loading && !detail.data && <SkeletonRows rows={2} />}
      {detail.error && <ErrorBlock error={detail.error} onRetry={detail.reload} title="Could not load the candidates." />}

      {candidates.length > 0 && (
        <section aria-label="Candidates" className="cands">
          <h4 className="small-label">{c.metadata_review_pending ? "Metadata candidates (optional)" : "Candidates"}</h4>
          <ol className="cand-list">
            {candidates.map((k) => {
              const mine = pending?.target.candidateId === k.candidate_id ? pending : null;
              const meta = metaLine([authorLine(k.authors), k.year, k.venue]);
              return (
                <li key={k.candidate_id} className="cand" data-rejected={k.rejected || undefined}>
                  <span className="cand-rank num" aria-label={`Rank ${k.rank}`}>
                    {k.rank}
                  </span>
                  <div className="cand-main">
                    <p className="cand-title serif">{k.title ?? "Untitled"}</p>
                    {meta && <p className="cite-meta">{meta}</p>}
                    {k.doi && (
                      <p className="cite-meta">
                        <a href={`https://doi.org/${k.doi}`} target="_blank" rel="noopener noreferrer" className="mono">
                          {k.doi}
                        </a>
                      </p>
                    )}
                    {k.reasons.length > 0 && (
                      <ul className="chips">
                        {reasonList(k.reasons).map((r) => (
                          <li key={r} className="chip">
                            {r}
                          </li>
                        ))}
                      </ul>
                    )}
                    {mine?.intent && (
                      <div className="confirm-box" role="group" aria-label="Confirm review">
                        <p>
                          <strong>{mine.target.action === "accept" ? "Accept this match?" : "Reject this candidate?"}</strong> This changes the citation. It is one citation, one confirmation.
                        </p>
                        <IntentLines intent={mine.intent} />
                      </div>
                    )}
                  </div>
                  <div className="cand-act">
                    {k.rejected ? (
                      <span className="badge badge-neutral">
                        <Icon name="x-circle" size={14} />
                        <span>Rejected</span>
                      </span>
                    ) : mine?.intent ? (
                      <>
                        <button type="button" ref={confirmRef} className="btn btn-primary" onClick={confirm} disabled={mine.confirming}>
                          {mine.confirming ? "Saving…" : mine.target.action === "accept" ? "Confirm accept" : "Confirm reject"}
                        </button>
                        <button type="button" className="btn" onClick={() => dispatch({ type: "reset" })} disabled={mine.confirming}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button type="button" className="btn" onClick={() => start(k, "accept")} disabled={pending !== null}>
                          {mine && mine.target.action === "accept" ? "Preparing…" : "Accept"}
                        </button>
                        <button type="button" className="btn btn-text" onClick={() => start(k, "reject")} disabled={pending !== null}>
                          {mine && mine.target.action === "reject" ? "Preparing…" : "Reject"}
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      )}
      {!detail.loading && !detail.error && !fileCheck && candidates.length === 0 && (c.status === "check" || c.status === "no_match") && (
        <p className="muted">No candidates were found for this citation.</p>
      )}
    </div>
  );
}
