import type { Collection, Permissions, PermissionChange } from "../api/types";

export interface PermissionRow {
  change: PermissionChange;
  title: string;
  detail: string;
  granted: boolean;
  grantedText: string;
  blockedBy: string | null; // earlier row that must be granted first
}

// Three rows in the order the server expects them (contract section 6).
export function permissionRows(c: Pick<Collection, "folder" | "permissions">): PermissionRow[] {
  const p = c.permissions;
  const rows: Omit<PermissionRow, "blockedBy">[] = [
    {
      change: "add_allowed_folder",
      title: "Folder access",
      detail: `Save PDFs to ${c.folder ?? "the collection folder"}.`,
      granted: p.folder_granted,
      grantedText: "Allowed",
    },
    {
      change: "set_project_network",
      title: "Network lookups",
      detail: `Look up papers and fetch PDFs online. Mode now: ${p.network_mode}.`,
      granted: p.network_mode === "retrieval",
      grantedText: "Retrieval on",
    },
    {
      change: "set_project_downloads",
      title: "PDF downloads",
      detail: "Download PDFs of cited papers into the folder.",
      granted: p.downloads_enabled,
      grantedText: p.downloads_source === "global" ? "Allowed for all collections" : "Allowed",
    },
  ];
  return rows.map((r, i) => {
    const earlier = rows.slice(0, i).find((x) => !x.granted);
    return { ...r, blockedBy: earlier ? earlier.title : null };
  });
}

// Why "Download all" is unavailable, or null when the collection may download.
export function downloadBlockReason(p: Permissions): string | null {
  if (p.can_download) return null;
  if (!p.folder_granted) return "Folder access is not granted for this collection.";
  if (p.network_mode !== "retrieval") return "Network retrieval is off for this collection.";
  if (!p.downloads_enabled) return "PDF downloads are not allowed for this collection.";
  return "Downloads are not allowed for this collection.";
}
