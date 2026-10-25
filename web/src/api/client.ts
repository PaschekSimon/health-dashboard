// Talking to the local API, which is served by the same FastAPI process.

export async function fetchJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

// Reads are kept for two minutes so stepping through days (and back) answers
// at once; a sync, recompute or edit clears them (refresh → clearCache).
const cache = new Map<string, { t: number; p: Promise<unknown> }>();

export function get<T>(path: string): Promise<T> {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.t < 120000) return hit.p as Promise<T>;
  const p = fetchJson<T>(path);
  cache.set(path, { t: Date.now(), p });
  p.catch(() => cache.delete(path));
  return p;
}

export const clearCache = () => cache.clear();

export const prefetch = (...paths: string[]) =>
  setTimeout(() => paths.forEach(p => get(p).catch(() => {})), 300);

export async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
