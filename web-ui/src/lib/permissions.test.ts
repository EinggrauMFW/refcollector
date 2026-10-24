import { describe, expect, it } from "vitest";
import { downloadBlockReason, permissionRows } from "./permissions";

const perms = (over = {}) => ({
  folder_granted: false,
  network_mode: "offline",
  downloads_enabled: false,
  downloads_source: "none" as const,
  can_download: false,
  ...over,
});

describe("permissions", () => {
  it("orders the three rows and blocks each on the first ungranted one before it", () => {
    const rows = permissionRows({ folder: "C:/x", permissions: perms({ network_mode: "retrieval" }) });
    expect(rows.map((r) => r.change)).toEqual(["add_allowed_folder", "set_project_network", "set_project_downloads"]);
    expect(rows.map((r) => r.granted)).toEqual([false, true, false]);
    expect(rows.map((r) => r.blockedBy)).toEqual([null, "Folder access", "Folder access"]);
  });

  it("explains why downloads are unavailable, first failing requirement first", () => {
    expect(downloadBlockReason(perms())).toMatch(/Folder access/);
    expect(downloadBlockReason(perms({ folder_granted: true }))).toMatch(/Network retrieval/);
    expect(downloadBlockReason(perms({ folder_granted: true, network_mode: "retrieval" }))).toMatch(/PDF downloads/);
    expect(downloadBlockReason(perms({ can_download: true }))).toBeNull();
  });
});
