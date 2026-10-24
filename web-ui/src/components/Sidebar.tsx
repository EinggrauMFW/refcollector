import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { toApiError } from "../api/api";
import type { Collection } from "../api/types";
import type { ThemePref } from "../lib/hooks";
import { formatHash } from "../lib/route";
import { DeleteCollectionDialog } from "./DeleteCollectionDialog";
import { Icon, Mark, type AnyIcon } from "./Icon";
import { ModeSwitch } from "./ModeSwitch";

const THEMES: Array<{ id: ThemePref; label: string; icon: AnyIcon }> = [
  { id: "light", label: "Light", icon: "sun" },
  { id: "dark", label: "Dark", icon: "moon" },
  { id: "system", label: "System", icon: "monitor" },
];

const PIN_STORAGE_KEY = "rc-library-pinned-collections";

function getStoredPinned(): string[] {
  try {
    const raw = localStorage.getItem(PIN_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function storePinned(ids: string[]): void {
  try {
    localStorage.setItem(PIN_STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // Ignore storage errors
  }
}

export function Sidebar({
  collections,
  activeId,
  open,
  theme,
  onTheme,
  onNew,
  onNavigate,
  onRenamed,
  onDeleted,
}: {
  collections: Collection[] | undefined;
  activeId: string | null;
  open: boolean;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onNew: () => void;
  onNavigate: () => void;
  onRenamed?: (c: Collection) => void;
  onDeleted?: (cid: string) => void;
}) {
  const [pinned, setPinned] = useState<string[]>(getStoredPinned);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [deletingCol, setDeletingCol] = useState<Collection | null>(null);

  const renameInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close open collection menu on outside click or escape
  useEffect(() => {
    if (!menuOpenId) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenId(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpenId(null);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuOpenId]);

  // Focus input when entering rename mode
  useEffect(() => {
    if (renamingId) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renamingId]);

  const togglePin = (cid: string) => {
    setPinned((curr) => {
      const next = curr.includes(cid) ? curr.filter((id) => id !== cid) : [...curr, cid];
      storePinned(next);
      return next;
    });
    setMenuOpenId(null);
  };

  const startRename = (c: Collection) => {
    setMenuOpenId(null);
    setRenamingId(c.collection_id);
    setRenameVal(c.name);
    setRenameError(null);
  };

  const cancelRename = () => {
    setRenamingId(null);
    setRenameVal("");
    setRenameError(null);
  };

  const submitRename = async (e: FormEvent, cid: string) => {
    e.preventDefault();
    const clean = renameVal.trim();
    if (!clean) {
      setRenameError("Collection name cannot be empty.");
      return;
    }
    setRenameBusy(true);
    setRenameError(null);
    try {
      const updated = await api.renameCollection(cid, clean);
      onRenamed?.(updated);
      setRenamingId(null);
    } catch (err) {
      setRenameError(toApiError(err).message);
    } finally {
      setRenameBusy(false);
    }
  };

  const startDelete = (c: Collection) => {
    setMenuOpenId(null);
    setDeletingCol(c);
  };

  // Sort pinned to top with stable order
  const pinnedSet = new Set(pinned);
  const sorted = collections
    ? [...collections].sort((a, b) => {
        const aPin = pinnedSet.has(a.collection_id);
        const bPin = pinnedSet.has(b.collection_id);
        if (aPin && !bPin) return -1;
        if (!aPin && bPin) return 1;
        return 0;
      })
    : undefined;

  return (
    <>
      <aside id="sidebar" className="sidebar" data-open={open || undefined} aria-label="Library">
        <div className="brand">
          <Mark />
          <div>
            <p className="brand-name">RefCollector</p>
            <p className="brand-sub">Library</p>
          </div>
        </div>
        <ModeSwitch className="mode-switch-side" activeId={activeId} />
        <nav aria-labelledby="nav-collections">
          <p id="nav-collections" className="nav-label">
            Collections
          </p>
          <ul className="nav-list">
            {sorted?.map((c) => {
              const isPinned = pinnedSet.has(c.collection_id);
              const isRenaming = renamingId === c.collection_id;
              const isMenuOpen = menuOpenId === c.collection_id;

              if (isRenaming) {
                return (
                  <li key={c.collection_id} className="nav-row nav-rename-row">
                    <form className="nav-rename-form" onSubmit={(e) => submitRename(e, c.collection_id)}>
                      <input
                        ref={renameInputRef}
                        type="text"
                        className="input input-sm nav-rename-input"
                        value={renameVal}
                        onChange={(e) => setRenameVal(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") cancelRename();
                        }}
                        disabled={renameBusy}
                        aria-label={`Rename ${c.name}`}
                      />
                      <div className="nav-rename-btns">
                        <button type="submit" className="btn btn-sm btn-primary" disabled={renameBusy || !renameVal.trim()}>
                          Save
                        </button>
                        <button type="button" className="btn btn-sm" onClick={cancelRename} disabled={renameBusy}>
                          Cancel
                        </button>
                      </div>
                      {renameError && <p className="nav-rename-error">{renameError}</p>}
                    </form>
                  </li>
                );
              }

              return (
                <li key={c.collection_id} className="nav-row" data-active={c.collection_id === activeId || undefined}>
                  <a
                    className="nav-item"
                    href={formatHash({ cid: c.collection_id })}
                    aria-current={c.collection_id === activeId ? "page" : undefined}
                    onClick={onNavigate}
                  >
                    <Icon name="folder" />
                    <span className="nav-name">{c.name}</span>
                    {isPinned && (
                      <span className="nav-pin-mark" title="Pinned to top" aria-label="Pinned to top">
                        <Icon name="pin" size={12} />
                      </span>
                    )}
                    {c.check_count > 0 && (
                      <span className="nav-count" title={`${c.check_count} citations to check`}>
                        <span className="num">{c.check_count}</span> to check
                      </span>
                    )}
                  </a>
                  <div className="nav-menu-wrap" ref={isMenuOpen ? menuRef : undefined}>
                    <button
                      type="button"
                      className="icon-btn nav-menu-btn"
                      aria-label={`Options for ${c.name}`}
                      aria-haspopup="menu"
                      aria-expanded={isMenuOpen}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setMenuOpenId(isMenuOpen ? null : c.collection_id);
                      }}
                    >
                      <Icon name="more" size={14} />
                    </button>
                    {isMenuOpen && (
                      <div className="menu-dropdown" role="menu" aria-label={`Options for ${c.name}`}>
                        <button
                          type="button"
                          className="menu-item"
                          role="menuitem"
                          onClick={() => startRename(c)}
                        >
                          <Icon name="edit" size={14} />
                          <span>Rename</span>
                        </button>
                        <button
                          type="button"
                          className="menu-item"
                          role="menuitem"
                          onClick={() => togglePin(c.collection_id)}
                        >
                          <Icon name="pin" size={14} />
                          <span>{isPinned ? "Unpin" : "Pin to top"}</span>
                        </button>
                        <div className="menu-divider" role="separator" />
                        <button
                          type="button"
                          className="menu-item menu-item-danger"
                          role="menuitem"
                          onClick={() => startDelete(c)}
                        >
                          <Icon name="trash" size={14} />
                          <span>Delete</span>
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <button type="button" className="nav-item nav-new" onClick={onNew}>
            <Icon name="plus" />
            <span>New collection</span>
          </button>
        </nav>
        <div className="side-foot">
          <div className="seg-control" role="radiogroup" aria-label="Theme">
            {THEMES.map((t) => (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={theme === t.id}
                aria-label={t.label}
                title={t.label}
                className="seg-btn"
                onClick={() => onTheme(t.id)}
              >
                <Icon name={t.icon} />
              </button>
            ))}
          </div>
        </div>
      </aside>

      {deletingCol && (
        <DeleteCollectionDialog
          collection={deletingCol}
          onClose={() => setDeletingCol(null)}
          onDeleted={() => {
            const cid = deletingCol.collection_id;
            setDeletingCol(null);
            onDeleted?.(cid);
          }}
        />
      )}
    </>
  );
}
