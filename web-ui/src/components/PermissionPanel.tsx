import { useId, useReducer, useRef, useState } from "react";
import { api } from "../api";
import { newKey, toApiError, type ApiError } from "../api/api";
import type { Collection, PermissionChange } from "../api/types";
import { permissionRows } from "../lib/permissions";
import { twoStep } from "../lib/twostep";
import { Icon } from "./Icon";
import { ErrorBlock, IntentLines } from "./ui";

// Three rows, one Grant button each. Grant requests an intent and shows its description;
// the second click confirms. Nothing is granted without that second click.
export function PermissionPanel({ collection, onChange }: { collection: Collection; onChange: (c: Collection) => void }) {
  const [pending, dispatch] = useReducer(twoStep<PermissionChange>, null);
  const [error, setError] = useState<ApiError | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const rows = permissionRows(collection);

  const grant = async (change: PermissionChange) => {
    const key = newKey();
    setError(null);
    dispatch({ type: "start", target: change, key });
    try {
      const intent = await api.permissionIntent(collection.collection_id, {
        change,
        expected_settings_revision: collection.settings_revision,
        idempotency_key: key,
      });
      dispatch({ type: "intent", intent });
      queueMicrotask(() => confirmRef.current?.focus());
    } catch (e) {
      dispatch({ type: "reset" });
      setError(toApiError(e));
    }
  };

  const confirm = async () => {
    if (!pending?.intent) return;
    const { target: change, key, intent } = pending;
    dispatch({ type: "confirming" });
    try {
      const next = await api.permissionConfirm(
        collection.collection_id,
        { change, expected_settings_revision: collection.settings_revision, idempotency_key: key },
        intent.confirmation_token,
      );
      dispatch({ type: "reset" });
      onChange(next);
    } catch (e) {
      dispatch({ type: "reset" });
      setError(toApiError(e));
    }
  };

  return (
    <section className="perm" aria-labelledby={titleId}>
      <h2 id={titleId} className="section-title">
        Permissions
      </h2>
      <p className="muted perm-lede">RefCollector asks before it writes files or uses the network. Each change needs your confirmation.</p>
      {error && <ErrorBlock error={error} />}
      <ul className="perm-rows">
        {rows.map((r) => {
          const mine = pending?.target === r.change ? pending : null;
          const busy = pending !== null;
          const blocked = !r.granted && r.blockedBy !== null;
          return (
            <li key={r.change} className="perm-row">
              <div className="perm-main">
                <p className="perm-name">
                  {r.title}
                  <span className={r.granted ? "badge badge-positive" : "badge badge-neutral"}>
                    <Icon name={r.granted ? "check" : "lock"} size={14} />
                    <span>{r.granted ? r.grantedText : "Not granted"}</span>
                  </span>
                </p>
                <p className="muted">{r.detail}</p>
                {mine?.intent && <IntentLines intent={mine.intent} />}
                {blocked && <p className="muted perm-blocked">Grant “{r.blockedBy}” first.</p>}
              </div>
              <div className="perm-act">
                {r.granted ? null : mine?.intent ? (
                  <>
                    <button type="button" ref={confirmRef} className="btn btn-primary" onClick={confirm} disabled={mine.confirming}>
                      {mine.confirming ? "Confirming…" : "Confirm"}
                    </button>
                    <button type="button" className="btn" onClick={() => dispatch({ type: "reset" })} disabled={mine.confirming}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button type="button" className="btn" onClick={() => grant(r.change)} disabled={busy || blocked}>
                    {mine ? "Preparing…" : "Grant"}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
