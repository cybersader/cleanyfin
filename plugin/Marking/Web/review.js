import { $, fmt, loginForm, marking, session, signOut, streamUrl } from './common.js';

// The closed core list, in the order the 1-9 keys pick them (same enum as annotation-snapshot-v1).
const CORE = ['profanity', 'sexual_dialogue', 'sex_scene', 'nudity', 'violence', 'gore', 'disturbing', 'substance_use', 'crude'];
const HINT = { language: 'profanity', violence: 'violence' };

const video = $('video');
const state = {
  me: null, itemId: null, detail: null, mark: null, frame: null, inMs: null, outMs: null,
  fps: null, clock: null, timingRow: null, timingFault: null, chain: Promise.resolve(),
};
const status = (text, result = '') => {
  const el = $('editStatus');
  el.textContent = text;
  el.dataset.result = result;
};

// ---------------- the browser's frame timing: measured, or refinement is refused ----------------

/** Which browser this is, in the terms the timing register records. */
function browserTuple() {
  const ua = navigator.userAgent;
  const mode = /HeadlessChrome/.test(ua) ? 'headless' : 'headed';
  const brands = navigator.userAgentData?.brands ?? [];
  const chromium = brands.find((b) => b.brand === 'Chromium');
  const platform = navigator.userAgentData?.platform || (/Linux/.test(ua) ? 'Linux' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS' : 'unknown');
  if (chromium) return { engine: 'Chromium', major: Number(chromium.version), platform, mode };
  const m = ua.match(/(Firefox|Version)\/(\d+)/);
  if (m) return { engine: m[1] === 'Firefox' ? 'Firefox' : 'WebKit', major: Number(m[2]), platform, mode };
  return { engine: 'unknown', major: 0, platform, mode };
}

async function loadTiming() {
  const res = await marking('timing');
  const t = browserTuple();
  const rows = res.status === 200 && Array.isArray(res.body?.rows) ? res.body.rows : [];
  state.tuple = t;
  document.body.dataset.browser = JSON.stringify(t);
  state.timingRows = rows.filter((r) => r.result === 'pass' && r.engine === t.engine && r.major === t.major &&
    r.platform === t.platform && r.mode === t.mode);
}

function timingVerdict() {
  const t = state.tuple;
  const who = `${t.engine} ${t.major} (${t.platform}, ${t.mode})`;
  if (state.timingFault) return { ok: false, text: `Exact boundaries are off: ${state.timingFault}.`, state: 'fault' };
  const e = state.detail?.edition;
  if (!state.detail?.binding && e && (e.state === 'queued' || e.state === 'verifying')) {
    const pct = e.bytesTotal > 0 ? ` ${Math.floor((100 * e.bytesDone) / e.bytesTotal)}%` : '';
    const why = e.reason === 'FileSignalsChanged' ? 'This file changed on disk. Checking it again' : 'Checking that this is the exact file being reviewed';
    return { ok: false, text: `${why}${pct}… Marks can be placed when the check finishes.`, state: 'verifying' };
  }
  if (!state.detail?.binding) return { ok: false, text: `No verified file identity for this title (${state.detail?.bindingRefusal ?? 'unknown'}); marks cannot be placed.`, state: 'unbound' };
  if (!state.fps) return { ok: false, text: 'This title has no known frame rate; exact boundaries are not available.', state: 'unmeasured' };
  const row = state.timingRows.find((r) => r.frameRates.some((f) => Math.abs(f - state.fps) < 1e-6));
  state.timingRow = row ?? null;
  if (!row) return { ok: false, text: `Exact boundaries are not available here: frame timing has not been measured for ${who} at ${state.fps} fps. You can watch, classify and accept marks refined elsewhere.`, state: 'unmeasured' };
  return { ok: true, text: `Frame timing measured for ${who} at ${state.fps} fps (${row.framesChecked} frames, ${row.mismatches} off).`, state: 'measured' };
}

// ---------------- source clock ----------------

/** Browser media time (s) → source ms, through the binding's video track anchor. */
function sourceMs(mediaTime) {
  const c = state.clock;
  return c.anchorSourceMs + mediaTime * 1000 - (c.anchorPts * c.timeBase[0] * 1000) / c.timeBase[1];
}
function mediaTimeOf(ms) {
  const c = state.clock;
  return (ms - c.anchorSourceMs) / 1000 + (c.anchorPts * c.timeBase[0]) / c.timeBase[1];
}
const frameStart = (n) => (n * 1000) / state.fps;
const lastFrame = () => Math.max(0, Math.ceil((state.detail.binding.durationMs * state.fps) / 1000) - 1);
const frameAt = (ms) => Math.floor((ms * state.fps) / 1000 + 1e-9);

function presented(timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    let done = false;
    const id = video.requestVideoFrameCallback((_, meta) => { done = true; resolve(meta); });
    setTimeout(() => { if (!done) { video.cancelVideoFrameCallback(id); reject(new Error('no frame was presented')); } }, timeoutMs);
  });
}

/** Shows exactly frame n, and checks the browser really presented that frame. */
function showFrame(n) {
  state.chain = state.chain.then(async () => {
    if (!state.fps || !state.clock) return;
    n = Math.min(lastFrame(), Math.max(0, n));
    document.body.dataset.busy = '1';
    try {
      video.pause();
      if (state.frame === n && !video.seeking) return;
      const shown = presented();
      video.currentTime = mediaTimeOf(frameStart(n) + 500 / state.fps);
      const meta = await shown;
      const exact = (sourceMs(meta.mediaTime) * state.fps) / 1000;
      const k = Math.round(exact);
      if (Math.abs(exact - k) > 0.02 || k !== n) state.timingFault = `asked for frame ${n}, the browser presented ${exact.toFixed(3)}`;
      state.frame = k;
    } catch (err) {
      state.timingFault = err.message;
    } finally {
      document.body.dataset.busy = '0';
      render();
    }
  });
  return state.chain;
}

function nudge(ms) {
  if (state.frame == null) return;
  return showFrame(frameAt(frameStart(state.frame) + ms));
}

function goTo(text) {
  const t = text.trim();
  if (/^#\d+$/.test(t)) return showFrame(Number(t.slice(1)));
  const m = t.match(/^(?:(\d+):)?(\d+)(?:\.(\d{1,3}))?$/);
  if (!m) return;
  const ms = (Number(m[1] ?? 0) * 60 + Number(m[2])) * 1000 + Number((m[3] ?? '0').padEnd(3, '0'));
  return showFrame(frameAt(ms));
}

// ---------------- render ----------------

function canEdit() {
  const m = state.mark;
  if (!m || !state.me) return false;
  return state.me.mayReview || (state.me.mayMark && m.provenance.by === state.me.user && m.state === 'proposed');
}

function render() {
  const verdict = state.detail ? timingVerdict() : { ok: false, text: '', state: 'none' };
  const timing = $('timing');
  const again = state.detail?.reviewAgain > 0
    ? `File changed: ${state.detail.reviewAgain} mark(s) were made on another copy of this title. Review again: withdraw or reject each one marked FILE CHANGED, then mark this copy; publishing is refused until then. `
    : '';
  timing.textContent = again + verdict.text;
  timing.dataset.state = verdict.state;
  document.body.dataset.reviewAgain = String(state.detail?.reviewAgain ?? 0);
  const readout = $('frameReadout');
  if (state.frame != null && state.fps) {
    const ms = frameStart(state.frame);
    readout.textContent = `frame ${state.frame} · ${fmt(ms, true)} source`;
    readout.dataset.frame = String(state.frame);
    readout.dataset.ms = String(Math.floor(ms));
  }
  const refine = verdict.ok && canEdit();
  for (const id of ['setIn', 'setOut', 'proposeBtn']) $(id).disabled = !refine;
  $('proposeBtn').disabled = !refine || state.inMs == null || state.outMs == null || state.outMs <= state.inMs;
  const decide = Boolean(state.me?.mayReview && state.mark?.state === 'proposed');
  $('acceptBtn').disabled = !decide;
  $('rejectBtn').disabled = !decide;
  $('acceptBtn').hidden = !state.me?.mayReview;
  $('rejectBtn').hidden = !state.me?.mayReview;
  $('publishBtn').hidden = !state.me?.mayReview;
  $('publishBtn').disabled = !state.detail?.binding;
  $('inMs').textContent = state.inMs == null ? '–' : `${fmt(state.inMs, true)} (${state.inMs} ms)`;
  $('outMs').textContent = state.outMs == null ? '–' : `${fmt(state.outMs, true)} (${state.outMs} ms)`;
  const m = state.mark;
  $('markTitle').textContent = m ? `${m.ref.slice(0, 13)}… rev ${m.rev} · ${m.state}${m.boundsSource === 'capture-window' ? ' · rough' : ''}` : '(choose a mark)';
  document.body.dataset.refine = refine ? '1' : '0';
}

function li(text, buttons = [], onClick) {
  const el = document.createElement('li');
  const span = document.createElement('span');
  span.textContent = text;
  el.append(span);
  for (const [label, fn, cls] of buttons) {
    const b = document.createElement('button');
    b.textContent = label;
    b.className = `small ${cls ?? 'secondary'}`;
    b.addEventListener('click', (e) => { e.stopPropagation(); void fn(); });
    el.append(b);
  }
  if (onClick) { el.classList.add('pick'); el.addEventListener('click', onClick); }
  return el;
}

function renderLists() {
  const d = state.detail;
  $('captureCount').textContent = String(d.captures.length);
  $('captures').replaceChildren(...d.captures.map((c) => {
    const at = c.kind === 'span' ? `${fmt(c.atMs)}–${fmt(c.endAtMs)}` : `≈${fmt(c.atMs)}`;
    const buttons = [['Go', () => showFrame(frameAt(Math.max(0, c.atMs - c.window.beforeMs)))]];
    if (state.me.mayMark) buttons.push(['Draft mark', () => draft(c.ref)]);
    return li(`${at} · ${c.category ?? 'no category'}${c.clock.kind === 'filtered' ? ' · filtered clock' : ''} · ${c.author.slice(8, 16)}`, buttons);
  }));
  $('markCount').textContent = String(d.marks.length);
  $('marks').replaceChildren(...d.marks.map((m) => {
    const what = m.category ? `${m.category}${m.subcategory ? ` (${m.subcategory})` : ''} · sev ${m.severity} · ${m.action}` : 'not classified';
    const el = li(`${fmt(m.startMs, true)}–${fmt(m.endMs, true)} · ${what} · ${m.state}${m.boundsSource === 'capture-window' ? ' · rough' : ''}${m.fileChanged ? ' · FILE CHANGED, review again' : ''}`,
      [], () => select(m.ref));
    el.dataset.ref = m.ref;
    if (state.mark?.ref === m.ref) el.classList.add('selected');
    return el;
  }));
}

// ---------------- actions ----------------

async function loadItem(itemId, keepMark) {
  state.itemId = itemId;
  const res = await marking(`items/${itemId}`);
  if (res.status !== 200) { status(`Cannot open this title (${res.refusal ?? res.status}).`, 'refused'); return; }
  const first = !state.detail || state.detail.item.itemId !== itemId;
  state.detail = res.body;
  state.fps = res.body.item.frameRate;
  state.clock = res.body.binding?.tracks.find((t) => t.kind === 'video') ?? null;
  if (first) {
    document.body.dataset.ready = '0';
    state.frame = null;
    video.src = streamUrl(itemId);
    await new Promise((resolve) => video.addEventListener('loadeddata', resolve, { once: true }));
    if (state.clock && state.fps) await showFrame(0);
    document.body.dataset.ready = '1';
  }
  else if (state.clock && state.fps && state.frame == null) await showFrame(0);
  state.mark = keepMark ? state.detail.marks.find((m) => m.ref === keepMark) ?? null : null;
  renderLists();
  render();
  // The file check runs in the background on the server; look again until it settles.
  if (['queued', 'verifying'].includes(res.body.edition?.state)) {
    clearTimeout(state.editionPoll);
    state.editionPoll = setTimeout(() => { if (state.itemId === itemId) void loadItem(itemId, state.mark?.ref); }, 2000);
  }
}

function select(ref) {
  const m = state.detail.marks.find((x) => x.ref === ref);
  state.mark = m ?? null;
  if (!m) return;
  const capture = state.detail.captures.find((c) => m.provenance.from.includes(c.ref));
  $('category').value = m.category ?? HINT[capture?.category] ?? (CORE.includes(capture?.category) ? capture.category : '');
  $('subcategory').value = m.subcategory ?? '';
  $('severity').value = String(m.severity ?? 2);
  $('action').value = m.action ?? 'skip';
  state.inMs = m.boundsSource === 'reviewer' ? m.startMs : null;
  state.outMs = m.boundsSource === 'reviewer' ? m.endMs : null;
  renderLists();
  render();
  void showFrame(frameAt(m.startMs));
}

async function draft(captureRef) {
  const res = await marking('marks', { method: 'POST', body: { captureRef } });
  if (res.status !== 201) { status(`Not drafted: ${res.refusal ?? res.status}.`, 'refused'); return; }
  status('Draft made from the capture. Step to the first and last affected frame.', 'drafted');
  await loadItem(state.itemId, res.body.ref);
  select(res.body.ref);
}

function setIn() {
  if ($('setIn').disabled || state.frame == null) return;
  state.inMs = Math.floor(frameStart(state.frame));
  render();
}

function setOut() {
  if ($('setOut').disabled || state.frame == null) return;
  // The end is exclusive: the start of the frame after the last affected one.
  state.outMs = Math.ceil(frameStart(state.frame + 1));
  render();
}

async function propose() {
  if ($('proposeBtn').disabled) return;
  const m = state.mark;
  const body = {
    rev: m.rev, startMs: state.inMs, endMs: state.outMs, severity: Number($('severity').value), action: $('action').value,
  };
  if ($('category').value) body.category = $('category').value;
  if ($('subcategory').value.trim()) body.subcategory = $('subcategory').value.trim();
  const res = await marking(`marks/${encodeURIComponent(m.ref)}`, { method: 'PUT', body });
  if (res.status !== 200) { status(`Not saved: ${res.refusal ?? res.status}.`, 'refused'); return; }
  status(`Proposed rev ${res.body.rev}: ${fmt(res.body.startMs, true)}–${fmt(res.body.endMs, true)}.`, 'proposed');
  await loadItem(state.itemId, m.ref);
}

async function decide(kind) {
  const m = state.mark;
  if (!m || $(`${kind}Btn`).disabled) return;
  const res = await marking(`marks/${encodeURIComponent(m.ref)}/${kind}`, { method: 'POST', body: { rev: m.rev } });
  if (res.status !== 200) { status(`Not ${kind}ed: ${res.refusal ?? res.status}.`, 'refused'); return; }
  status(`${kind === 'accept' ? 'Accepted' : 'Rejected'} rev ${m.rev} (audit #${res.body.seq}).`, `${kind}ed`);
  await loadItem(state.itemId, m.ref);
}

async function publish() {
  const res = await marking(`items/${state.itemId}/publish`, { method: 'POST', body: {} });
  if (res.status !== 200) { status(`Not published: ${res.refusal ?? res.status}.`, 'refused'); return; }
  status(`Published ${res.body.annotations} accepted mark(s) as ${res.body.snapshotRev}.`, 'published');
}

// ---------------- wiring ----------------

$('category').replaceChildren(new Option('(choose)', ''), ...CORE.map((c, i) => new Option(`${i + 1} · ${c.replace('_', ' ')}`, c)));
$('backFrame').addEventListener('click', () => showFrame((state.frame ?? 0) - 1));
$('fwdFrame').addEventListener('click', () => showFrame((state.frame ?? -1) + 1));
$('back100').addEventListener('click', () => nudge(-100));
$('fwd100').addEventListener('click', () => nudge(100));
$('playBtn').addEventListener('click', () => (video.paused ? video.play() : video.pause()));
$('goForm').addEventListener('submit', (e) => { e.preventDefault(); void goTo($('goTo').value); $('goTo').blur(); });
$('setIn').addEventListener('click', setIn);
$('setOut').addEventListener('click', setOut);
$('proposeBtn').addEventListener('click', () => void propose());
$('acceptBtn').addEventListener('click', () => void decide('accept'));
$('rejectBtn').addEventListener('click', () => void decide('reject'));
$('publishBtn').addEventListener('click', () => void publish());
$('itemSelect').addEventListener('change', (e) => { if (e.target.value) void loadItem(e.target.value); });
$('signOutBtn').addEventListener('click', async () => { await signOut(); location.reload(); });
video.addEventListener('play', () => { $('playBtn').textContent = 'Pause'; });
video.addEventListener('pause', () => { $('playBtn').textContent = 'Play'; });
// While playing, follow the presented frame; stepping resumes from wherever it stopped.
function follow(_, meta) {
  if (!video.paused && state.fps && state.clock) { state.frame = Math.round((sourceMs(meta.mediaTime) * state.fps) / 1000); render(); }
  video.requestVideoFrameCallback(follow);
}
video.requestVideoFrameCallback(follow);

document.addEventListener('keydown', (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  const acts = {
    ',': () => showFrame((state.frame ?? 0) - 1), '.': () => showFrame((state.frame ?? -1) + 1),
    '[': () => nudge(-100), ']': () => nudge(100), i: setIn, I: setIn, o: setOut, O: setOut,
    Enter: () => propose(), a: () => decide('accept'), A: () => decide('accept'),
    s: () => { $('severity').value = String((Number($('severity').value) + 1) % 4); },
    S: () => { $('severity').value = String((Number($('severity').value) + 1) % 4); },
    ' ': () => (video.paused ? video.play() : video.pause()),
  };
  if (/^[1-9]$/.test(k)) { $('category').value = CORE[Number(k) - 1]; e.preventDefault(); return; }
  if (acts[k]) { e.preventDefault(); void acts[k](); }
});

async function showTestPlayback() {
  const reply = await marking('test-playback');
  if (reply.status !== 200) { $('testStatus').textContent = 'Could not read test playback policy.'; return; }
  const { enabled, canChange, reports } = reply.body;
  $('testPanel').hidden = !(canChange || enabled);
  $('testControl').hidden = !canChange;
  $('testPlayback').checked = enabled;
  $('testStatus').textContent = !enabled ? 'Off.' : reports.length ? 'On for the measured layouts below only.' : 'On. No measured layouts are available.';
  $('testReports').replaceChildren(...reports.map(r => {
    const li = document.createElement('li');
    li.textContent = `${r.layout} · ${r.player}. Silence: ${r.silence}; pop: ${r.pop}; picture: ${r.picture}; sync: ${r.sync}; kept sound lost: ${r.loss}.`;
    return li;
  }));
}
$('testPlayback').addEventListener('change', async e => {
  const input = e.currentTarget;
  input.disabled = true;
  try {
    const r = await marking('test-playback/' + (input.checked ? 'on' : 'off'), { method: 'POST' });
    await showTestPlayback();
    if (r.status !== 204) $('testStatus').textContent = 'Policy change refused.';
  } finally { input.disabled = false; }
});

loginForm('Browser', async () => {
  const me = await marking('me');
  if (me.status !== 200) { status(`Cannot use marking (${me.refusal ?? me.status}).`, 'refused'); return; }
  state.me = me.body;
  $('who').textContent = `${session()?.name ?? ''} · ${me.body.roles.join(', ') || 'no role'}`;
  await showTestPlayback();
  await loadTiming();
  const items = await marking('items');
  for (const it of items.body?.items ?? []) $('itemSelect').append(new Option(`${it.name} (${it.captures} captures, ${it.marks} marks)`, it.itemId));
  const wanted = new URLSearchParams(location.search).get('item');
  if (wanted) { $('itemSelect').value = wanted; await loadItem(wanted); }
  render();
});
