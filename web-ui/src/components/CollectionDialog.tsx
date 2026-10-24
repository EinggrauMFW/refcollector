import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Collection } from "../api/types";
import { useAsync } from "../lib/hooks";
import { PermissionPanel } from "./PermissionPanel";
import { ErrorBlock } from "./ui";

// Step 1 names the collection and its folder. Step 2 shows the three permissions it needs.
// Creating a collection grants nothing (contract section 5).
export function CollectionDialog({
  onClose,
  onCreated,
  onChanged,
}: {
  onClose: () => void;
  onCreated: (c: Collection) => void;
  onChanged: (c: Collection) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const nameId = useId();
  const folderId = useId();
  const [name, setName] = useState("");
  const [folder, setFolder] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [created, setCreated] = useState<Collection | null>(null);
  const keyRef = useRef(newKey());

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);

  // The server proposes the root (contract section 8.2): <default_root>/<name>.
  const defaults = useAsync(() => api.defaults(), "defaults");
  const root = defaults.data?.default_root.replace(/[\\/]+$/, "");
  const suggestion = root && name.trim() ? `${root}/${name.trim()}` : "";
  const folderValue = touched ? folder : suggestion;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const c = await api.createCollection({ name: name.trim(), folder: folderValue.trim(), idempotency_key: keyRef.current });
      setCreated(c);
      onCreated(c);
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="dialog" aria-labelledby="cd-title" onClose={onClose}>
      <div className="dialog-head">
        <h2 id="cd-title">{created ? `Set up ${created.name}` : "New collection"}</h2>
      </div>
      {created ? (
        <div className="dialog-body">
          <PermissionPanel
            collection={created}
            onChange={(c) => {
              setCreated(c);
              onChanged(c);
            }}
          />
          <div className="dialog-foot">
            <p className="muted">You can change these later from the collection page.</p>
            <button type="button" className="btn btn-primary" onClick={() => ref.current?.close()}>
              Done
            </button>
          </div>
        </div>
      ) : (
        <form className="dialog-body" onSubmit={submit}>
          <div className="field">
            <label htmlFor={nameId}>Name</label>
            <input id={nameId} className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} autoFocus autoComplete="off" />
          </div>
          <div className="field">
            <label htmlFor={folderId}>Folder</label>
            <input
              id={folderId}
              className="input input-mono"
              value={folderValue}
              onChange={(e) => {
                setTouched(true);
                setFolder(e.target.value);
              }}
              required
              placeholder="C:/Users/you/RefCollector/Thesis"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={`${folderId}-hint`}
            />
            <p id={`${folderId}-hint`} className="hint">
              Absolute path. Uploaded and downloaded PDFs are copied here.
            </p>
          </div>
          {error && <ErrorBlock error={error} />}
          <div className="dialog-foot">
            <button type="button" className="btn" onClick={() => ref.current?.close()}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || !folderValue.trim()}>
              {busy ? "Creating…" : "Create collection"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
