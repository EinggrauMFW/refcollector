import { useEffect, useState } from "react";
import { api } from "./api";
import type { Collection } from "./api/types";
import { CollectionDialog } from "./components/CollectionDialog";
import { Icon } from "./components/Icon";
import { Shelf } from "./components/Shelf";
import { ModeSwitch } from "./components/ModeSwitch";
import { Sidebar } from "./components/Sidebar";
import { ErrorBlock, SkeletonRows } from "./components/ui";
import { navigate, useAsync, useRoute, useTheme } from "./lib/hooks";

export function App() {
  const route = useRoute();
  const [theme, setTheme] = useTheme();
  const [stale, setStale] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(false);
  const cols = useAsync(() => api.listCollections(), "collections", stale);

  const list = cols.data;
  const active = list?.find((c) => c.collection_id === route.cid) ?? null;
  const replace = (c: Collection) => cols.setData((all) => all.map((x) => (x.collection_id === c.collection_id ? c : x)));

  // "#/" opens the first collection.
  const firstId = list?.[0]?.collection_id;
  useEffect(() => {
    if (!route.cid && firstId) navigate({ cid: firstId }, true);
  }, [route.cid, firstId]);

  let content;
  if (cols.error && !list) {
    content = (
      <div className="page-pad">
        <ErrorBlock error={cols.error} onRetry={cols.reload} title="Could not load your collections." />
      </div>
    );
  } else if (!list) {
    content = (
      <div className="page-pad">
        <header className="title-row">
          <span className="skel skel-title" />
        </header>
        <SkeletonRows rows={4} />
      </div>
    );
  } else if (list.length === 0) {
    content = (
      <div className="page-pad">
        <header className="title-row">
          <div className="title-text">
            <h1>Your library</h1>
            <p className="subline">Collections group the papers you read and the papers they cite.</p>
          </div>
        </header>
        <div className="empty empty-drop">
          <p className="empty-title">No collections yet.</p>
          <p className="muted">A collection is a named shelf with a folder on disk. Create one, then upload a PDF to list its citations.</p>
          <button type="button" className="btn btn-primary" onClick={() => setDialog(true)}>
            <Icon name="plus" />
            <span>Create your first collection</span>
          </button>
        </div>
      </div>
    );
  } else if (!active) {
    content = route.cid ? (
      <div className="page-pad">
        <div className="empty">
          <p className="empty-title">This collection does not exist.</p>
          <p className="muted">Pick one from the list on the left.</p>
        </div>
      </div>
    ) : null;
  } else {
    content = (
      <Shelf
        key={active.collection_id}
        collection={active}
        route={route}
        onCollectionChange={replace}
        onCollectionsStale={() => setStale((n) => n + 1)}
      />
    );
  }

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to the shelf
      </a>
      <header className="topbar">
        <button type="button" className="icon-btn" aria-expanded={menuOpen} aria-controls="sidebar" aria-label="Collections menu" onClick={() => setMenuOpen((o) => !o)}>
          <Icon name={menuOpen ? "close" : "menu"} />
        </button>
        <span className="topbar-title">{active?.name ?? "Library"}</span>
        <ModeSwitch activeId={active?.collection_id ?? null} />
      </header>
      <Sidebar
        collections={list}
        activeId={active?.collection_id ?? null}
        open={menuOpen}
        theme={theme}
        onTheme={setTheme}
        onNew={() => {
          setMenuOpen(false);
          setDialog(true);
        }}
        onNavigate={() => setMenuOpen(false)}
        onRenamed={(updated) => {
          replace(updated);
        }}
        onDeleted={(cid) => {
          cols.setData((all) => all.filter((x) => x.collection_id !== cid));
          if (active?.collection_id === cid) {
            const next = list?.find((x) => x.collection_id !== cid);
            if (next) navigate({ cid: next.collection_id });
            else navigate({});
          }
        }}
      />
      <main id="main" className="canvas" tabIndex={-1}>
        {content}
      </main>
      {dialog && (
        <CollectionDialog
          onClose={() => setDialog(false)}
          onCreated={(c) => {
            cols.setData((all) => [...all, c]);
            setStale((n) => n + 1);
            navigate({ cid: c.collection_id });
          }}
          onChanged={replace}
        />
      )}
    </div>
  );
}
