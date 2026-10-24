import { useEffect, useRef, type KeyboardEvent } from "react";
import type { Collection, PaperCard } from "../api/types";
import type { PaperJob } from "../lib/job";
import { authorLine, metaLine } from "../lib/format";
import { countCitations } from "../lib/status";
import { Icon } from "./Icon";
import { PaperBody, type PaperActions } from "./PaperBody";
import { CoverageStrip, JobBar } from "./ui";

export interface RowJob extends PaperJob {
  bar: boolean; // a collection-wide job shows one bar in the shelf header, not one per paper
}

// One shelf entry. The title is a disclosure button inside an h3; the body expands in place.
export function PaperRow({
  collection,
  card,
  expanded,
  citationId,
  job,
  refreshKey,
  titleOf,
  onToggle,
  actions,
}: {
  collection: Collection;
  card: PaperCard;
  expanded: boolean;
  citationId: string | null;
  job: RowJob | null;
  refreshKey: number;
  titleOf: (paperId: string) => string | null;
  onToggle: (paperId: string | null) => void;
  actions: PaperActions;
}) {
  const toggleRef = useRef<HTMLButtonElement>(null);
  const regionRef = useRef<HTMLDivElement>(null);
  const userOpened = useRef(false);
  const toggleId = `paper-toggle-${card.paper_id}`;
  const regionId = `paper-region-${card.paper_id}`;

  // Focus moves into a row the user just expanded.
  useEffect(() => {
    if (expanded && userOpened.current) {
      userOpened.current = false;
      regionRef.current?.focus({ preventScroll: true });
      toggleRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [expanded]);

  const toggle = () => {
    if (expanded) {
      onToggle(null);
      toggleRef.current?.focus();
    } else {
      userOpened.current = true;
      onToggle(card.paper_id);
    }
  };

  // Esc closes the open citation first, then the paper, and puts focus back on the trigger.
  const onKeyDown = (e: KeyboardEvent<HTMLLIElement>) => {
    if (e.key !== "Escape" || !expanded || e.defaultPrevented) return;
    e.preventDefault();
    e.stopPropagation();
    if (citationId) {
      actions.onCitation(null);
      document.getElementById(`cite-toggle-${citationId}`)?.focus();
    } else {
      onToggle(null);
      toggleRef.current?.focus();
    }
  };

  const uploaded = card.kind === "uploaded";
  const n = countCitations(card.status_counts);
  const meta = metaLine([authorLine(card.authors), card.year, card.venue]) || card.file_name;
  const stripLabel = `${n.downloaded} of ${n.total} citations downloaded, ${n.check} to check`;

  return (
    <li className="paper" data-expanded={expanded || undefined} data-kind={card.kind} onKeyDown={onKeyDown}>
      <h3 className="paper-head">
        <button
          ref={toggleRef}
          id={toggleId}
          type="button"
          className="paper-toggle"
          aria-expanded={expanded}
          aria-controls={regionId}
          data-paper-toggle
          onClick={toggle}
        >
          <span className="paper-main">
            <span className="paper-title serif">{card.title}</span>
            <span className="paper-meta">{meta}</span>
          </span>
          <span className="paper-side">
            <span className="side-line">
              <span className="paper-kind">
                <Icon name={uploaded ? "upload" : "download"} size={14} />
                <span>{uploaded ? "Uploaded" : "Downloaded"}</span>
              </span>
              {uploaded && n.check > 0 && (
                <span className="badge badge-warning">
                  <Icon name="alert" size={14} />
                  <span>
                    <span className="num">{n.check}</span> to check
                  </span>
                </span>
              )}
              {!card.folder_path && <span className="muted">Not in folder</span>}
            </span>
            <span className="side-line">
              {uploaded ? (
                n.total > 0 ? (
                  <>
                    <span className="paper-count">
                      <span className="num">{n.downloaded}</span> of <span className="num">{n.total}</span> downloaded
                    </span>
                    <CoverageStrip counts={card.status_counts} label={stripLabel} />
                  </>
                ) : (
                  job ? null : <span className="muted">No citations yet</span>
                )
              ) : (
                <span className="paper-count">
                  Cited by <span className="num">{card.cited_by.length}</span> {card.cited_by.length === 1 ? "paper" : "papers"}
                </span>
              )}
            </span>
          </span>
          <Icon name="chevron" />
        </button>
      </h3>
      {job?.bar && <JobBar status={job.status} error={job.error} />}
      {expanded && (
        <PaperBody
          id={regionId}
          labelledBy={toggleId}
          collection={collection}
          card={card}
          job={job}
          citationId={citationId}
          refreshKey={refreshKey}
          titleOf={titleOf}
          actions={actions}
          regionRef={regionRef}
        />
      )}
    </li>
  );
}
