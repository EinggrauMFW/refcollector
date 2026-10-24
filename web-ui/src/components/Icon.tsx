import type { IconName } from "../lib/status";

export type AnyIcon =
  | IconName
  | "plus"
  | "upload"
  | "search"
  | "chevron"
  | "close"
  | "copy"
  | "external"
  | "sun"
  | "moon"
  | "monitor"
  | "folder"
  | "check"
  | "lock"
  | "menu"
  | "refresh"
  | "file"
  | "more"
  | "pin"
  | "trash"
  | "edit";

// One outline icon set: 16px viewBox, 1.5px stroke, currentColor.
const PATHS: Record<AnyIcon, string> = {
  text: "M3 4h10M3 8h10M3 12h6",
  spinner: "M8 2.5a5.5 5.5 0 1 0 5.5 5.5",
  link: "M6.5 9.5l3-3M7 4.5l.8-.8a2.5 2.5 0 0 1 3.5 3.5l-.8.8M9 11.5l-.8.8a2.5 2.5 0 0 1-3.5-3.5l.8-.8",
  alert: "M8 2.5l6 10.5H2L8 2.5zM8 6.5v3M8 11.2v.1",
  "search-off": "M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM10.8 10.8L14 14M5 5l4 4M9 5L5 9",
  download: "M8 2.5v8M4.5 7.5L8 11l3.5-3.5M3 13.5h10",
  "file-off": "M4 2h5l3 3v9H4V2zM2 2l12 12",
  "file-check": "M4 2h5l3 3v9H4V2zM6 9l1.7 1.7L10.5 8",
  "x-circle": "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM6 6l4 4M10 6l-4 4",
  plus: "M8 3v10M3 8h10",
  upload: "M8 11V3M4.5 6.5L8 3l3.5 3.5M3 13.5h10",
  search: "M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM10.8 10.8L14 14",
  chevron: "M4.5 6.5L8 10l3.5-3.5",
  close: "M3.5 3.5l9 9M12.5 3.5l-9 9",
  copy: "M5.5 5.5h7v8h-7v-8zM3.5 10.5v-8h7",
  external: "M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5",
  sun: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1",
  moon: "M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z",
  monitor: "M2 3h12v8H2V3zM5.5 13.5h5M8 11v2.5",
  folder: "M2 4.5h4l1.5 1.5H14v7H2v-8.5z",
  check: "M3.5 8.5l3 3 6-7",
  lock: "M4.5 7.5h7v6h-7v-6zM6 7.5V5.5a2 2 0 0 1 4 0v2",
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  refresh: "M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3",
  file: "M4 2h5l3 3v9H4V2zM9 2v3h3",
  more: "M4 8a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm5.5 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0zm5.5 0a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z",
  pin: "M9.5 2.5l4 4-2.5 2.5 1.5 4.5-2-2-3.5 3.5-3.5-3.5 3.5-3.5-2-2 4.5 1.5 2.5-2.5zM3 13l3-3",
  trash: "M3 4.5h10M5.5 4.5V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5M6 7.5v5M10 7.5v5M4.5 4.5l.8 9.5a1 1 0 0 0 1 .9h3.4a1 1 0 0 0 1-.9l.8-9.5",
  edit: "M10.5 3.5l2 2L4.5 13.5H2.5v-2L10.5 3.5z",
};

export function Icon({ name, size = 16, spin = false }: { name: AnyIcon; size?: number; spin?: boolean }) {
  return (
    <svg
      className={spin ? "icon icon-spin" : "icon"}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

// The product mark: a citation bracket pair around a short list of lines.
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="7" className="mark-bg" />
      <path d="M12 8H9v16h3M20 8h3v16h-3" fill="none" className="mark-fg" strokeWidth="2.4" strokeLinecap="square" />
      <path d="M13.5 12.5h5M13.5 16h5M13.5 19.5h3" className="mark-fg" strokeWidth="1.8" fill="none" />
    </svg>
  );
}
