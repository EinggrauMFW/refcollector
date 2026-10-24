import { useEffect, useReducer, useRef, useState } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Citation, PendingFile } from "../api/types";
import { checkWords, formatBytes, hostOf } from "../lib/format";
import { fileViewerId } from "../lib/route";
import { twoStep } from "../lib/twostep";
import { Icon } from "./Icon";
import { ErrorBlock, IntentLines } from "./ui";

type FileAction = "approve" | "reject";

// A PDF the engine fetched but could not verify. The user looks at it, then approves or rejects it.
// Each decision is two steps (an intent with readable lines, then Confirm) and covers one file.
export function FilePanel({
  cid,
  citation: c,
  file,
  onOpenPdf,
  onChanged,
}: {
  cid: string;
  citation: Citation;
  file: PendingFile;
  onOpenPdf: (viewerId: string) => void;
  onChanged: () => void;
}) {
  const [pending, dispatch] = useReducer(twoStep<FileAction>, null);
  const [error, setError] = useState<ApiError | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (pending?.intent) confirmRef.current?.focus();
  }, [pending?.intent]);

  const body = (action: FileAction, key: string) => ({
    file_kind: file.file_kind ?? "work_file",
    work_file_id: file.work_file_id,
    action,
    expected_revision: c.revision,
    idempotency_key: key,
  });

  const start = async (action: FileAction) => {
    const key = newKey();
    setError(null);
    dispatch({ type: "start", target: action, key });
    try {
      dispatch({ type: "intent", intent: await api.fileReviewIntent(cid, c.citation_id, body(action, key)) });
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
      await api.fileReviewConfirm(cid, c.citation_id, body(target, key), intent.confirmation_token);
      dispatch({ type: "reset" });
      onChanged();
    } catch (e) {
      dispatch({ type: "reset" });
      setError(toApiError(e));
    }
  };

  const host = hostOf(file.source_url);
  const verb = pending?.target === "approve" ? "Approve" : "Reject";

  return (
    <section className="file-panel" aria-label="PDF waiting for approval">
      <h4 className="small-label">PDF waiting for your approval</h4>
      <p>
        RefCollector downloaded a PDF for <span className="serif">{c.title ?? "this citation"}</span>, but it could not confirm that the file is the right one. Open it, then decide.
      </p>
      <dl className="facts">
        <div>
          <dt>Size</dt>
          <dd className="num">{formatBytes(file.size)}</dd>
        </div>
        {host && (
          <div>
            <dt>From</dt>
            <dd className="mono">{host}</dd>
          </div>
        )}
      </dl>
      <div className="reasons">
        <p className="small-label">What the automatic checks did not confirm</p>
        {file.failed_checks.length > 0 ? (
          <ul className="check-list">
            {file.failed_checks.map((k) => (
              <li key={k}>
                <Icon name="alert" size={14} />
                <span>{checkWords(k)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">The server named no failed check.</p>
        )}
      </div>

      {error && <ErrorBlock error={error} />}

      {pending?.intent && (
        <div className="confirm-box" role="group" aria-label={`Confirm ${verb.toLowerCase()}`}>
          <p>
            <strong>{pending.target === "approve" ? "Approve this PDF?" : "Reject this PDF?"}</strong> This changes the citation. It is one file, one confirmation.
          </p>
          <IntentLines intent={pending.intent} />
        </div>
      )}

      <div className="file-actions">
        <button type="button" className="btn" onClick={() => onOpenPdf(fileViewerId(file.work_file_id, file.file_kind))}>
          <Icon name="file" />
          <span>Open PDF</span>
        </button>
        {pending?.intent ? (
          <>
            <button type="button" ref={confirmRef} className="btn btn-primary" onClick={confirm} disabled={pending.confirming}>
              {pending.confirming ? "Saving…" : `Confirm ${verb.toLowerCase()}`}
            </button>
            <button type="button" className="btn" onClick={() => dispatch({ type: "reset" })} disabled={pending.confirming}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn" onClick={() => start("approve")} disabled={pending !== null}>
              {pending?.target === "approve" ? "Preparing…" : "Approve PDF"}
            </button>
            <button type="button" className="btn btn-text" onClick={() => start("reject")} disabled={pending !== null}>
              {pending?.target === "reject" ? "Preparing…" : "Reject PDF"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
