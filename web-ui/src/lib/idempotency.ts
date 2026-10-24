// One idempotency key per action, kept until the action succeeds, so a retry after a lost response
// repeats the same request instead of creating a second one (contract section 6).
export function keyFor(keys: Map<string, string>, fingerprint: string, make: () => string): string {
  let key = keys.get(fingerprint);
  if (!key) {
    key = make();
    keys.set(fingerprint, key);
  }
  return key;
}

export const fileFingerprint = (f: { name: string; size: number; lastModified: number }): string =>
  `${f.name}|${f.size}|${f.lastModified}`;
