import type { Intent } from "../api/types";

// Two-step confirmation (permissions, Check panel): request an intent, show it, then confirm.
// Only one change is in flight at a time. Nothing is confirmed without the second step.
export interface TwoStep<T> {
  target: T;
  key: string; // idempotency key shared by intent and confirm
  intent: Intent | null; // null while the intent is requested
  confirming: boolean;
}

export type TwoStepEvent<T> =
  | { type: "start"; target: T; key: string }
  | { type: "intent"; intent: Intent }
  | { type: "confirming" }
  | { type: "reset" };

export function twoStep<T>(s: TwoStep<T> | null, e: TwoStepEvent<T>): TwoStep<T> | null {
  switch (e.type) {
    case "start":
      return s === null ? { target: e.target, key: e.key, intent: null, confirming: false } : s;
    case "intent":
      return s && !s.intent ? { ...s, intent: e.intent } : s;
    case "confirming":
      return s?.intent && !s.confirming ? { ...s, confirming: true } : s;
    case "reset":
      return null;
  }
}
