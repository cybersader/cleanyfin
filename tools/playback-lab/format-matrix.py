#!/usr/bin/env python3
"""Measure filtered playback on common household stream layouts, one layout at a time (lab only).

For each Jellyfin line, one disposable loopback server with this plugin build and one read-only folder of synthetic
layout copies of the hard fixture (format_fixtures.py). Each layout in turn: the household verifier hashes and
probes the file, a single reviewed mark removes exactly source frames [137, 263) on that file's own clock, the
plugin's real PlaybackInfo answers a desktop-mpv profile with a ticketed cut list, and mpv in the server's private
loopback namespace plays it. Measured per layout, with picture and sound from the same playback: the straight cut
(every kept frame once and in order, no marked frame, every kept sample of every channel equal to mpv's uninterrupted
decode minus exactly the marked sample indices), fresh starts before, at and after the join, and paused exact seeks
across and onto the join. A take that times out or fails is "unmeasured", never a pass.

    python3 tools/playback-lab/format-matrix.py                     # both Jellyfin lines, every layout
    python3 tools/playback-lab/format-matrix.py --jellyfin 12.4 --layout mkv-h264-eac351
    python3 tools/playback-lab/format-matrix.py --local              # plain files, no Jellyfin (diagnosis)

Writes a receipt (JSON) and candidate evidence rows; never edits the reviewed register. Desktop mpv decoding to PCM
only: this says nothing about a TV app, HDMI output or audio passed through undecoded to a receiver.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
sys.path.insert(0, str(LAB))
import format_fixtures as ff  # noqa: E402
import format_measure as fm  # noqa: E402
from evidence_label import describe as describe_source  # noqa: E402
from hard_fixture_lab import DEFAULT_JELLYFIN, JELLYFIN, media  # noqa: E402

_spec = importlib.util.spec_from_file_location('check_plugin', LAB/'check-plugin.py')
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

# The static player setting every take uses (and the evidence tuple names): AC-3/E-AC-3 decoders otherwise add
# dither whose random state differs after a join, so kept sound would differ by 1 LSB from the uncut decode.
PLAYER_OPTIONS = ['--ad-lavc-o=cons_noisegen=1']
DEVICE_MODE = 'Linux lab container, desktop mpv, decoded PCM capture, ad-lavc-o cons_noisegen 1'
DIAGNOSTIC = {'mp4-h264-aacnopns'}  # measured to explain a result; never a register row
CONTROLS = ['straight-play', 'seek-forward', 'seek-back']


def redact(text):
    """Never keep a capability: slots, tickets and the lab origin go before anything is stored."""
    text = re.sub(r'/Cleanyfin/play/[0-9a-f]{32}/', '/Cleanyfin/play/<slot>/', text)
    text = re.sub(r'([?&]t=)[0-9a-f]{32}', r'\1<ticket>', text)
    return re.sub(r'%\d+%http://127\.0\.0\.1:\d+[^,]*/Cleanyfin', '%n%<origin>/Cleanyfin', text)


def mpv_identity():
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', fm.MPV], text=True))[0]['Id']
    version = subprocess.run(['docker', 'run', '--rm', '--pull=never', '--network', 'none', '--entrypoint', 'mpv', fm.MPV,
                              '--version'], capture_output=True, text=True, timeout=60).stdout.split()[1]
    return image.removeprefix('sha256:'), version.removeprefix('v')


def probe_json(directory, file):
    return json.loads(media(directory, 'ffprobe', '-show_entries',
                            'format=duration,start_time:stream=index,codec_type,codec_name,time_base,start_pts',
                            '-of', 'json', f'/lab/{file}', readonly=True))


def summarize(name, result):
    if 'unmeasured' in result:
        return dict(layout=name, result='unmeasured', why=result['unmeasured'])
    v = fm.verdict(result)
    s = result['straight']
    starts = result['starts']
    deviations = []
    if s['picture']['markedCount'] or not s['picture']['ok']:
        deviations.append(f"straight cut: {s['picture']['markedCount']} marked frame(s), {s['picture']['missing']} kept frame(s) missing")
    if not s['sound']['exact']:
        deviations.append(f"straight cut sound: {s['sound']['summary']}, peak difference {max(s['sound']['peakLRmax'])} LSB")
    for x in starts:
        if x['markedCount'] or not x['pictureOk']:
            deviations.append(f"start {x['startSeconds']} s: {x['markedCount']} marked frame(s) or kept frames out of order")
        if not x['sound']['exact']:
            lead = x.get('leadingExtraSamples')
            deviations.append(f"start {x['startSeconds']} s: " + (
                f"{lead} extra sample(s) from before the start position ({'MARKED' if x['extraMarked'] else 'kept'} source samples)"
                if lead else x['sound']['summary']))
    # Subtitle text is marked content too: a line that starts inside the mark (e.g. a language mark) must not be shown.
    marked_lines = [text for begin, _, text in ff.SUBTITLES if ff.START*fm.FRAME_MS <= begin < ff.END*fm.FRAME_MS]
    shown = [t for t in marked_lines if any(seen.startswith(t + ' @') for seen in s.get('subtitles', []))]
    if shown:
        deviations.append(f"subtitle line(s) that start inside the mark shown after the join: {', '.join(shown)}")
    for l in result.get('seek', {}).get('landings', []):
        if l['landed'] != l['expected'] or l['marked']:
            deviations.append(f"seek {l['target']}: landed on frame {l['landed']}, expected {l['expected']}" + (' (MARKED)' if l['marked'] else ''))
    v = dict(v, subtitles=not shown, exact=v['exact'] and not shown)
    return dict(layout=name, result='exact' if v['exact'] else 'inexact', verdict=v, deviations=deviations)


def register_row(name, summary, result, tuple_, evidence_ref, today):
    """A candidate row for the reviewed register: experimental if every claim measured exact, refused otherwise."""
    v = summary.get('verdict', {})
    s = result.get('straight', {})

    def r(ok, text_ok, text_bad):
        return dict(outcome='pass' if ok else 'fail', limit=text_ok if ok else text_bad)
    pic, snd = s.get('picture', {}), s.get('sound', {})
    devs = '; '.join(summary.get('deviations', []))[:400] or 'none'
    results = dict(
        markedPicture=r(v.get('markedPicture') and v.get('seeks') and v.get('subtitles', True), 'Zero of 126 marked frames in the straight cut, the fresh starts and the seeks; every seek landed on the expected kept frame.',
                        f"Marked picture shown: {devs}"),
        markedSound=r(v.get('markedSound'), 'Kept sound equals the uncut decode minus exactly the marked sample indices, so no marked sample; 0 ms of 880 Hz in 5 ms windows.',
                      f"Marked or misplaced sound: {devs}"),
        keptPictureAtJoin=r(v.get('keptPicture'), f"All {pic.get('frames')} kept frames once and in order, including both join neighbours.",
                            f"Kept picture not exact: {devs}"),
        keptSoundAtJoin=r(v.get('keptSound') and v.get('starts'), f"{snd.get('summary', '')}; every channel, no realignment; fresh starts before, at and after the join exact.",
                          f"Kept sound not exact: {devs}"))
    return dict(tuple=tuple_, actions=['cut'], controls=CONTROLS, results=results, evidenceRef=evidence_ref, date=today,
                status='experimental' if summary['result'] == 'exact' else 'refused',
                observation=(f"Desktop mpv in the plugin lab, {name}, synthetic hard-fixture copy; decoded PCM only. "
                             'Not a TV app, HDMI output or audio passthrough; resume and restart not measured.'))


class FormatLab(cp.PointerLab):
    """One disposable server for a whole Jellyfin line; layouts are measured one after another."""

    def __init__(self, work, jellyfin, fixtures, layouts, mpv):
        super().__init__(work, jellyfin)
        self.name = 'cleanyfin-formats-' + secrets.token_hex(6)
        self.fixtures, self.layouts = fixtures, layouts
        self.mpv_image, self.mpv_version = mpv
        self.client, self.version = 'mpv', self.mpv_version
        self.mounts = ['-v', f'{fixtures}:/media/movies:ro,z']

    def enroll_evidence(self, uid):
        self.put(self.data/'profiles'/uid/'evidence-device.txt', f'{self.mpv_image}\n{DEVICE_MODE}\n'.encode())

    def detail(self, item):
        status, body, _ = self.m_get(f'items/{item}')
        return body if status == 200 else None

    def m_get(self, path):
        status, h, body = self.request('/Cleanyfin/marking/' + path, user=self.admin)
        return status, (json.loads(body) if body and h.get('content-type', '').startswith('application/json') else body), h

    def prepare(self):
        self.users_library()
        self.original = (self.work/'fixture/source.mp4').read_bytes()  # what inspect() looks for in server files
        api = self.api
        self.folder('Movies', '/media/movies')
        folders = {f['Name']: f['ItemId'] for f in api('/Library/VirtualFolders', user=self.admin)}
        self.refresh(folders['Movies'])
        wanted = {f'/media/movies/{n}.{ff.LAYOUTS[n]["container"]}': n for n in self.layouts}
        self.items = {}
        for _ in range(180):
            items = api('/Items?Recursive=true&IncludeItemTypes=Movie,Video&Fields=Path,MediaSources', user=self.admin)['Items']
            self.items = {wanted[i['Path']]: i['Id'].replace('-', '') for i in items if i.get('Path') in wanted and i.get('MediaSources')}
            if len(self.items) == len(wanted):
                break
            time.sleep(1)
        policy = api('/Users/'+self.viewer, user=self.admin)['Policy']
        policy.update(EnableAllFolders=False, EnabledFolders=[folders['Movies']])
        api(f'/Users/{self.viewer}/Policy', 'POST', policy, user=self.admin)
        self.folders = folders
        self.configurl = '/Plugins/'+cp.PLUGIN+'/Configuration'
        self.cfg = api(self.configurl, user=self.admin)
        self.data = self.work/'config/plugins/configurations/cleanyfin-shelves-v1'
        self.register_path = self.data/'evidence-register-v1.json'
        self.policy(self.viewer, self.one[0], ['violence'])
        self.cfg.update(EnableAuthRangeLab=True, LabOrigin=self.origin, LabPointerKey=secrets.token_hex(16),
                        LabLifetimeSeconds=600, EditionHashBytesPerSecond=0)
        self.update()
        self.server_ref = api('/System/Info/Public')['Id']
        self.extra_tokens = []

    def tuple_for(self, source):
        return dict(adapterId='mpv-edl', adapterRevision='2', appId='mpv', appVersion=self.mpv_version, appBuild=self.mpv_image,
                    backend='mpv', deviceMode=DEVICE_MODE, jellyfinVersion=self.server_version, pluginBuild=self.plugin_build,
                    source=source)

    def offer(self, name, item):
        spec = ff.LAYOUTS[name]
        profile = {'Name': 'mpv', 'DirectPlayProfiles': [{'Type': 'Video', 'Container': spec['container'],
                   'VideoCodec': 'h264,hevc', 'AudioCodec': 'aac,ac3,eac3,flac,opus'}],
                   'TranscodingProfiles': [{'Type': 'Video', 'Context': 'Streaming', 'Protocol': 'hls', 'Container': 'ts',
                                            'VideoCodec': 'h264', 'AudioCodec': 'aac'}],
                   # mpv renders embedded subtitles itself; without this Jellyfin plans a transcode for a default subtitle.
                   'SubtitleProfiles': [{'Format': 'srt', 'Method': 'Embed'}, {'Format': 'subrip', 'Method': 'Embed'}]}
        status, h, raw = self.request('/Items/'+self.pointer+'/PlaybackInfo', 'POST',
                                      {'DeviceProfile': profile, 'MediaSourceId': self.pointer, 'MaxStreamingBitrate': 100000000,
                                       'AutoOpenLiveStream': True}, user=self.one)
        sources = json.loads(raw).get('MediaSources', []) if status == 200 else []
        if len(sources) != 1:
            log = cp.docker('logs', self.cid, stderr=subprocess.STDOUT).decode(errors='replace')
            hint = [l for l in log.splitlines() if 'Evidence tuple unknown' in l or 'projection refused' in l][-2:]
            raise fm.Unmeasured(f"no filtered offer ({h.get('x-cleanyfin-reason')}); {' | '.join(hint)}")
        s = sources[0]
        if (s['Container'] != spec['container'] or not s['SupportsDirectPlay'] or s['SupportsTranscoding'] or s.get('TranscodingUrl')
                or h.get('x-cleanyfin-evidence') != 'experimental'):
            raise RuntimeError('filtered source is not the direct-only projection of the measured layout')
        url = s['Path']
        self.slots.add(url.split('/play/')[1].split('/')[0])
        self.tokens.add(url.split('t=')[1])
        st, _, edl = self.request(url)
        if st != 200:
            raise fm.Unmeasured(f'cut list status {st}')
        return url, s, edl.decode()

    def measure(self, name):
        spec = ff.LAYOUTS[name]
        item = self.items.get(name)
        if not item:
            return dict(unmeasured='not indexed by the server')
        detail = None
        for _ in range(240):
            detail = self.detail(item)
            if detail and detail.get('binding'):
                break
            time.sleep(0.5)
        binding = (detail or {}).get('binding')
        if not binding:
            state = (detail or {}).get('edition')
            return dict(unmeasured=f'edition not verified: {state}')
        tracks = binding['tracks']
        video = next(t for t in tracks if t['kind'] == 'video')
        av = video['anchorSourceMs']
        start_ms, end_ms = av + ff.START*fm.FRAME_MS, av + ff.END*fm.FRAME_MS
        # Snapshot exactly as the review page publishes it, bound to the verified household edition.
        doc = json.loads((ROOT/'tests/Delivery.Contract.Tests/Fixtures/hard-fixture.snapshot.v1.json').read_bytes())
        now = datetime.now(timezone.utc)
        doc['acceptedAt'] = (now - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        doc['expiresAt'] = (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        doc['binding'] = binding
        doc['annotations'] = [self.mark('m1', 'violence', 'skip', start_ms, end_ms)]
        snapshots = self.data/'snapshots'
        for old in snapshots.glob('*.json') if snapshots.exists() else []:
            old.unlink()
        self.put(snapshots/(item+'.json'), json.dumps(doc).encode())
        self.item = item
        original = self.api('/Items/'+item+'/PlaybackInfo', user=self.admin)['MediaSources'][0]
        source, audio_index = describe_source(original['Container'], original['MediaStreams'], tracks, binding['durationMs'])
        tuple_ = self.tuple_for(source)
        pending = dict(tuple=tuple_, actions=['cut'], controls=[], evidenceRef='tools/playback-lab/format-matrix.py',
                       date=now.strftime('%Y-%m-%d'), status='experimental',
                       observation='Measurement in progress: admits this layout so it can be measured; claims nothing.',
                       results={k: dict(outcome='not-measured', limit='Being measured by format-matrix.py.')
                                for k in ('markedPicture', 'markedSound', 'keptPictureAtJoin', 'keptSoundAtJoin')})
        self.put(self.register_path, json.dumps(dict(registerVersion=1, rows=[pending])).encode())
        self.cfg['LabSourceSha256'] = binding['sha256']
        self.update()
        if self.api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] != 1:
            return dict(unmeasured='reconcile did not produce one filtered item', tuple=tuple_)
        self.pointer, _ = self.find_pointer(self.viewer)
        self.folders = {f['Name']: f['ItemId'] for f in self.api('/Library/VirtualFolders', user=self.admin)}
        p = self.api('/Users/'+self.viewer, user=self.admin)['Policy']
        p.update(EnableAllFolders=False, EnabledFolders=[self.folders['Movies'], self.folders['Playback samples '+self.viewer]])
        self.api('/Users/'+self.viewer+'/Policy', 'POST', p, user=self.admin)
        url, projected, edl = self.offer(name, item)
        probe = probe_json(self.fixtures, f"{name}.{spec['container']}")
        result = dict(tuple=tuple_, audioIndex=audio_index, projectedAudioIndex=projected.get('DefaultAudioStreamIndex'),
                      edl=redact(edl))
        work = self.work/'capture'/name
        work.mkdir(parents=True)
        (work/'plan.json').write_text(json.dumps(ff.plan({'sha256': binding['sha256']})))
        runner = fm.Runner(work, network='container:'+self.cid, timeout=self.case_timeout, mounts=['-v', f'{self.fixtures}:/media:ro,z'])
        channels = spec['audio'][0][1]
        try:
            # Each take re-asks PlaybackInfo, so a slow host cannot outlive a ticket.
            fresh = lambda: self.offer(name, item)[0]  # noqa: E731
            measured = fm.measure(runner, source=fresh, control_source=f"/media/{name}.{spec['container']}", probe=probe,
                                  channels=channels, plan_path=work/'plan.json', mpv_options=PLAYER_OPTIONS)
            measured['seek'] = fm.seeks(runner, fresh(), PLAYER_OPTIONS)
        except fm.Unmeasured as e:
            return result | dict(unmeasured=str(e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return result | measured


def local(layouts, fixtures, case_timeout):
    results = {}
    for name in layouts:
        spec = ff.LAYOUTS[name]
        file = f"{name}.{spec['container']}"
        probe = probe_json(fixtures, file)
        cut = fm.plan_for(probe)
        audio = spec['audio'][0][0]
        # The same choice as the plugin (PointerEvidence.Partitions).
        partitions = ('split' if audio == 'flac' else 'before-instant' if spec['container'] == 'mkv' and audio in ('ac3', 'eac3')
                      else 'joint')
        (fixtures/f'{name}.edl').write_text(fm.edl_body(file, cut, split=5 if partitions == 'split' else 0,
                                                        nudge_us=5 if partitions == 'before-instant' else 0))
        (fixtures/f'{name}.plan.json').write_text(json.dumps(ff.plan({'sha256': '0'*64})))
        runner = fm.Runner(fixtures, timeout=case_timeout)
        print(f'{name}: measuring (local file, {partitions} cut list)', flush=True)
        try:
            r = fm.measure(runner, source=f'{name}.edl', control_source=file, probe=probe, channels=spec['audio'][0][1],
                           plan_path=fixtures/f'{name}.plan.json', mpv_options=PLAYER_OPTIONS)
            r['seek'] = fm.seeks(runner, f'{name}.edl', PLAYER_OPTIONS)
        except fm.Unmeasured as e:
            r = dict(unmeasured=str(e))
        results[name] = r
        print(json.dumps(summarize(name, r)), flush=True)
    return results


def main():
    if not __debug__:
        raise SystemExit('Run without Python optimization: fixture assertions are required.')
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--jellyfin', choices=[*sorted(JELLYFIN), 'both'], default='both')
    p.add_argument('--layout', action='append', choices=sorted(ff.LAYOUTS), help='repeat to pick layouts (default: all)')
    p.add_argument('--local', action='store_true', help='plain files through mpv only; no Jellyfin, no plugin')
    p.add_argument('--case-timeout', type=int, default=600, help='seconds per mpv take; a timeout is "unmeasured"')
    p.add_argument('--receipt', type=Path, help='write the full receipt JSON here (default: print a summary only)')
    p.add_argument('--rows', type=Path, help='write candidate register rows (JSON list) here')
    args = p.parse_args()
    layouts = args.layout or [n for n in ff.LAYOUTS if n not in DIAGNOSTIC]
    scratch = ROOT/'plugin/obj'
    scratch.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='formats-', dir=scratch))
    fixtures = work/'movies'
    fixtures.mkdir()
    print(f'Generating {len(layouts)} synthetic layouts (pinned Jellyfin ffmpeg)...', flush=True)
    facts = {}
    for name in layouts:
        facts[name] = ff.generate(fixtures, name)
        print(f"  {name}: {facts[name]['sha256'][:16]}... {facts[name]['size']} bytes", flush=True)
    receipt = dict(method=fm.__doc__.strip().splitlines()[0], playerOptions=PLAYER_OPTIONS, deviceMode=DEVICE_MODE,
                   fixtures={n: {k: f[k] for k in ('sha256', 'size', 'video', 'audio')} for n, f in facts.items()}, runs={})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    rows, ok = [], True
    try:
        if args.local:
            receipt['runs']['local'] = {n: dict(result=r, summary=summarize(n, r)) for n, r in local(layouts, fixtures, args.case_timeout).items()}
        else:
            mpv = mpv_identity()
            receipt['mpv'] = dict(image=mpv[0], version=mpv[1])
            for line in (sorted(JELLYFIN) if args.jellyfin == 'both' else [args.jellyfin]):
                lab = FormatLab(work/f'jf-{line}', line, fixtures, layouts, mpv)
                lab.work.mkdir()
                lab.case_timeout = args.case_timeout
                run = receipt['runs'][line] = {}
                passed = False
                try:
                    lab.setup()
                    lab.prepare()
                    receipt.setdefault('pluginBuild', {})[line] = lab.plugin_build
                    for name in layouts:
                        print(f'[{line}] {name}: measuring through PlaybackInfo and the ticketed cut list', flush=True)
                        try:
                            result = lab.measure(name)
                        except (fm.Unmeasured, RuntimeError, AssertionError, subprocess.SubprocessError, OSError, KeyError, ValueError) as e:
                            result = dict(unmeasured=f'{type(e).__name__}: {e}'[:300])
                        summary = summarize(name, result)
                        run[name] = dict(result=result, summary=summary)
                        print(json.dumps(summary), flush=True)
                        if 'tuple' in result and summary['result'] != 'unmeasured' and name not in DIAGNOSTIC:
                            rows.append(register_row(name, summary, result, result['tuple'],
                                                     'tools/playback-lab/format-matrix.py', today))
                    cp.docker('stop', lab.cid)
                    lab.inspect()
                    passed = True
                finally:
                    if lab.cid:
                        subprocess.run(['docker', 'stop', lab.cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if passed:
                            subprocess.run(['docker', 'rm', '-v', lab.cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if not passed:
                        ok = False
                        print(f'[{line}] lab failed; retained {lab.work} ({lab.name})', flush=True)
    finally:
        if args.receipt:
            args.receipt.write_text(json.dumps(receipt, indent=1) + '\n')
        if args.rows:
            args.rows.write_text(json.dumps(rows, indent=2) + '\n')
        print('\nLayout | ' + ' | '.join(receipt['runs']), flush=True)
        for name in layouts:
            print(name + ' | ' + ' | '.join(r.get(name, {}).get('summary', {}).get('result', 'unmeasured') for r in receipt['runs'].values()), flush=True)
        if ok:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
