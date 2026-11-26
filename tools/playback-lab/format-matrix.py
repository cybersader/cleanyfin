#!/usr/bin/env python3
"""Measure filtered playback on common household stream layouts, one layout at a time (lab only).

For each Jellyfin line, one disposable loopback server with this plugin build and one read-only folder of synthetic
layout copies of the hard fixture (format_fixtures.py). Each layout in turn: the household verifier hashes and
probes the file, a single reviewed mark removes exactly source frames [137, 263) on that file's own clock, the
plugin's real PlaybackInfo answers a desktop-mpv profile with a ticketed cut list, and mpv in the server's private
loopback namespace plays it. Picture and sound are captured from the same playback on straight cuts, fresh starts,
and paused seeks; subtitle layouts also exercise every offered track. The mark is then republished at each sweep
position (on and off frame and packet edges, one-frame and one-millisecond marks, and marks at either endpoint).
R22 judges every straight, fresh-start and seek-and-resume take against the same-layout uncut control and the ideal
hard cut. Timeouts and incomplete captures are unmeasured, never passes. The host oracle needs NumPy and SciPy.

    python3 tools/playback-lab/format-matrix.py                    # both lines, 14 named R22 fixtures
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
import aac_noise  # noqa: E402
from r21_oracle import deviation  # noqa: E402
from r22_oracle import R22, THRESHOLDS  # noqa: E402
from evidence_label import describe as describe_source  # noqa: E402
from hard_fixture_lab import DEFAULT_JELLYFIN, JELLYFIN, media  # noqa: E402

_spec = importlib.util.spec_from_file_location('check_plugin', LAB/'check-plugin.py')
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

# The static player setting every take uses (and the evidence tuple names): AC-3/E-AC-3 decoders otherwise add
# dither whose random state differs after a join, so kept sound would differ by 1 LSB from the uncut decode.
PLAYER_OPTIONS = ['--ad-lavc-o=cons_noisegen=1']
DEVICE_MODE = 'Linux lab container, desktop mpv, decoded PCM capture, ad-lavc-o cons_noisegen 1'
DIAGNOSTIC = {'mp4-h264-aacnopns', 'mkv-h264-aacnopns', ff.HARD_AAC}  # measured to explain a result; never a register row
DOLBY_LAYOUTS = ('mkv-h264-ac351', 'mkv-h264-eac351', 'mkv-hevc10-eac351', 'mkv-hevc10-hdr10-eac351',
                 'mp4-h264-eac351', 'mkv-h264-eac351+aac-srt', 'mkv-h264-eac351-ass+srt', 'mp4-h264-eac351-mov_text')
R22_LAYOUTS = (*DOLBY_LAYOUTS, 'mkv-h264-flac', 'mp4-h264-aac', 'mkv-h264-aac', 'mkv-hevc10-aac', 'mp4-hevc10-aac', ff.HARD_AAC)
# Owner decision R20: real AAC encoder profiles. Not in the default run (--aac or --layout picks them).
AAC_PROFILES = ['mp4-h264-aacnopns', 'mp4-h264-aac51', 'mp4-h264-fdkaac', 'mp4-h264-fdkaaclow', 'mp4-h264-fdkaac51',
                'mkv-h264-aacnopns', 'mkv-h264-fdkaac']
CONTROLS = ['straight-play', 'seek-forward', 'seek-back']
# The admission sweep: marks [start, end) in ms after the first picture (frames every 40 ms; AC-3/E-AC-3 packets every
# 32 ms, at 32k ms in MP4 and 32k - 5.333 ms in Matroska, where the encoder priming is stored). A layout is admitted only
# if every one passes R22 on that server line: straight cut, fresh starts, and paused exact seeks with resume.
POSITIONS = [
    (5480, 10520),   # the hard fixture's own mark: on frames, inside a GOP and an audio packet
    (1000, 1960),    # on frames, short
    (6000, 12000),   # both edges on keyframes (frames 150 and 300)
    (2000, 2040),    # exactly one frame
    (4000, 4001),    # one millisecond starting on a frame: one frame and 48 samples
    (12345, 12346),  # one millisecond between frames: no frame, 48 samples only
    (3001, 3961),    # 1 ms after a frame instant
    (3039, 7999),    # 1 ms before a frame instant
    (1600, 3200),    # on frames that are also MP4 audio packet edges
    (4800, 9600),    # likewise, longer
    (1595, 3195),    # just after a Matroska audio packet edge (1594.667, 3194.667 ms)
    (1594, 3194),    # just before it
    (5131, 8331),    # just after Matroska packet edges, mid-frame
    (2333, 6654),    # arbitrary, mid-frame and mid-packet
    (4040, 8361),
    (6143, 10464),
    (7013, 7509),
    (8888, 13017),
    (9216, 11215),   # 9216 is an MP4 packet edge, mid-frame
    (10999, 15001),
    (15360, 16000),  # MP4 packet edge on a frame
    (0, 1000),       # a mark from the very start
    (0, 1001),       # from the start to 1 ms after a frame: the first picture then comes 39 ms in
    (18000, 10**9),  # to the very end of the file
]


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
    positions = result.get('sweep', [])
    passed = sum(p['result'] == 'pass' for p in positions)
    measured = sum(p['result'] != 'unmeasured' for p in positions)
    counts = dict(passed=passed, measured=measured, total=len(POSITIONS))
    if 'unmeasured' in result:
        return dict(layout=name, result='unmeasured', why=result['unmeasured'], positions=counts,
                    deviationTypes=['unmeasured'])
    subtitle_checks = result.get('subtitleChecks', ['no filtered sidecars measured'] if ff.LAYOUTS[name]['subtitles'] else [])
    original_takes = [t['r22'] for t in [result.get('straight', {}), *result.get('starts', [])] if 'r22' in t]
    original_unmeasured = (bool(result.get('originalUnmeasured')) or not original_takes or
                           any(t['unmeasured'] for t in original_takes))
    original_ok = (not original_unmeasured and all(t['passed'] for t in original_takes)
                   and result.get('seek', {}).get('ok', False))
    v = dict(sweep=passed == len(POSITIONS), subtitles=not subtitle_checks, originalMark=original_ok)
    kinds = {d['kind'] for p in positions for d in p['deviations']}
    kinds.update(k for t in original_takes for k, ok in t['criteria'].items() if not ok)
    if subtitle_checks:
        kinds.add('subtitle-deviation')
    if measured < len(POSITIONS) or original_unmeasured:
        kinds.add('unmeasured')
    metrics = [t['metrics'] for p in positions for t in p.get('r22Takes', {}).values()]
    keys = ('keptSoundLossMs', 'worstAvOffsetMs', 'transientExcessRms')
    worst = {k: max((m[k] for m in metrics if k in m), default=None) for k in keys}
    worst['transientMarginRms'] = min((m['transientMarginRms'] for m in metrics if 'transientMarginRms' in m),
                                      default=None)
    return dict(layout=name, result='r22-pass' if all(v.values()) else 'unmeasured' if 'unmeasured' in kinds else 'r22-fail',
                verdict=v, deviationTypes=sorted(kinds), positions=counts, worst=worst,
                worstMeasuredTakes={k: sum(k in m for m in metrics) for k in keys},
                originalUnmeasured=result.get('originalUnmeasured'))


def check_subtitles(spec, result):
    """A leak OR missing/mistimed kept text OR an unaccounted selectable track fails the layout."""
    problems = []
    a, b = (result['cut'][k] + result['cut']['zeroMs'] for k in ('startMs', 'endMs'))
    forbidden = [t for _, _, cues in spec['subtitles'] for start, end, t in cues if start <= b and end >= a]
    expected_tracks = [(i, 'ass' if codec == 'ass' else 'subrip', flags == 'default', flags == 'forced')
                       for i, (codec, flags, _) in enumerate(spec['subtitles'], 1)]
    for sid, ((_, _, cues), take) in enumerate(zip(spec['subtitles'], result['subtitleTakes'], strict=True), 1):
        control, straight = take['control'], take['straight']
        if [s.rsplit(' @', 1)[0] for s in control['subtitles']] != [t for _, _, t in cues]:
            problems.append(f'track {sid}: positive control did not display every authored cue')
        actual_tracks = [(t['id'], t.get('codec'), bool(t.get('default')), bool(t.get('forced'))) for t in straight['subtitleTracks']]
        if actual_tracks != expected_tracks or [t['id'] for t in straight['subtitleTracks'] if t.get('selected')] != [sid]:
            problems.append(f'track {sid}: unexpected selectable track list or not selected ({actual_tracks})')
        kept = [(start if end < a else start-(b-a), end if end < a else end-(b-a), t)
                for start, end, t in cues if end < a or start > b]
        actual = [(t, round(float(at)*1000)) for t, at in (s.rsplit(' @', 1) for s in straight['subtitles'])]
        def matching_times(actual, expected):
            # time-pos is evaluated on 25 fps frames; one frame is the observation resolution, not a cut tolerance.
            return len(actual) == len(expected) and all(abs(x-y) <= fm.FRAME_MS for x, y in zip(actual, expected))
        if ([t for t, _ in actual] != [t for _, _, t in kept] or
                not matching_times([t for _, t in actual], [start for start, _, _ in kept]) or
                not matching_times([round(t*1000) for t in straight['subtitleClears']], [end for _, end, _ in kept])):
            problems.append(f'track {sid}: kept subtitle sequence/onset/clear not as expected')
        traces = straight['subtitles'] + [text for start in take.get('starts', []) for text in start['subtitles']]
        for l in take['subtitleSeek']['landings']:
            if l['target'] is None:
                continue
            at = l['target'] * 1000
            expected = next((text for begin, end, text in kept if begin <= at < end), '')
            if l['sid'] != sid or (l['subtitle'] or '') != expected:
                problems.append(f"track {sid}: wrong active subtitle at seek {l['target']} s")
            traces.append(l['subtitle'] or '')
        if not take['subtitleSeek']['ok']:
            problems.append(f'track {sid}: wrong seek picture')
        if not take['jointPassed']:
            problems.append(f'track {sid}: selected-track picture/sound did not pass R22')
        leaks = [text for text in forbidden if any(text in line for line in traces)]
        if leaks:
            problems.append(f'track {sid}: forbidden subtitle text shown: {", ".join(leaks)}')
    return problems


def register_row(name, summary, tuple_, evidence_ref, today, complete_line):
    """Only Dolby tuples passing all six criteria may become an experimental candidate."""
    dolby = name in DOLBY_LAYOUTS
    admitted = complete_line and dolby and summary['result'] == 'r22-pass'
    n = summary['positions']
    swept = f"{n['passed']}/{n['total']} positions passed"
    kinds = ', '.join(summary['deviationTypes']) or 'none'
    failure = f'R22: {swept}; deviations: {kinds}.'
    if not dolby:
        failure += ' Not authorized for admission (FLAC waits for owner; AAC stays off).'
    outcome = 'pass' if admitted else ('not-measured' if summary['result'] == 'unmeasured' else 'fail')
    limits = dict(markedPicture='Zero marked frames, including paused seek landings.',
                  markedSound='Zero marked samples proven by an exact all-channel source match; unresolved residuals never pass.',
                  keptPictureAtJoin='Zero extra missing, repeated or frozen kept frames; <=1 ms timestamp resolution beyond uncut control.',
                  keptSoundAtJoin='No longer zero or -60 dBFS silence; 2 kHz/5 ms RMS no higher than ideal splice or same-picture uncut onset, within repeat-control noise; A/V within calibrated jitter in 100 ms and stays; <=40 ms kept loss.')
    results = {key: dict(outcome=outcome, limit=(f'R22: {swept}. {text}' if admitted else failure))
               for key, text in limits.items()}
    return dict(tuple=tuple_, actions=['cut'], controls=CONTROLS, results=results, evidenceRef=evidence_ref, date=today,
                status='experimental' if admitted else 'refused',
                observation=f'Desktop mpv lab decoded PCM only, {name}: {swept}. {R22} '
                            'No TV, HDMI, passthrough or persisted-resume claim.' +
                            (' R19: offered subtitle tracks tested.' if ff.LAYOUTS[name]['subtitles'] else ''))


def candidate_rows(run, evidence_ref, today, finished):
    # Several named fixtures can resolve to the same observed tuple (e.g. HDR and SDR on 12.4).
    # A partial line or an unbound fixture cannot establish which named tests cover that tuple.
    complete_line = finished and all('tuple' in run.get(name, {}).get('result', {}) for name in DOLBY_LAYOUTS)
    by_tuple = {}
    for name, entry in run.items():
        if name in DIAGNOSTIC or 'tuple' not in entry['result']:
            continue
        tuple_ = entry['result']['tuple']
        row = register_row(name, entry['summary'], tuple_, evidence_ref, today, complete_line)
        by_tuple.setdefault(json.dumps(tuple_, sort_keys=True), []).append((name, row))
    rows = []
    for group in by_tuple.values():
        # A refusal on a shared tuple wins over a pass; do not emit duplicate tuples.
        row = min((r for _, r in group), key=lambda r: r['status'] != 'refused')
        if len(group) > 1:
            row['observation'] += ' Shared tuple fixtures: ' + ', '.join(name for name, _ in group) + '.'
        rows.append(row)
    return rows



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

    def tuple_for(self, source, spec):
        # MpvEdlRenderer.Revision: the cut-list form and whether subtitle partitions are present.
        revision = {('joint', False): '2', ('joint', True): '3', ('split', False): '4', ('split', True): '5'}[
            (form(spec), bool(spec['subtitles']))]
        return dict(adapterId='mpv-edl', adapterRevision=revision, appId='mpv', appVersion=self.mpv_version, appBuild=self.mpv_image,
                    backend='mpv', deviceMode=DEVICE_MODE, jellyfinVersion=self.server_version, pluginBuild=self.plugin_build,
                    source=source)

    def offer(self, name, item):
        spec = ff.LAYOUTS[name]
        profile = {'Name': 'mpv', 'DirectPlayProfiles': [{'Type': 'Video', 'Container': spec['container'],
                   'VideoCodec': 'h264,hevc', 'AudioCodec': 'aac,ac3,eac3,flac,opus'}],
                   'TranscodingProfiles': [{'Type': 'Video', 'Context': 'Streaming', 'Protocol': 'hls', 'Container': 'ts',
                                            'VideoCodec': 'h264', 'AudioCodec': 'aac'}],
                   # mpv renders embedded subtitles itself; without this Jellyfin plans a transcode for a default subtitle.
                   'SubtitleProfiles': [{'Format': c, 'Method': 'Embed'} for c in ('srt', 'subrip', 'ass', 'mov_text')]}
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

    def subtitle_routes(self, edl, list_url, projected, spec):
        # Inspect only our loopback descriptor URLs; never store credentials or route payloads in the receipt.
        links = re.findall(r'%\d+%(http://[^,\n]+)', edl)
        source = next(u for u in links if '/source?t=' in u)
        subs = [u for u in links if '/subtitle-' in u]
        self.tokens.update(u.split('t=')[1] for u in links)
        tracks = [t for t in projected['MediaStreams'] if t['Type'] == 'Subtitle']
        assert len(subs) == len(tracks) == len(spec['subtitles']), 'EDL and PlaybackInfo must expose only rewritten tracks'
        for link, track, (codec, flags, _) in zip(subs, tracks, spec['subtitles']):
            assert f"/subtitle-{track['Index']}.{'ass' if codec == 'ass' else 'srt'}?" in link
            assert (track['Codec'] == ('ass' if codec == 'ass' else 'subrip') and
                    track['IsDefault'] == (flags == 'default') and track['IsForced'] == (flags == 'forced') and
                    not track.get('IsExternal') and not track.get('DeliveryUrl'))
            st, headers, content = self.request(link)
            hs, _, _ = self.request(link, method='HEAD')
            assert st == hs == 200 and content and headers.get('cache-control') == 'private, no-store'
            url_base = link.split('?')[0]
            for wrong in (list_url, source):
                assert self.request(url_base + '?' + wrong.split('?')[1])[0] == 404, 'wrong subtitle ticket accepted'
            assert self.request(source.split('?')[0] + '?' + link.split('?')[1])[0] == 404, 'subtitle ticket authorized the original'
            assert self.request(link, method='POST')[0] == 404, 'subtitle ticket authorized an unsupported method'
        assert self.request('/Videos/'+self.pointer+'/'+self.pointer+'/Subtitles/'+str(tracks[0]['Index'])+'/Stream.srt',
                            user=self.one)[0] >= 400, 'native subtitle route exposed a filtered pointer track'
        return dict(tracks=len(tracks), ticketScoped=True, noStore=True, nativeSubtitleRefused=True)

    def arm(self, item, binding, start_ms, end_ms):
        """Publish one reviewed mark for the verified edition (as the review page would) and project it to the viewer."""
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
        if self.api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] != 1:
            raise fm.Unmeasured('reconcile did not produce one filtered item')
        self.pointer, _ = self.find_pointer(self.viewer)
        self.folders = {f['Name']: f['ItemId'] for f in self.api('/Library/VirtualFolders', user=self.admin)}
        p = self.api('/Users/'+self.viewer, user=self.admin)['Policy']
        p.update(EnableAllFolders=False, EnabledFolders=[self.folders['Movies'], self.folders['Playback samples '+self.viewer]])
        self.api('/Users/'+self.viewer+'/Policy', 'POST', p, user=self.admin)

    def measure(self, name, positions=(), stop_on_fail=True):
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
        original = self.api('/Items/'+item+'/PlaybackInfo', user=self.admin)['MediaSources'][0]
        source, audio_index = describe_source(original['Container'], original['MediaStreams'], tracks, binding['durationMs'])
        tuple_ = self.tuple_for(source, spec)
        now = datetime.now(timezone.utc)
        pending = dict(tuple=tuple_, actions=['cut'], controls=[], evidenceRef='tools/playback-lab/format-matrix.py',
                       date=now.strftime('%Y-%m-%d'), status='experimental',
                       observation='Measurement in progress: admits this layout so it can be measured; claims nothing.',
                       results={k: dict(outcome='not-measured', limit='Being measured by format-matrix.py.')
                                for k in ('markedPicture', 'markedSound', 'keptPictureAtJoin', 'keptSoundAtJoin')})
        self.put(self.register_path, json.dumps(dict(registerVersion=1, rows=[pending])).encode())
        self.cfg['LabSourceSha256'] = binding['sha256']
        self.update()
        try:
            self.arm(item, binding, av + ff.START*fm.FRAME_MS, av + ff.END*fm.FRAME_MS)
        except fm.Unmeasured as e:
            return dict(unmeasured=str(e), tuple=tuple_)
        url, projected, edl = self.offer(name, item)
        subtitle_routes = self.subtitle_routes(edl, url, projected, spec) if spec['subtitles'] else None
        probe = probe_json(self.fixtures, f"{name}.{spec['container']}")
        result = dict(tuple=tuple_, audioIndex=audio_index, projectedAudioIndex=projected.get('DefaultAudioStreamIndex'),
                      edl=redact(edl), form=form(spec))
        work = self.work/'capture'/name
        work.mkdir(parents=True)
        (work/'plan.json').write_text(json.dumps(ff.plan({'sha256': binding['sha256']})))
        runner = fm.Runner(work, network='container:'+self.cid, timeout=self.case_timeout, mounts=['-v', f'{self.fixtures}:/media:ro,z'])
        channels = spec['audio'][0][1]
        ref = None
        measured = {}
        try:
            # Each take re-asks PlaybackInfo, so a slow host cannot outlive a ticket.
            fresh = lambda: self.offer(name, item)[0]  # noqa: E731
            ref = fm.reference(runner, control_source=f"/media/{name}.{spec['container']}", probe=probe,
                               channels=channels, mpv_options=PLAYER_OPTIONS)

            def joint(options):
                # Another subtitle track needs its own uncut positive control (same sound, its own text).
                return fm.measure(runner, source=fresh, control_source=f"/media/{name}.{spec['container']}", probe=probe,
                                  channels=channels, plan_path=work/'plan.json', mpv_options=options,
                                  ref=ref if options == PLAYER_OPTIONS else None)

            measured = joint(PLAYER_OPTIONS)
            measured['seek'] = fm.seeks(runner, fresh(), PLAYER_OPTIONS)
            if spec['subtitles']:
                points = [(None, 0), (1.24, 31), (2.24, 56), (5.44, 136), (5.48, 263), (5.68, 268),
                          (10.12, 379), (11.20, 406), (1.56, 39)]
                keys = ('subtitles', 'subtitleTracks', 'subtitleClears')
                only_subs = lambda x: {k: x[k] for k in keys}  # noqa: E731
                measured['subtitleRoutes'] = subtitle_routes
                # First track must play automatically; each alternate is captured by sid and switched to after load
                # for the paused seeks. All are checked against an unedited positive control.
                takes = []
                for sid in range(1, len(spec['subtitles'])+1):
                    run = measured if sid == 1 else joint([*PLAYER_OPTIONS, f'--sid={sid}'])
                    seek = fm.seeks(runner, fresh(), PLAYER_OPTIONS, points, switch_sid=sid if sid > 1 else None)
                    takes.append(dict(control=only_subs(run['control']), straight=only_subs(run['straight']),
                                      starts=[only_subs(s) for s in run['starts']], subtitleSeek=seek,
                                      jointPassed=all(t['r22']['passed'] for t in [run['straight'], *run['starts']]),
                                      jointR22=[t['r22'] for t in [run['straight'], *run['starts']]],
                                      straightPicture=run['straight']['picture'], straightSound=run['straight']['sound']))
                measured['subtitleTakes'] = takes
                measured['subtitleChecks'] = check_subtitles(spec, measured)
        except (fm.Unmeasured, subprocess.TimeoutExpired) as e:
            measured['originalUnmeasured'] = redact(str(e))
        try:
            if ref is not None:
                measured['sweep'] = sweep(runner, fresh, ref, spec, positions, stop_on_fail,
                                          arm=lambda s, e, to_end: self.arm(item, binding, av + s,
                                                                            binding['durationMs'] if to_end else av + e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        measured.pop('control', None)
        return result | measured | (dict(control=ref['control']) if ref is not None else {})


def form(spec):
    """Mirror of PointerEvidence.Partitions: the tie-free split form for AC-3, E-AC-3 and FLAC sound."""
    return 'split' if ff.CODEC_NAME.get(spec['audio'][0][0], spec['audio'][0][0]) in ('ac3', 'eac3', 'flac') else 'joint'


def subtitle_leaks(spec, mark, zero_ms, position):
    """R19 at one cut position, default-selected track: no text touching the mark, every untouched cue shown."""
    if not spec['subtitles']:
        return []
    a, b = mark.start_ms + zero_ms, mark.end_ms + zero_ms
    cues = spec['subtitles'][0][2]
    forbidden = [t for start, end, t in cues if start <= b and end >= a]
    kept = [t for start, end, t in cues if not (start <= b and end >= a)]
    traces = (position['straight'].get('subtitles', []) +
              [x for s in position['starts'] for x in s.get('subtitles', [])] +
              [l['subtitle'] or '' for l in position.get('seek', {}).get('landings', [])])
    shown = [line.rsplit(' @', 1)[0] for line in position['straight'].get('subtitles', [])]
    problems = [f'forbidden subtitle text shown: {t}' for t in forbidden if any(t in line for line in traces)]
    problems += [f'kept subtitle not shown: {t}' for t in kept if t not in shown]
    return problems


def sweep(runner, source, ref, spec, positions, stop_on_fail, arm=None, seek=True):
    """Every cut position: arm the mark (plugin path) or write the cut list (local), then the straight cut, fresh
    starts around the join and paused exact seeks around it. Stops at the first failing position if asked."""
    out = []
    cut = ref['cut']
    av = cut['videoAnchorMs']
    for rel_start, rel_end in positions:
        to_end = rel_end >= cut['durationMs'] - av
        rel_end = min(rel_end, cut['durationMs'] - av)
        mark = fm.Mark(cut, av + rel_start, av + rel_end)
        try:
            if arm:
                # The lab probe and server edition can differ on the Matroska priming clock.
                arm(rel_start, rel_end, to_end)
            seeker = (lambda targets: fm.seeks(runner, source() if callable(source) else source, PLAYER_OPTIONS, targets,
                                               marked_frames=mark.marked_frames, resume_channels=ref['channels'])) if seek else None
            position = fm.sweep_position(runner, source, ref, mark, mpv_options=PLAYER_OPTIONS, seek=seeker)
            leaks = subtitle_leaks(spec, mark, cut['zeroMs'], position)
            issues = position['deviations'] + [deviation('subtitle-deviation', detail=text) for text in leaks]
            outcome = 'unmeasured' if position['unmeasured'] else 'pass' if position['passed'] and not leaks else 'fail'
            compact = dict(mark=position['mark'], result=outcome, deviations=issues, r22Takes=position['r22Takes'],
                           takes=len(position['r22Takes']), seekLandings=position.get('seek', {}).get('landings', []))
        except (fm.Unmeasured, subprocess.TimeoutExpired) as exc:
            compact = dict(mark=mark.label(), result='unmeasured', deviations=[deviation('unmeasured', detail=redact(str(exc)))])
        out.append(compact)
        print(f"    {compact['mark']}: {compact['result']} " +
              ', '.join(sorted({d['kind'] for d in compact['deviations']})), flush=True)
        if stop_on_fail and compact['result'] != 'pass':
            break

    return out


def local(layouts, fixtures, case_timeout, positions, stop_on_fail):
    results = {}
    for name in layouts:
        spec = ff.LAYOUTS[name]
        file = f"{name}.{spec['container']}"
        probe = probe_json(fixtures, file)
        cut = fm.plan_for(probe)
        split = form(spec) == 'split'
        (fixtures/f'{name}.plan.json').write_text(json.dumps(ff.plan({'sha256': '0'*64})))
        runner = fm.Runner(fixtures, timeout=case_timeout)
        print(f'{name}: measuring (local file, {form(spec)} cut list)', flush=True)
        edl = fixtures/f'{name}.edl'
        try:
            ref = fm.reference(runner, control_source=file, probe=probe, channels=spec['audio'][0][1], mpv_options=PLAYER_OPTIONS)
            edl.write_text(fm.edl_body(file, cut, split=split))
            r = fm.measure(runner, source=edl.name, control_source=file, probe=probe, channels=spec['audio'][0][1],
                           plan_path=fixtures/f'{name}.plan.json', mpv_options=PLAYER_OPTIONS, ref=ref)
            r['seek'] = fm.seeks(runner, edl.name, PLAYER_OPTIONS)

            def write(rel_start, rel_end, to_end):
                av = cut['videoAnchorMs']
                edl.write_text(fm.edl_body(file, dict(cut, startMs=av + rel_start,
                                                      endMs=cut['durationMs'] if to_end else av + rel_end), split=split))
            r['sweep'] = sweep(runner, edl.name, ref, spec, positions, stop_on_fail, arm=write)
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
    p.add_argument('--layout', action='append', choices=sorted(ff.LAYOUTS), help='repeat to pick layouts (default: R22 cohort)')
    p.add_argument('--hard-aac', action='store_true', help='include the original AAC hard fixture in a custom selection')
    p.add_argument('--aac', action='store_true', help='measure the R20 AAC encoder profiles (with the AAC-LC baseline)')
    p.add_argument('--local', action='store_true', help='plain files through mpv only; no Jellyfin, no plugin')
    p.add_argument('--case-timeout', type=int, default=600, help='seconds per mpv take; a timeout is "unmeasured"')
    p.add_argument('--receipt', type=Path, help='write the full receipt JSON here (default: print a summary only)')
    p.add_argument('--rows', type=Path, help='write candidate register rows (JSON list) here')
    p.add_argument('--positions', type=int, default=len(POSITIONS), help='sweep only the first N cut positions (diagnosis: a partial sweep never admits)')
    p.add_argument('--stop-on-fail', action='store_true', help='diagnosis only; a partial sweep never admits')
    p.add_argument('--keep-going', action='store_false', dest='stop_on_fail', help=argparse.SUPPRESS)
    p.set_defaults(stop_on_fail=False)
    args = p.parse_args()
    layouts = args.layout or (['mp4-h264-aac', *AAC_PROFILES] if args.aac else list(R22_LAYOUTS))
    if args.hard_aac and ff.HARD_AAC not in layouts:
        layouts.append(ff.HARD_AAC)
    scratch = ROOT/'plugin/obj'
    scratch.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='formats-', dir=scratch))
    fixtures = work/'movies'
    fixtures.mkdir()
    print(f'Generating {len(layouts)} synthetic layouts (pinned Jellyfin ffmpeg)...', flush=True)
    facts = {}
    for name in layouts:
        facts[name] = ff.generate(fixtures, name)
        if ff.CODEC_NAME.get(ff.LAYOUTS[name]['audio'][0][0], ff.LAYOUTS[name]['audio'][0][0]) == 'aac':
            # Does the default AAC track make the decoder draw random noise (PNS)? See aac_noise.py.
            facts[name]['decoderNoise'] = aac_noise.scan(fixtures, facts[name]['file'])
        print(f"  {name}: {facts[name]['sha256'][:16]}... {facts[name]['size']} bytes", flush=True)
    measurement_source = ('format-matrix.py', 'format_measure.py', 'format_fixtures.py',
                          'r22_oracle.py', 'r21_oracle.py', 'exact_audio.py')
    receipt = dict(admissionStandard=R22, thresholds=THRESHOLDS, method=fm.__doc__.strip().splitlines()[0],
                   harnessSha256={f: hashlib.sha256((LAB/f).read_bytes()).hexdigest() for f in measurement_source},
                   playerOptions=PLAYER_OPTIONS, deviceMode=DEVICE_MODE, plannedLayouts=layouts,
                   plannedPositions=POSITIONS, requestedPositionCount=min(args.positions, len(POSITIONS)),
                   fixtures={n: {k: f[k] for k in ('sha256', 'size', 'video', 'audio', 'subtitles', 'decoderNoise') if k in f}
                             for n, f in facts.items()}, runs={})
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    ok = True
    finished = set()
    evidence_ref = 'tools/playback-lab/format-matrix.py'
    if args.receipt and args.receipt.resolve().is_relative_to(ROOT):
        evidence_ref = args.receipt.resolve().relative_to(ROOT).as_posix()

    def persist():
        rows = [row for line, run in receipt['runs'].items() if line != 'local'
                for row in candidate_rows(run, evidence_ref, today, line in finished)] if args.rows else []
        for path, doc, indent in ((args.receipt, receipt, 1), (args.rows, rows, 2)):
            if path:
                pending = path.with_name(path.name + '.pending')
                pending.write_text(json.dumps(doc, indent=indent) + '\n')
                pending.replace(path)
    persist()
    try:
        if args.local:
            receipt['runs']['local'] = {n: dict(result=r, summary=summarize(n, r)) for n, r in local(layouts, fixtures, args.case_timeout, POSITIONS[:args.positions], args.stop_on_fail).items()}
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
                    persist()
                    for name in layouts:
                        print(f'[{line}] {name}: measuring through PlaybackInfo and the ticketed cut list', flush=True)
                        try:
                            result = lab.measure(name, POSITIONS[:args.positions], args.stop_on_fail)
                        except (fm.Unmeasured, RuntimeError, AssertionError, subprocess.SubprocessError, OSError, KeyError, ValueError) as e:
                            result = dict(unmeasured=redact(f'{type(e).__name__}: {e}')[:300])
                        summary = summarize(name, result)
                        run[name] = dict(result=result, summary=summary)
                        print(json.dumps(summary), flush=True)
                        persist()
                    cp.docker('stop', lab.cid)
                    lab.inspect()
                    finished.add(line)
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
        persist()
        print('\nLayout | ' + ' | '.join(receipt['runs']), flush=True)
        for name in layouts:
            print(name + ' | ' + ' | '.join(r.get(name, {}).get('summary', {}).get('result', 'unmeasured') for r in receipt['runs'].values()), flush=True)
        if ok:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
