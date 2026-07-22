import './style.css';

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
  NowPlayingItem?: { Id: string; Name: string; RunTimeTicks?: number };
  PlayState?: { PositionTicks?: number };
}

const state: {
  timer: number | undefined;
  item: NowPlaying | null;
  fp: string | null;
  posMs: number;
  inMs: number | null;
  outMs: number | null;
} = { timer: undefined, item: null, fp: null, posMs: 0, inMs: null, outMs: null };

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
      state.item = null;
      return;
    }
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
