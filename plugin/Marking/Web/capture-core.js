// capture-v1 (delivery/schema/capture-v1.schema.json): one press while watching.
// Same rules as pwa/src/capture.ts (pwa/test/capture.test.ts checks both agree). A capture is not a
// mark and never filters anything; a press does not pause the TV (R18). Pure functions plus the
// phone's offline outbox.

export const QUICK_CATEGORIES = [
  { id: 'language', label: 'Language' },
  { id: 'violence', label: 'Violence' },
  { id: 'sex_nudity', label: 'Sex / Nudity' },
  { id: 'other', label: 'Other' },
];

// Human reaction lag before the press, and a little after it.
export const REACTION_MS = 5000;
export const AFTER_MS = 1500;
const MAX_WINDOW_MS = 600_000;

const clampWindow = (ms) => Math.min(MAX_WINDOW_MS, Math.max(0, Math.round(ms)));

/** Refs and ids must match the schemas' ref pattern: ^[A-Za-z0-9][A-Za-z0-9:._-]*$ */
export function toRef(text) {
  const cleaned = String(text).replace(/[^A-Za-z0-9:._-]/g, '');
  return /^[A-Za-z0-9]/.test(cleaned) ? cleaned.slice(0, 256) : `x${cleaned}`.slice(0, 256);
}

export function newCaptureRef(nowMs, random = Math.random) {
  const tail = Math.floor(random() * 36 ** 6).toString(36).padStart(6, '0');
  return `cap:${nowMs.toString(36)}${tail}`;
}

/** The session position is whatever the TV app last reported; add the time since then while it plays. */
export function estimate(report, nowMs) {
  const reportAgeMs = Math.max(0, nowMs - report.reportedAtMs);
  const atMs = Math.max(0, Math.round(report.paused ? report.positionMs : report.positionMs + reportAgeMs));
  return {
    atMs,
    reportAgeMs,
    observed: {
      sessionPositionMs: Math.max(0, Math.round(report.positionMs)),
      reportedAt: new Date(report.reportedAtMs).toISOString(),
      paused: report.paused,
    },
  };
}

function base(report, nowMs, ref) {
  return {
    captureVersion: 1,
    ref,
    capturedAt: new Date(nowMs).toISOString(),
    author: toRef(`jf:user:${report.userId}`),
    surface: 'phone-remote',
    target: {
      serverRef: toRef(report.serverRef),
      itemId: toRef(report.itemId),
      mediaSourceId: toRef(report.mediaSourceId),
    },
    clock: { kind: 'source' },
  };
}

export function pointCapture(report, nowMs, ref, category) {
  const at = estimate(report, nowMs);
  return {
    ...base(report, nowMs, ref),
    kind: 'point',
    atMs: at.atMs,
    window: { beforeMs: clampWindow(REACTION_MS + at.reportAgeMs), afterMs: AFTER_MS },
    observed: at.observed,
    ...(category ? { category } : {}),
  };
}

export function spanCapture(start, report, nowMs, ref, category) {
  const end = estimate(report, nowMs);
  return {
    ...base(report, nowMs, ref),
    kind: 'span',
    atMs: start.atMs,
    endAtMs: Math.max(start.atMs + 1, end.atMs),
    window: {
      beforeMs: clampWindow(REACTION_MS + start.reportAgeMs),
      afterMs: clampWindow(AFTER_MS + end.reportAgeMs),
    },
    observed: end.observed,
    ...(category ? { category } : {}),
  };
}

// ---- Outbox: every capture is written here first, then sent; it leaves only when the household store has it ----

const DB_NAME = 'cleanyfin-household-capture';
const STORE = 'outbox';

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'capture.ref' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, run) {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const result = run(t.objectStore(STORE));
      t.oncomplete = () => resolve(result?.result);
      t.onerror = () => reject(t.error);
    });
  } finally {
    db.close();
  }
}

/** Append-only: `add` refuses an existing ref rather than overwriting it. */
export const queueCapture = (capture) => tx('readwrite', (s) => s.add({ capture, refusal: null }));
export const markRefused = (ref, refusal) => tx('readwrite', (s) => {
  const get = s.get(ref);
  get.onsuccess = () => { if (get.result) s.put({ ...get.result, refusal }); };
});
export const dequeue = (ref) => tx('readwrite', (s) => s.delete(ref));
export async function outbox() {
  const items = await tx('readonly', (s) => s.getAll());
  return (items ?? []).sort((a, b) => a.capture.capturedAt.localeCompare(b.capture.capturedAt));
}

/** Unsent captures as the export the reviewer's CLI reads: a JSON array of capture-v1 objects. */
export function exportJson(captures) {
  return JSON.stringify(captures, null, 2) + '\n';
}
