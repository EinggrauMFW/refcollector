import type { LibraryApi } from "./api";
import { client } from "./client";

// VITE_API_MOCK=1 selects the in-memory implementation. The condition is a build-time constant,
// so a normal build drops the dynamic import and ships no mock data.
export const api: LibraryApi =
  import.meta.env.VITE_API_MOCK === "1" ? (await import("./mock")).mockApi : client;
