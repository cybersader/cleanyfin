import './style.css';
import {
  QUICK_CATEGORIES, allCaptures, estimate, exportJson, newCaptureRef, pointCapture, saveCapture, spanCapture,
  type CaptureV1, type Estimate, type QuickCategory, type SessionReport,
} from './capture';

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
};

const jfBaseEl = $<HTMLInputElement>('jfBase');
const jfTokenEl = $<HTMLInputElement>('jfToken');
const apiBaseEl = $<HTMLInputElement>('apiBase');
const submitterEl = $<HTMLInputElement>('submitterId');
const statusEl = $<HTMLParagraphElement>('status');
const nowPlayingEl = $<HTMLParagraphElement>('nowPlaying');
const positionEl = $<HTMLSpanElement>('position');
const inMsEl = $<HTMLSpanElement>('inMs');
const outMsEl = $<HTMLSpanElement>('outMs');
const resultEl = $<HTMLPreElement>('result');
const categoryEl = $<HTMLSelectElement>('category');
const severityEl = $<HTMLSelectElement>('severity');
const actionEl = $<HTMLSelectElement>('action');

const TICKS_PER_MS = 10000; // Jellyfin ticks are 100ns; 10000 ticks = 1 ms

interface NowPlaying {
  id: string;
  name: string;
  runtimeMs: number;
}

interface JfSession {
  UserId?: string;
  LastPlaybackCheckIn?: string;
  NowPlayingItem?: { Id: string; Name: string; RunTimeTicks?: number };
  PlayState?: { PositionTicks?: number; IsPaused?: boolean; MediaSourceId?: string };
}

const state: {
  timer: number | undefined;
  item: NowPlaying | null;
  fp: string | null;
  posMs: number;
  inMs: number | null;
  outMs: number | null;
  report: SessionReport | null;
  spanStart: Estimate | null;
  spanCategory: QuickCategory | undefined;
} = {
  timer: undefined, item: null, fp: null, posMs: 0, inMs: null, outMs: null,
  report: null, spanStart: null, spanCategory: undefined,
};

// ---- Mark while watching (capture-v1, stored on this phone) ----

const serverRefEl = $<HTMLInputElement>('serverRef');
const captureNowEl = $<HTMLParagraphElement>('captureNow');
const captureStatusEl = $<HTMLParagraphElement>('captureStatus');
const captureListEl = $<HTMLOListElement>('captureList');
const captureCountEl = $<HTMLSpanElement>('captureCount');
const markBtn = $<HTMLButtonElement>('markBtn');
const spanBtn = $<HTMLButtonElement>('spanBtn');
const catBtns = Array.from(document.querySelectorAll<HTMLButtonElement>('button.cat'));

const fmt = (ms: number): string => {
  const s = Math.floor(ms / 1000);
  const hh = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return hh > 0 ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`;
};
const label = (c?: QuickCategory): string => QUICK_CATEGORIES.find((q) => q.id === c)?.label ?? 'no category';

function setCaptureEnabled(enabled: boolean): void {
  for (const b of [markBtn, spanBtn, ...catBtns]) b.disabled = !enabled;
}

async function refreshCaptures(): Promise<void> {
  const captures = await allCaptures();
  captureCountEl.textContent = String(captures.length);
  captureListEl.replaceChildren(
    ...captures.slice(-5).reverse().map((c) => {
      const li = document.createElement('li');
      const when = c.kind === 'span' ? `${fmt(c.atMs)}–${fmt(c.endAtMs ?? c.atMs)}` : `≈${fmt(c.atMs)}`;
      li.textContent = `${when} · ${label(c.category)} · look within ${Math.round((c.window.beforeMs + c.window.afterMs) / 1000)} s`;
      return li;
    }),
  );
}

async function record(capture: CaptureV1): Promise<void> {
  try {
    await saveCapture(capture);
    captureStatusEl.textContent = `Saved ${capture.kind === 'span' ? 'span' : 'moment'} at ${fmt(capture.atMs)} (${label(capture.category)}).`;
    await refreshCaptures();
  } catch (err) {
    captureStatusEl.textContent = `Not saved: ${(err as Error).message}`;
  }
}

markBtn.addEventListener('click', () => {
  if (!state.report) return;
  const now = Date.now();
  void record(pointCapture(state.report, now, newCaptureRef(now)));
});

for (const b of catBtns) {
  b.addEventListener('click', () => {
    if (!state.report) return;
    const category = b.dataset.cat as QuickCategory;
    if (state.spanStart) {
      // While a span is open, a category names the span instead of saving a separate moment.
      state.spanCategory = category;
      captureStatusEl.textContent = `Span started at ${fmt(state.spanStart.atMs)} · ${label(category)}. Tap End when it is over.`;
      return;
    }
    const now = Date.now();
    void record(pointCapture(state.report, now, newCaptureRef(now), category));
  });
}

spanBtn.addEventListener('click', () => {
  if (!state.report) return;
  const now = Date.now();
  if (!state.spanStart) {
    state.spanStart = estimate(state.report, now);
    state.spanCategory = undefined;
    spanBtn.textContent = 'End';
    spanBtn.classList.add('open');
    captureStatusEl.textContent = `Span started at ${fmt(state.spanStart.atMs)}. Tap a category if you like, then End.`;
    return;
  }
  const capture = spanCapture(state.spanStart, state.report, now, newCaptureRef(now), state.spanCategory);
  state.spanStart = null;
  state.spanCategory = undefined;
  spanBtn.textContent = 'Start';
  spanBtn.classList.remove('open');
  void record(capture);
});

$<HTMLButtonElement>('exportBtn').addEventListener('click', async () => {
  const captures = await allCaptures();
  const blob = new Blob([exportJson(captures)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `cleanyfin-captures-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

void refreshCaptures();

function setStatus(msg: string, ok = true): void {
  statusEl.textContent = msg;
  statusEl.style.color = ok ? '#8fce9b' : '#f08a8a';
}

async function poll(): Promise<void> {
  const jf = jfBaseEl.value.replace(/\/+$/, '');
  try {
    const res = await fetch(`${jf}/Sessions`, { headers: { 'X-Emby-Token': jfTokenEl.value } });
    if (!res.ok) {
      setStatus(`Jellyfin /Sessions -> HTTP ${res.status}`, false);
      return;
    }
    const sessions = (await res.json()) as JfSession[];
    const active = sessions.find((s) => s.NowPlayingItem);
    if (!active?.NowPlayingItem) {
      nowPlayingEl.textContent = '— nothing playing on any session —';
      captureNowEl.textContent = 'Nothing is playing. Start a movie on the TV.';
      state.item = null;
      state.report = null;
      setCaptureEnabled(false);
      return;
    }
    const polledAt = Date.now();
    const checkIn = active.LastPlaybackCheckIn ? Date.parse(active.LastPlaybackCheckIn) : NaN;
    state.report = {
      serverRef: serverRefEl.value || 'home',
      userId: active.UserId ?? 'unknown',
      itemId: active.NowPlayingItem.Id,
      mediaSourceId: active.PlayState?.MediaSourceId ?? active.NowPlayingItem.Id,
      positionMs: Math.round((active.PlayState?.PositionTicks ?? 0) / TICKS_PER_MS),
      // Position is as old as the TV app's last report, not as old as this poll.
      reportedAtMs: Number.isFinite(checkIn) && checkIn <= polledAt ? checkIn : polledAt,
      paused: active.PlayState?.IsPaused ?? false,
    };
    captureNowEl.textContent = `${active.NowPlayingItem.Name} · ${fmt(state.report.positionMs)}${state.report.paused ? ' · paused' : ''}`;
    setCaptureEnabled(true);
    const npi = active.NowPlayingItem;
    state.item = {
      id: npi.Id,
      name: npi.Name,
      runtimeMs: Math.round((npi.RunTimeTicks ?? 0) / TICKS_PER_MS),
    };
    state.posMs = Math.round((active.PlayState?.PositionTicks ?? 0) / TICKS_PER_MS);

    // Resolve the release fingerprint via the plugin — the browser cannot read
    // the file's bytes, so the plugin computes the moviehash for us (R04). Falls
    // back to jf:ItemId if the plugin isn't installed.
    try {
      const fpRes = await fetch(`${jf}/Cleanyfin/Fingerprint?itemId=${encodeURIComponent(npi.Id)}`, {
        headers: { 'X-Emby-Token': jfTokenEl.value },
      });
      state.fp = fpRes.ok ? ((await fpRes.json()) as { Fingerprint: string }).Fingerprint : `jf:${npi.Id}`;
    } catch {
      state.fp = `jf:${npi.Id}`;
    }

    nowPlayingEl.textContent = `${state.item.name}  ·  fp: ${state.fp}`;
    positionEl.textContent = (state.posMs / 1000).toFixed(3);
    setStatus('connected');
  } catch (err) {
    setStatus(`connect error: ${(err as Error).message} (CORS? see README)`, false);
  }
}

$<HTMLButtonElement>('connectBtn').addEventListener('click', () => {
  if (state.timer !== undefined) {
    window.clearInterval(state.timer);
  }
  void poll();
  state.timer = window.setInterval(() => void poll(), 1000);
});

$<HTMLButtonElement>('markInBtn').addEventListener('click', () => {
  state.inMs = state.posMs;
  inMsEl.textContent = String(state.inMs);
});

$<HTMLButtonElement>('markOutBtn').addEventListener('click', () => {
  state.outMs = state.posMs;
  outMsEl.textContent = String(state.outMs);
});

$<HTMLButtonElement>('submitBtn').addEventListener('click', async () => {
  if (!state.item) {
    setStatus('no item playing', false);
    return;
  }
  if (state.inMs === null || state.outMs === null || state.outMs <= state.inMs) {
    setStatus('mark IN then OUT (out must be after in)', false);
    return;
  }
  const body = {
    fingerprint: state.fp ?? `jf:${state.item.id}`,
    durationMs: state.item.runtimeMs,
    startMs: state.inMs,
    endMs: state.outMs,
    category: categoryEl.value,
    severity: Number(severityEl.value),
    action: actionEl.value,
    submitterId: submitterEl.value || 'anonymous',
  };
  try {
    const api = apiBaseEl.value.replace(/\/+$/, '');
    const res = await fetch(`${api}/api/v1/segments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    resultEl.textContent = `HTTP ${res.status}\n${text}`;
    setStatus(res.ok ? 'segment submitted' : `submit failed (${res.status})`, res.ok);
  } catch (err) {
    resultEl.textContent = String(err);
    setStatus(`submit error: ${(err as Error).message} (CORS? see README)`, false);
  }
});
