// Disposable lab only. Not a household player, authorization layer, or general MP4 parser.
export const video = document.querySelector('video');
const status = document.querySelector('#status');
const seek = document.querySelector('#seek');
let objectURL, plan;

export function event(target, type, timeout = 8000) {
    return new Promise((resolve, reject) => {
        function finish(error) {
            clearTimeout(timer);
            target.removeEventListener(type, success);
            target.removeEventListener('error', failure);
            error ? reject(error) : resolve();
        }
        const success = () => finish();
        const failure = () => finish(Error(`${type} failed`));
        const timer = setTimeout(() => finish(Error(`Timed out waiting for ${type}`)), timeout);
        target.addEventListener(type, success);
        target.addEventListener('error', failure);
    });
}
async function checked(path, options) {
    const response = await fetch(path, {cache: 'no-store', ...options});
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    return response;
}
async function fixture(item) {
    if (!item) return checked('./Fixture.mp4');
    if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) || !/^[0-9a-f]{32}$/i.test(item))
        throw Error('Jellyfin mode requires a loopback lab and fixture ID');
    // Non-admin fixture account on the disposable server. Never store or log its token.
    const auth = await (await checked('/Users/AuthenticateByName', {method: 'POST', headers: {
        'Content-Type': 'application/json', Authorization: 'MediaBrowser Client="Cleanyfin Lab", Device="Fixture", DeviceId="fixture-player", Version="1"'
    }, body: JSON.stringify({Username: 'Viewer', Pw: ''})})).json();
    return checked(`/Videos/${item}/stream.mp4?static=true&mediaSourceId=${item}`, {headers: {'X-Emby-Token': auth.AccessToken}});
}
function parts(bytes) {
    const view = new DataView(bytes.buffer), starts = [];
    for (let i = 0; i < bytes.length;) {
        if (i + 8 > bytes.length) throw Error('Truncated MP4 box');
        const length = view.getUint32(i);
        if (length < 8 || length > bytes.length - i) throw Error('Unsupported MP4 box size');
        if (String.fromCharCode(...bytes.subarray(i + 4, i + 8)) === 'moof') starts.push(i);
        i += length;
    }
    if (starts.length !== 3) throw Error('Not the three-fragment fixture');
    return [bytes.subarray(0, starts[0]), bytes.subarray(starts[0], starts[1]), bytes.subarray(starts[2])];
}
function reset() {
    video.pause(); video.removeAttribute('src'); video.load();
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null; plan = null; seek.disabled = true;
}
async function filteredSource(bytes, cutLength) {
    const mime = 'video/mp4; codecs="avc1.42c01e,mp4a.40.2"';
    if (!window.MediaSource?.isTypeSupported(mime)) throw Error('Browser lacks the fixture codec/MSE');
    const [init, before, after] = parts(bytes);
    const media = new MediaSource(), opened = event(media, 'sourceopen');
    objectURL = URL.createObjectURL(media); video.src = objectURL; await opened;
    const buffer = media.addSourceBuffer(mime);
    async function append(data) { const done = event(buffer, 'updateend'); buffer.appendBuffer(data); await done; }
    await append(init); await append(before);
    buffer.timestampOffset = -cutLength;
    await append(after); media.endOfStream();
}
export async function load(filtered) {
    reset(); status.textContent = 'Checking source and cut…';
    try {
        const config = await (await checked('./plan.json')).json();
        const index = await (await checked('./fixture-index.json')).json();
        const [a, b] = config.cut;
        if (index.sha256 !== config.sha256 || index.keyframes?.length !== 3 ||
            config.cut.length !== 2 || !Number.isFinite(a) || !Number.isFinite(b) || b <= a ||
            a !== index.keyframes[1] || b !== index.keyframes[2])
            throw Error('Unsupported plan; this fixture requires exact fragment boundaries');
        const item = new URLSearchParams(location.search).get('item');
        const response = await fixture(item);
        // Do not accidentally allocate a movie-sized buffer if the lab points to the wrong item.
        const length = Number(response.headers.get('Content-Length'));
        if (!(length > 0 && length <= 1024*1024)) throw Error('Only small known-length lab fixtures are supported');
        const bytes = new Uint8Array(await response.arrayBuffer());
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        if (Array.from(digest, b => b.toString(16).padStart(2,'0')).join('') !== config.sha256) throw Error('Wrong source; playback refused');
        if (filtered) await filteredSource(bytes, b-a);
        else { objectURL = URL.createObjectURL(new Blob([bytes], {type:'video/mp4'})); video.src = objectURL; }
        await video.play(); plan = filtered ? config : null;
        seek.disabled = !filtered;
        status.textContent = `${filtered ? 'Filtered timeline' : 'Original control'} · ${item ? 'Jellyfin direct stream' : 'local fixture'}`;
    } catch (error) { reset(); status.textContent = `Stopped: ${error.message}`; throw error; }
}
export function seekSource(seconds) {
    if (!plan) throw Error('No filtered timeline');
    const [start, end] = plan.cut;
    video.currentTime = seconds < start ? seconds : Math.max(start, seconds - (end-start));
    return video.currentTime;
}
seek.onclick = () => seekSource(4);
