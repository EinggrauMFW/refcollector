import { useEffect, useRef } from "react";
import { api } from "../api";
import type { FileKind } from "../api/types";
import { authorLine, metaLine } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { Icon } from "./Icon";
import { CopyButton, ErrorBlock, SkeletonRows } from "./ui";

// Side-panel overlay. A native modal <dialog> gives the focus trap, Esc and focus restore.
// `fileId` shows a PDF that waits for approval (a work file); `paperId` shows a shelf paper.
export function PdfViewer({ cid, paperId, fileId, fileKind, onClose }: { cid: string; paperId?: string; fileId?: string; fileKind?: FileKind; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const detail = useAsync(async () => (paperId ? api.paperDetail(cid, paperId) : null), `${cid}|${paperId ?? ""}`);

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
  }, []);

  const p = detail.data?.paper;
  const src = fileId ? api.workFileUrl(cid, fileId, fileKind) : p?.file_url;
  const title = fileId ? "PDF waiting for approval" : (p?.title ?? "PDF");
  return (
    <dialog ref={ref} className="dialog viewer" aria-labelledby="viewer-title" onClose={onClose} onKeyDown={(e) => e.stopPropagation()}>
      <div className="viewer-head">
        <div className="viewer-titles">
          <h2 id="viewer-title" className="serif">
            {title}
          </h2>
          {p && <p className="muted">{metaLine([authorLine(p.authors), p.year, p.venue]) || p.file_name}</p>}
          {fileId && <p className="muted">Not verified yet. Check that it is the cited paper, then close this view and decide.</p>}
        </div>
        <div className="viewer-actions">
          {!fileId && <CopyButton text={p?.folder_path ?? null} disabledReason="This file is not in the collection folder yet." />}
          {src && (
            <a className="btn" href={src} target="_blank" rel="noopener noreferrer">
              <Icon name="external" />
              <span>New tab</span>
            </a>
          )}
          <button type="button" className="btn" onClick={() => ref.current?.close()} data-autofocus>
            <Icon name="close" />
            <span>Close</span>
          </button>
        </div>
      </div>
      <div className="viewer-body">
        {!fileId && detail.loading && !p && <SkeletonRows rows={3} />}
        {detail.error && <ErrorBlock error={detail.error} onRetry={detail.reload} title="Could not open this PDF." />}
        {src && <iframe className="viewer-frame" src={src} title={`PDF: ${title}`} />}
      </div>
    </dialog>
  );
}
