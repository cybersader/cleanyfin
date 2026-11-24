#!/usr/bin/env node
// Drives the plugin's capture and review pages in a fresh, private headless Chromium against the
// disposable lab only. No npm packages: Chrome DevTools Protocol over --remote-debugging-pipe, so
// the browser opens no debugging port. Its only route to the lab is the loopback origin it is given
// (check-plugin.py forwards that into the network-less lab container); every other host is unresolvable.
//
//   node tools/playback-lab/marking-browser.mjs SCENARIO < config.json   (one JSON result on stdout)
//
// Scenarios: capture (phone page, live + offline queue), measure (review-page frame timing against the
// barcode fixture), refine (step to exact boundaries, propose, accept, publish). Secrets arrive on stdin,
// never in argv, and are not printed.
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

const scenario = process.argv[2];
const config = JSON.parse(await new Promise((resolve) => {
  let text = '';
  process.stdin.on('data', (d) => { text += d; });
  process.stdin.on('end', () => resolve(text));
}));

function chromium() {
  if (process.env.CLEANYFIN_CHROMIUM) return process.env.CLEANYFIN_CHROMIUM;
  const root = join(homedir(), '.cache/ms-playwright');
  const builds = existsSync(root) ? readdirSync(root).filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split('-')[1]) - Number(a.split('-')[1])) : [];
  for (const b of builds) {
    const path = join(root, b, 'chrome-linux64/chrome');
    if (existsSync(path)) return path;
  }
  throw new Error('No Chromium: set CLEANYFIN_CHROMIUM');
}

class Browser {
  constructor() {
    this.profile = mkdtempSync(join(config.scratch ?? tmpdir(), 'chromium-'));
    const origin = new URL(config.origin);
    this.proc = spawn(chromium(), [
      '--headless=new', '--remote-debugging-pipe', `--user-data-dir=${this.profile}`, '--no-first-run',
      '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-sync',
      '--disable-features=Translate,MediaRouter,OptimizationHints', '--no-proxy-server', '--mute-audio',
      '--autoplay-policy=no-user-gesture-required', `--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE ${origin.hostname}`,
      'about:blank',
    ], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    let buffer = Buffer.alloc(0);
    this.proc.stdio[4].on('data', (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let end;
      while ((end = buffer.indexOf(0)) >= 0) {
        const msg = JSON.parse(buffer.subarray(0, end).toString());
        buffer = buffer.subarray(end + 1);
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? reject(new Error(`${msg.error.message}`)) : resolve(msg.result);
        } else {
          for (const l of this.listeners) l(msg);
        }
      }
    });
  }

  send(method, params = {}, sessionId = this.session) {
    const id = ++this.id;
    this.proc.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  async open(viewport) {
    const { targetId } = await this.send('Target.createTarget', { url: 'about:blank' }, null);
    this.session = (await this.send('Target.attachToTarget', { targetId, flatten: true }, null)).sessionId;
    for (const domain of ['Page', 'Runtime', 'Network']) await this.send(`${domain}.enable`);
    await this.send('Emulation.setDeviceMetricsOverride', viewport);
    this.version = (await this.send('Browser.getVersion', {}, null)).product;
  }

  async goto(path) {
    const loaded = new Promise((resolve) => {
      const l = (m) => { if (m.method === 'Page.loadEventFired' && m.sessionId === this.session) { this.listeners.splice(this.listeners.indexOf(l), 1); resolve(); } };
      this.listeners.push(l);
    });
    await this.send('Page.navigate', { url: config.origin + path });
    await loaded;
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(`page: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    return r.result.value;
  }

  async waitFor(expression, what, timeoutMs = 15000) {
    const until = Date.now() + timeoutMs;
    while (Date.now() < until) {
      if (await this.eval(expression)) return;
      await sleep(50);
    }
    throw new Error(`timed out waiting for ${what}`);
  }

  async type(selector, text) {
    await this.eval(`document.querySelector(${JSON.stringify(selector)}).focus()`);
    await this.send('Input.insertText', { text });
  }

  async key(key) {
    const codes = { ',': [188, 'Comma'], '.': [190, 'Period'], '[': [219, 'BracketLeft'], ']': [221, 'BracketRight'], Enter: [13, 'Enter'] };
    const [vk, code] = codes[key] ?? [key.toUpperCase().charCodeAt(0), /\d/.test(key) ? `Digit${key}` : `Key${key.toUpperCase()}`];
    const text = key === 'Enter' ? '\r' : key;
    await this.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, text, windowsVirtualKeyCode: vk });
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: vk });
  }

  click(selector) {
    return this.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el || el.disabled) return false; el.click(); return true; })()`);
  }

  async screenshot(path) {
    const { data } = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(path, Buffer.from(data, 'base64'));
  }

  async close() {
    const exited = new Promise((resolve) => this.proc.once('exit', resolve));
    this.proc.kill('SIGKILL');
    await exited;
    rmSync(this.profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function signIn(b, user) {
  await b.waitFor(`!document.getElementById('login').hidden`, 'sign-in form');
  await b.type('#username', user.name);
  await b.type('#password', user.password);
  await b.eval(`document.getElementById('login').requestSubmit()`);
}

// ---------------- phone capture page ----------------

async function capture(b) {
  await b.open({ width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await b.goto('/Cleanyfin/marking/capture');
  await signIn(b, config.user);
  await b.waitFor(`document.body.dataset.playing === ${JSON.stringify(config.itemId)} && !document.getElementById('markBtn').disabled`, 'the TV session to show as playing');
  const result = { browser: b.version };
  // A span named Violence, then a one-tap Mark: both should reach the server at once.
  await b.click('#spanBtn');
  await b.click('button.cat[data-cat="violence"]');
  await sleep(1200);
  await b.click('#spanBtn');
  await b.waitFor(`document.body.dataset.queued === '0' && document.querySelectorAll('#captureList li').length === 1`, 'span sent');
  await b.click('#markBtn');
  await b.waitFor(`document.body.dataset.queued === '0' && document.querySelectorAll('#captureList li').length === 2`, 'mark sent');
  result.sentLive = await b.eval(`[...document.querySelectorAll('#captureList li')].map((li) => li.textContent)`);
  // Offline: the press must stay in the outbox, then go once the network is back.
  await b.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await b.click('button.cat[data-cat="language"]');
  await b.waitFor(`document.body.dataset.queued === '1'`, 'offline capture queued');
  result.queuedOffline = await b.eval(`document.getElementById('queueStatus').textContent`);
  await b.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await b.eval(`window.dispatchEvent(new Event('online'))`);
  await b.waitFor(`document.body.dataset.queued === '0' && document.querySelectorAll('#captureList li').length === 3`, 'queued capture sent after reconnect');
  result.sentAfterReconnect = await b.eval(`[...document.querySelectorAll('#captureList li')].map((li) => li.textContent)`);
  if (config.screenshot) await b.screenshot(config.screenshot);
  return result;
}

// ---------------- review page: frame timing measurement ----------------

// The hard fixture's barcode (tools/playback-lab/hard-fixture.py): 6 sync cells and 9 frame-number bits,
// with a complement row underneath to reject damaged frames; red background for the marked frames.
const DECODE = `(() => {
  const v = document.getElementById('video');
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720;
  const x = c.getContext('2d', { willReadFrequently: true });
  x.drawImage(v, 0, 0, 1280, 720);
  const px = (X, Y) => x.getImageData(X, Y, 1, 1).data;
  const white = (col, row) => { const d = px(160 + col * 64 + 32, 296 + row * 64 + 32); return (d[0] + d[1] + d[2]) / 3 > 128; };
  const SYNC = [0, 1, 1, 0, 1, 0];
  for (let col = 0; col < 6; col++) for (let row = 0; row < 2; row++) if (white(col, row) !== Boolean(SYNC[col] ^ row)) return { ok: false };
  let n = 0;
  for (let bit = 0; bit < 9; bit++) { const a = white(6 + bit, 0); if (a === white(6 + bit, 1)) return { ok: false }; if (a) n |= 1 << bit; }
  const bg = px(20, 20);
  const color = bg[0] > 150 && bg[1] < 90 && bg[2] < 90 ? 'red' : bg[2] > 150 && bg[0] < 90 && bg[1] < 90 ? 'blue'
    : bg[1] > 100 && bg[0] < 90 && bg[2] < 90 ? 'green' : 'other';
  return { ok: true, frame: n, color };
})()`;

async function openReview(b, user, query) {
  await b.open({ width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await b.goto(`/Cleanyfin/marking/review?${query}`);
  await signIn(b, user);
  await b.waitFor(`document.body.dataset.ready === '1' && document.body.dataset.busy === '0'`, 'review page ready', 30000);
}

async function idle(b) {
  await b.waitFor(`document.body.dataset.busy === '0'`, 'frame step');
}

/** What the page says it shows, and what the pixels say. */
async function observe(b) {
  await idle(b);
  const page = await b.eval(`({ frame: Number(document.getElementById('frameReadout').dataset.frame), ms: Number(document.getElementById('frameReadout').dataset.ms), timing: document.getElementById('timing').dataset.state })`);
  return { ...page, pixels: await b.eval(DECODE) };
}

async function go(b, frame) {
  await b.eval(`(() => { const i = document.getElementById('goTo'); i.value = '#${frame}'; document.getElementById('goForm').requestSubmit(); })()`);
  await sleep(30);
  return observe(b);
}

async function measure(b) {
  await openReview(b, config.user, `item=${config.itemId}`);
  const result = { browser: JSON.parse(await b.eval('document.body.dataset.browser')), product: b.version, checks: [] };
  result.before = await b.eval(`({ timing: document.getElementById('timing').dataset.state, refine: document.body.dataset.refine, text: document.getElementById('timing').textContent })`);
  const record = (how, expected, o) => result.checks.push({ how, expected, page: o.frame, pixels: o.pixels.ok ? o.pixels.frame : null, ms: o.ms, color: o.pixels.color });
  // Seeks: keyframes, B-frames, both boundaries of the marked scene, both ends, and seeded random frames.
  let seed = 3736549;
  const random = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed % 500; };
  const targets = [0, 1, 2, 49, 50, 51, 99, 100, 136, 137, 138, 199, 200, 262, 263, 264, 300, 449, 450, 498, 499,
    ...Array.from({ length: 20 }, random)];
  for (const t of targets) record(`seek #${t}`, t, await go(b, t));
  // Single-frame steps forward and back across the scene start, then 100 ms nudges.
  let at = (await go(b, 130)).frame;
  for (let i = 0; i < 12; i++) { await b.key('.'); at += 1; record('step .', at, await observe(b)); }
  for (let i = 0; i < 5; i++) { await b.key(','); at -= 1; record('step ,', at, await observe(b)); }
  for (let i = 0; i < 3; i++) { await b.key(']'); at = Math.floor((at * 40 + 100) / 40); record('nudge ]', at, await observe(b)); }
  for (let i = 0; i < 3; i++) { await b.key('['); at = Math.floor((at * 40 - 100) / 40); record('nudge [', at, await observe(b)); }
  // The page must keep refusing exact boundaries while this browser is unmeasured.
  await b.key('i');
  result.afterKeyI = await b.eval(`document.getElementById('inMs').textContent`);
  result.mismatches = result.checks.filter((c) => c.page !== c.expected || c.pixels !== c.expected).length;
  result.framesChecked = result.checks.length;
  result.msExact = result.checks.every((c) => c.ms === Math.floor(c.expected * 1000 / 25));
  result.redExact = result.checks.every((c) => (c.color === 'red') === (c.expected >= 137 && c.expected < 263));
  result.fault = await b.eval(`document.getElementById('timing').dataset.state`);
  return result;
}

// ---------------- review page: exact boundaries, propose, accept, publish ----------------

async function refine(b) {
  await openReview(b, config.user, `item=${config.itemId}`);
  const result = { timing: await b.eval(`document.getElementById('timing').dataset.state`) };
  await b.eval(`document.querySelector('#marks li[data-ref="${config.markRef}"]').click()`);
  await b.waitFor(`document.getElementById('markTitle').textContent.includes('rev')`, 'mark selected');
  await idle(b);
  result.refineEnabled = await b.eval('document.body.dataset.refine');
  // Find the first and last affected frames by looking, as a person would: step until the picture changes.
  let o = await go(b, 120);
  while (o.pixels.color !== 'red') { await b.key('.'); o = await observe(b); if (o.frame > 499) throw new Error('no marked frame found'); }
  result.firstRed = { page: o.frame, pixels: o.pixels.frame };
  await b.key('i');
  o = await go(b, 255);
  while (o.pixels.color === 'red') { await b.key('.'); o = await observe(b); }
  await b.key(',');
  o = await observe(b);
  result.lastRed = { page: o.frame, pixels: o.pixels.frame, color: o.pixels.color };
  await b.key('o');
  await b.key('5'); // violence
  for (let i = 0; i < 4; i++) await b.key('s'); // severity cycles back to 2
  result.form = await b.eval(`({ inMs: document.getElementById('inMs').textContent, outMs: document.getElementById('outMs').textContent, category: document.getElementById('category').value, severity: document.getElementById('severity').value, action: document.getElementById('action').value })`);
  await b.key('Enter');
  await b.waitFor(`document.getElementById('editStatus').dataset.result === 'proposed'`, 'proposal saved');
  result.proposed = await b.eval(`document.getElementById('editStatus').textContent`);
  await b.waitFor(`document.getElementById('markTitle').textContent.includes('proposed')`, 'new revision loaded');
  if (config.accept) {
    await b.key('a');
    await b.waitFor(`['accepted', 'refused'].includes(document.getElementById('editStatus').dataset.result)`, 'accept answered');
    result.accepted = await b.eval(`document.getElementById('editStatus').textContent`);
  }
  if (config.publish) {
    await b.waitFor(`!document.getElementById('publishBtn').disabled`, 'publish available');
    await b.click('#publishBtn');
    await b.waitFor(`['published', 'refused'].includes(document.getElementById('editStatus').dataset.result)`, 'publish answered');
    result.published = await b.eval(`document.getElementById('editStatus').textContent`);
    await go(b, 137);
    await b.eval(`document.querySelector('#marks li[data-ref="${config.markRef}"]')?.classList.add('selected')`);
  }
  if (config.screenshot) await b.screenshot(config.screenshot);
  return result;
}

const b = new Browser();
try {
  const run = { capture, measure, refine }[scenario];
  if (!run) throw new Error(`unknown scenario ${scenario}`);
  process.stdout.write(JSON.stringify(await run(b)) + '\n');
} catch (err) {
  process.stdout.write(JSON.stringify({ error: err.message }) + '\n');
  process.exitCode = 1;
} finally {
  await b.close();
}
