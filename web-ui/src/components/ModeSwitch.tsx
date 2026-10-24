import { advancedHref, formatHash } from "../lib/route";

// Library | Advanced. Same look and links in both apps; the current mode carries aria-current.
export function ModeSwitch({ activeId, className }: { activeId: string | null; className?: string }) {
  return (
    <nav className={className ? `mode-switch ${className}` : "mode-switch"} aria-label="Mode">
      <a href={formatHash({ cid: activeId })} aria-current="page">
        Library
      </a>
      <a href={advancedHref(activeId)}>Advanced</a>
    </nav>
  );
}
