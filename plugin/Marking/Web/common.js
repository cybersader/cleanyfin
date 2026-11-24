// Shared by the capture and review pages. Both are served by the Cleanyfin plugin on Jellyfin's own
// origin, so every call is same-origin: no CORS setting, no second server. Sign-in is Jellyfin's own
// AuthenticateByName; the token stays in this browser and is sent only to this server.

export const BASE = location.pathname.replace(/\/Cleanyfin\/marking\/[^/]*$/, '');
const AUTH_KEY = 'cleanyfin-marking-auth';
const DEVICE_KEY = 'cleanyfin-marking-device';

export const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
};

function deviceId() {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
}

export function session() {
  try { return JSON.parse(localStorage.getItem(AUTH_KEY) ?? 'null'); } catch { return null; }
}

function header(device, token) {
  const q = (s) => String(s).replace(/["\\]/g, '');
  return `MediaBrowser Client="Cleanyfin marking", Device="${q(device)}", DeviceId="${deviceId()}", Version="0.2.0"` +
    (token ? `, Token="${q(token)}"` : '');
}

export async function signIn(device, username, password) {
  const res = await fetch(`${BASE}/Users/AuthenticateByName`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: header(device) },
    body: JSON.stringify({ Username: username, Pw: password }),
  });
  if (!res.ok) throw new Error(res.status === 401 ? 'Wrong name or password.' : `Sign-in failed (${res.status}).`);
  const body = await res.json();
  const s = { token: body.AccessToken, userId: body.User.Id.replace(/-/g, ''), name: body.User.Name, device };
  localStorage.setItem(AUTH_KEY, JSON.stringify(s));
  return s;
}

export async function signOut() {
  const s = session();
  localStorage.removeItem(AUTH_KEY);
  if (s) await fetch(`${BASE}/Sessions/Logout`, { method: 'POST', headers: { Authorization: header(s.device, s.token) } }).catch(() => {});
}

/** A Jellyfin call as the signed-in person. Returns { status, body } and never throws on an HTTP refusal. */
export async function jf(path, { method = 'GET', body } = {}) {
  const s = session();
  const headers = { Authorization: header(s?.device ?? 'Browser', s?.token) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(`${BASE}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = null; }
  if (res.status === 401) localStorage.removeItem(AUTH_KEY);
  return { status: res.status, body: parsed, refusal: res.headers.get('X-Cleanyfin-Refusal') ?? parsed?.refusal ?? null };
}

export const marking = (path, options) => jf(`/Cleanyfin/marking/${path}`, options);

/** The original, through Jellyfin's ordinary static stream. A video element cannot send headers, so the token rides in ApiKey. */
export function streamUrl(itemId) {
  const s = session();
  return `${BASE}/Videos/${itemId}/stream?static=true&mediaSourceId=${itemId}&ApiKey=${encodeURIComponent(s?.token ?? '')}`;
}

export const fmt = (ms, exact = false) => {
  const neg = ms < 0;
  ms = Math.abs(ms);
  const s = Math.floor(ms / 1000);
  const hh = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  const frac = exact ? `.${String(Math.floor(ms % 1000)).padStart(3, '0')}` : '';
  return (neg ? '-' : '') + (hh > 0 ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`) + frac;
};

/** Wires a sign-in form: calls onReady(session) once signed in. */
export function loginForm(device, onReady) {
  const form = $('login');
  const status = $('loginStatus');
  const show = (signedIn) => {
    form.hidden = signedIn;
    for (const el of document.querySelectorAll('[data-needs-login]')) el.hidden = !signedIn;
  };
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    status.textContent = 'Signing in…';
    try {
      const s = await signIn(device, $('username').value, $('password').value);
      $('password').value = '';
      status.textContent = '';
      show(true);
      onReady(s);
    } catch (err) {
      status.textContent = err.message;
    }
  });
  const s = session();
  show(Boolean(s));
  if (s) onReady(s);
}
