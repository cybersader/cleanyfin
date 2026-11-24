// capture-v1 (delivery/schema/capture-v1.schema.json): one press while watching.
// Fuzzy on purpose. A capture is not a mark and never filters anything; a reviewer
// sets exact bounds later. Pressing Mark does not pause the TV (decision R18).

export type QuickCategory = 'language' | 'violence' | 'sex_nudity' | 'other';
export const QUICK_CATEGORIES: { id: QuickCategory; label: string }[] = [
  { id: 'language', label: 'Language' },
  { id: 'violence', label: 'Violence' },
  { id: 'sex_nudity', label: 'Sex / Nudity' },
  { id: 'other', label: 'Other' },
];

export interface CaptureV1 {
  captureVersion: 1;
  ref: string;
  capturedAt: string;
  author: string;
  surface: 'phone-remote';
  target: { serverRef: string; itemId: string; mediaSourceId: string };
  clock: { kind: 'source' };
  kind: 'point' | 'span';
  atMs: number;
  endAtMs?: number;
  window: { beforeMs: number; afterMs: number };
  observed: { sessionPositionMs: number; reportedAt: string; paused: boolean };
  category?: QuickCategory;
}

/** What the last `GET /Sessions` poll said about the playing session. */
export interface SessionReport {
  serverRef: string;
  userId: string;
  itemId: string;
  mediaSourceId: string;
  positionMs: number;
  /** When the client last reported its position (LastPlaybackCheckIn), else when we polled. */
  reportedAtMs: number;
  paused: boolean;
}

/** A position estimate at the moment of a press, with its honest uncertainty. */
export interface Estimate {
  atMs: number;
  reportAgeMs: number;
  observed: CaptureV1['observed'];
}

// Human reaction lag before the press, and a little after it.
export const REACTION_MS = 5000;
export const AFTER_MS = 1500;
const MAX_WINDOW_MS = 600_000;

const clampWindow = (ms: number): number => Math.min(MAX_WINDOW_MS, Math.max(0, Math.round(ms)));

/** Refs and ids must match the schemas' ref pattern: ^[A-Za-z0-9][A-Za-z0-9:._-]*$ */
export function toRef(text: string): string {
  const cleaned = text.replace(/[^A-Za-z0-9:._-]/g, '');
  return /^[A-Za-z0-9]/.test(cleaned) ? cleaned.slice(0, 256) : `x${cleaned}`.slice(0, 256);
}

export function newCaptureRef(nowMs: number, random: () => number = Math.random): string {
  const tail = Math.floor(random() * 36 ** 6).toString(36).padStart(6, '0');
  return `cap:${nowMs.toString(36)}${tail}`;
}

/** The session position is whatever the TV app last reported; add the time since then while it plays. */
export function estimate(report: SessionReport, nowMs: number): Estimate {
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

function base(report: SessionReport, nowMs: number, ref: string): Omit<CaptureV1, 'kind' | 'atMs' | 'window' | 'observed'> {
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

export function pointCapture(report: SessionReport, nowMs: number, ref: string, category?: QuickCategory): CaptureV1 {
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

export function spanCapture(start: Estimate, report: SessionReport, nowMs: number, ref: string,
  category?: QuickCategory): CaptureV1 {
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

// ---- IndexedDB: captures stay on this phone until exported ----

const DB_NAME = 'cleanyfin-marking';
const STORE = 'captures';

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE, { keyPath: 'ref' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Append-only: `add` refuses an existing ref rather than overwriting it. */
export async function saveCapture(capture: CaptureV1): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add(capture);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function allCaptures(): Promise<CaptureV1[]> {
  const db = await openDb();
  const items = await new Promise<CaptureV1[]>((resolve, reject) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result as CaptureV1[]);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return items.sort((a, b) => a.capturedAt.localeCompare(b.capturedAt) || a.ref.localeCompare(b.ref));
}

/** The export the reviewer's CLI reads: a JSON array of capture-v1 objects. */
export function exportJson(captures: CaptureV1[]): string {
  return JSON.stringify(captures, null, 2) + '\n';
}
