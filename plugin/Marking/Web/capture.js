import { $, fmt, jf, loginForm, marking, session, signOut } from './common.js';
import {
  QUICK_CATEGORIES, dequeue, estimate, exportJson, markRefused, newCaptureRef, outbox, pointCapture, queueCapture, spanCapture,
} from './capture-core.js';

const TICKS_PER_MS = 10000; // Jellyfin ticks are 100 ns
const STALE_MS = 60_000; // after this long without a session report, stop offering Mark

const state = { report: null, reportSeenAt: 0, spanStart: null, spanCategory: undefined, serverRef: null, sending: false, timer: undefined };
const captureNow = $('captureNow');
const captureStatus = $('captureStatus');
const markBtn = $('markBtn');
const spanBtn = $('spanBtn');
const catBtns = Array.from(document.querySelectorAll('button.cat'));
const label = (c) => QUICK_CATEGORIES.find((q) => q.id === c)?.label ?? 'no category';
const recent = [];

function setEnabled(enabled) {
  for (const b of [markBtn, spanBtn, ...catBtns]) b.disabled = !enabled;
}

function renderRecent() {
  $('captureList').replaceChildren(...recent.slice(-5).reverse().map(({ c, sent }) => {
    const li = document.createElement('li');
    const when = c.kind === 'span' ? `${fmt(c.atMs)}–${fmt(c.endAtMs ?? c.atMs)}` : `≈${fmt(c.atMs)}`;
    li.textContent = `${when} · ${label(c.category)} · ${sent}`;
    return li;
  }));
}

async function renderQueue() {
  const items = await outbox();
  const refused = items.filter((i) => i.refusal);
  $('queueCount').textContent = String(items.length);
  $('exportBtn').hidden = items.length === 0;
  $('queueStatus').textContent = items.length === 0 ? '' :
    `${items.length - refused.length} waiting to send` + (refused.length ? `, ${refused.length} refused by the server (${refused[0].refusal})` : '') + '.';
  document.body.dataset.queued = String(items.length);
}

/** Sends everything in the outbox, oldest first. A capture leaves the outbox only when the server has it. */
async function flush() {
  if (state.sending) return;
  state.sending = true;
  try {
    for (const item of await outbox()) {
      if (item.refusal) continue;
      let res;
      try {
        res = await marking('captures', { method: 'POST', body: item.capture });
      } catch {
        break; // offline: keep everything queued and try again later
      }
      if (res.status === 201 || res.status === 200) {
        await dequeue(item.capture.ref);
        const r = recent.find((x) => x.c.ref === item.capture.ref);
        if (r) r.sent = 'sent';
      } else if (res.status >= 400 && res.status < 500 && res.status !== 401 && res.status !== 408 && res.status !== 429) {
        await markRefused(item.capture.ref, res.refusal ?? `HTTP ${res.status}`);
        const r = recent.find((x) => x.c.ref === item.capture.ref);
        if (r) r.sent = `refused: ${res.refusal ?? res.status}`;
      } else {
        break;
      }
    }
  } finally {
    state.sending = false;
    renderRecent();
    await renderQueue();
  }
}

async function record(capture) {
  try {
    await queueCapture(capture);
    recent.push({ c: capture, sent: 'waiting' });
    captureStatus.textContent = `Saved ${capture.kind === 'span' ? 'span' : 'moment'} at ${fmt(capture.atMs)} (${label(capture.category)}).`;
    renderRecent();
    await flush();
  } catch (err) {
    captureStatus.textContent = `Not saved: ${err.message}`;
  }
}

const live = () => state.report && Date.now() - state.reportSeenAt < STALE_MS;

markBtn.addEventListener('click', () => {
  if (!live()) return;
  const now = Date.now();
  void record(pointCapture(state.report, now, newCaptureRef(now)));
});

for (const b of catBtns) {
  b.addEventListener('click', () => {
    if (!live()) return;
    const category = b.dataset.cat;
    if (state.spanStart) {
      // While a span is open, a category names the span instead of saving a separate moment.
      state.spanCategory = category;
      captureStatus.textContent = `Span started at ${fmt(state.spanStart.atMs)} · ${label(category)}. Tap End when it is over.`;
      return;
    }
    const now = Date.now();
    void record(pointCapture(state.report, now, newCaptureRef(now), category));
  });
}

spanBtn.addEventListener('click', () => {
  if (!live()) return;
  const now = Date.now();
  if (!state.spanStart) {
    state.spanStart = estimate(state.report, now);
    state.spanCategory = undefined;
    spanBtn.textContent = 'End';
    spanBtn.classList.add('open');
    captureStatus.textContent = `Span started at ${fmt(state.spanStart.atMs)}. Tap a category if you like, then End.`;
    return;
  }
  const capture = spanCapture(state.spanStart, state.report, now, newCaptureRef(now), state.spanCategory);
  state.spanStart = null;
  state.spanCategory = undefined;
  spanBtn.textContent = 'Start';
  spanBtn.classList.remove('open');
  void record(capture);
});

$('exportBtn').addEventListener('click', async () => {
  const items = await outbox();
  const blob = new Blob([exportJson(items.map((i) => i.capture))], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `cleanyfin-captures-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

$('signOutBtn').addEventListener('click', async () => {
  window.clearInterval(state.timer);
  await signOut();
  location.reload();
});

async function poll() {
  const me = session();
  if (!me) return;
  try {
    if (!state.serverRef) {
      const info = await jf('/System/Info/Public');
      state.serverRef = info.body?.Id ?? null;
    }
    const res = await jf('/Sessions?activeWithinSeconds=960');
    if (res.status !== 200 || !Array.isArray(res.body)) throw new Error(`HTTP ${res.status}`);
    // Only this person's own playback, and never this page's own session.
    const playing = res.body.filter((s) => s.NowPlayingItem && (s.UserId ?? '').replace(/-/g, '') === me.userId)
      .sort((a, b) => Date.parse(b.LastPlaybackCheckIn ?? 0) - Date.parse(a.LastPlaybackCheckIn ?? 0));
    const active = playing[0];
    if (!active || !state.serverRef) {
      state.report = null;
      captureNow.textContent = 'Nothing is playing on your account. Start a movie on the TV.';
      setEnabled(false);
      return;
    }
    const polledAt = Date.now();
    const checkIn = active.LastPlaybackCheckIn ? Date.parse(active.LastPlaybackCheckIn) : NaN;
    const itemId = active.NowPlayingItem.Id.replace(/-/g, '');
    state.report = {
      serverRef: state.serverRef,
      userId: me.userId,
      itemId,
      mediaSourceId: (active.PlayState?.MediaSourceId ?? itemId).replace(/-/g, ''),
      positionMs: Math.round((active.PlayState?.PositionTicks ?? 0) / TICKS_PER_MS),
      // The position is as old as the TV app's last report, not as old as this poll.
      reportedAtMs: Number.isFinite(checkIn) && checkIn <= polledAt ? checkIn : polledAt,
      paused: active.PlayState?.IsPaused ?? false,
    };
    state.reportSeenAt = polledAt;
    captureNow.textContent = `${active.NowPlayingItem.Name} · ${fmt(estimate(state.report, polledAt).atMs)}` +
      `${state.report.paused ? ' · paused' : ''} · ${active.DeviceName ?? active.Client ?? 'TV'}`;
    setEnabled(true);
    document.body.dataset.playing = itemId;
  } catch {
    // Offline or the server is restarting: keep the last report for a while so a press still lands in the outbox.
    if (!live()) setEnabled(false);
    captureNow.textContent = state.report ? `${captureNow.textContent.replace(/ · offline$/, '')} · offline` : 'Cannot reach the server.';
  }
  await flush();
}

window.addEventListener('online', () => void flush());

loginForm('Phone', (s) => {
  $('whoami').textContent = `(${s.name})`;
  void renderQueue();
  void poll();
  state.timer = window.setInterval(() => void poll(), 1000);
});
