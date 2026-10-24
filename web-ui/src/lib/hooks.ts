import { useCallback, useEffect, useEffectEvent, useState, useSyncExternalStore } from "react";
import { api } from "../api";
import { toApiError, type ApiError } from "../api/api";
import type { JobStatus } from "../api/types";
import { ERROR_RETRY_MS, nextPollDelay } from "./job";
import { formatHash, parseHash, type Route } from "./route";

// ---------- async data ----------
interface AsyncState<T> {
  req: string;
  key: string;
  data?: T;
  error?: ApiError;
}

// Runs `fn` whenever `key` or `token` changes, or `reload()` is called. Data from an earlier key is dropped;
// data from an earlier reload of the same key stays visible, so a refresh does not flash a skeleton.
export function useAsync<T>(fn: () => Promise<T>, key: string, token: string | number = 0) {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<AsyncState<T>>({ req: "", key: "" });
  const req = `${key}#${version}.${token}`;
  const run = useEffectEvent(fn);

  useEffect(() => {
    let live = true;
    run().then(
      (data) => live && setState({ req, key, data }),
      (err: unknown) => live && setState((s) => ({ req, key, data: s.key === key ? s.data : undefined, error: toApiError(err) })),
    );
    return () => {
      live = false;
    };
  }, [req, key]);

  const loading = state.req !== req;
  const data = state.key === key ? state.data : undefined;
  const setData = useCallback((update: (d: T) => T) => setState((s) => (s.data === undefined ? s : { ...s, data: update(s.data) })), []);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error: loading ? undefined : state.error, loading, reload, setData };
}

// ---------- hash route ----------
const subscribeHash = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};
const readHash = () => window.location.hash;

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, readHash);
  return parseHash(hash);
}

export function navigate(route: Partial<Route>, replace = false) {
  const hash = formatHash(route);
  if (replace) window.location.replace(hash);
  else window.location.hash = hash;
}

// ---------- job polling (contract section 6) ----------
// Polls after `next_poll_after_ms`, reports every status, stops at a terminal state or on unmount.
export function useJob(
  cid: string,
  jobId: string,
  handlers: { onStatus: (s: JobStatus) => void; onError: (e: ApiError | null) => void; onDone: (s: JobStatus) => void },
) {
  const status = useEffectEvent(handlers.onStatus);
  const error = useEffectEvent(handlers.onError);
  const done = useEffectEvent(handlers.onDone);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const s = await api.job(cid, jobId);
        if (!live) return;
        error(null);
        status(s);
        const delay = nextPollDelay(s);
        if (delay === null) done(s);
        else timer = setTimeout(poll, delay);
      } catch (err) {
        if (!live) return;
        error(toApiError(err));
        timer = setTimeout(poll, ERROR_RETRY_MS);
      }
    };
    void poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [cid, jobId]);
}

// ---------- theme ----------
export type ThemePref = "system" | "light" | "dark";
const THEME_KEY = "rc-library-theme";

export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() => {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  });
  useEffect(() => {
    const root = document.documentElement;
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  }, [pref]);
  return [pref, setPref] as const;
}

// ---------- small helpers ----------
export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
