import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { toApiError, type ApiError } from "../api/api";
import type { Collection } from "../api/types";
import { useAsync } from "../lib/hooks";
import { Icon } from "./Icon";
import { ErrorBlock, SkeletonRows } from "./ui";

export function DeleteCollectionDialog({
  collection,
  onClose,
  onDeleted,
}: {
  collection: Collection;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  const consequences = useAsync(
    () => api.collectionConsequences(collection.collection_id),
    "collection-consequences",
    collection.collection_id,
  );
  const data = consequences.data;
  const loading = consequences.loading;
  const error = actionError ?? consequences.error;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (confirmText.trim() !== collection.name || data?.has_active_jobs) return;
    setBusy(true);
    setActionError(null);
    try {
      await api.deleteCollection(collection.collection_id);
      onDeleted();
    } catch (err) {
      setActionError(toApiError(err));
      setBusy(false);
    }
  };

  const matches = confirmText.trim() === collection.name;

  return (
    <dialog ref={ref} className="dialog" aria-labelledby="delete-col-title" onClose={onClose}>
      <div className="dialog-head">
        <h2 id="delete-col-title">Delete collection</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={onClose} disabled={busy}>
          <Icon name="close" />
        </button>
      </div>

      {loading && (
        <div className="dialog-body">
          <p className="muted">Checking collection records…</p>
          <SkeletonRows rows={2} />
        </div>
      )}

      {error && !data && (
        <div className="dialog-body">
          <ErrorBlock error={error} onRetry={consequences.reload} title="Could not inspect collection consequences." />
        </div>
      )}

      {data && (
        <form onSubmit={submit}>
          <div className="dialog-body">
            {actionError && <ErrorBlock error={actionError} />}

            {data.has_active_jobs && (
              <div className="block-error" role="alert">
                <Icon name="alert" />
                <div className="block-error-text">
                  <p>
                    <strong>Cannot delete while background jobs are active.</strong>
                  </p>
                  <p className="muted">
                    This collection has active jobs running or queued. Please wait for jobs to complete or cancel them before deleting.
                  </p>
                </div>
              </div>
            )}

            <p>
              Are you sure you want to delete <strong>{collection.name}</strong>?
            </p>

            <div className="callout callout-warning">
              <p>
                <strong>Database records will be deleted:</strong>
              </p>
              <ul className="plain-list">
                <li>• <strong>{data.paper_count}</strong> uploaded and downloaded papers</li>
                <li>• <strong>{data.citation_count}</strong> citations and review matches</li>
              </ul>
            </div>

            <div className="callout callout-neutral">
              <p>
                <strong>Disk files are preserved:</strong>
              </p>
              <p className="muted">
                {data.file_count > 0 ? (
                  <>
                    The <strong>{data.file_count}</strong> file(s) in your folder on disk will{" "}
                    <strong>NOT</strong> be deleted:
                  </>
                ) : (
                  <>Files in your collection folder will <strong>NOT</strong> be deleted:</>
                )}
              </p>
              {data.folder_path && (
                <p className="mono font-sm">{data.folder_path}</p>
              )}
            </div>

            <div className="field">
              <label htmlFor="confirm-delete-name" className="label">
                To confirm deletion, type <strong>{collection.name}</strong>:
              </label>
              <input
                id="confirm-delete-name"
                type="text"
                className="input"
                autoComplete="off"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={collection.name}
                disabled={busy}
              />
            </div>
          </div>

          <div className="dialog-foot">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-danger"
              disabled={!matches || busy || Boolean(data.has_active_jobs)}
            >
              {busy ? "Deleting…" : "Delete collection"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
