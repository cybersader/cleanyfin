#!/usr/bin/env python3
"""One synthetic read-only original, one new isolated Jellyfin. Never accepts a server URL."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.dont_write_bytecode = True
from evidence_label import describe as describe_source
from exact_audio import compare_cut, read_pcm, summary
from hard_fixture_lab import DEFAULT_JELLYFIN, JELLYFIN

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
MPV = 'cleanyfin-edl-lab:mpv'
PLUGIN = 'b1f7c2e0-4a3d-4c9a-9f2b-1e6d5a8c0f11'
BASE = '/labbase'
REGISTER = ROOT/'delivery/evidence/register-v1.json'
USER = f'{os.getuid()}:{os.getgid()}'


def docker(*args, **kw):
    return subprocess.run(['docker', *map(str, args)], check=True, stdout=subprocess.PIPE, **kw).stdout


def check(label, passed):
    if not passed:
        raise RuntimeError(label)
    print('PASS', label, flush=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Lab:
    def __init__(self, work, jellyfin=DEFAULT_JELLYFIN):
        self.work = work
        # Server image, plugin JellyfinTarget and SDK always move together (one table row).
        self.jf = JELLYFIN[jellyfin]
        self.name = 'cleanyfin-auth-' + secrets.token_hex(6)
        self.cid = None
        self.port = 8096  # private loopback in a new --network none namespace
        self.origin = f'http://127.0.0.1:{self.port}{BASE}'
        self.tokens, self.slots = set(), set()
        self.build_disk = 0
        self.client, self.version = 'auth-range', '1'
        self.mounts = []  # extra read-only library mounts a case adds (e.g. an ordinary household folder)

    def setup(self):
        image, sdk = self.jf['image'], self.jf['sdk']
        for img in (image, sdk):
            docker('image', 'inspect', img)
        w = self.work
        for name in ('config/config', 'cache', 'plans', 'other', 'capture', 'home'):
            (w/name).mkdir(parents=True)
        for folder in ('plugin', 'delivery/Cleanyfin.Delivery', 'delivery/Cleanyfin.Marking', 'delivery/evidence'):
            shutil.copytree(ROOT/folder, w/'src'/folder, ignore=shutil.ignore_patterns('bin','obj'))
        # A pre-staged package source permits a completely networkless SDK build.
        feed = os.environ.get('CLEANYFIN_NUGET_FEED')
        sdk_options, build_options = [], []
        if feed:
            path = Path(feed).resolve(strict=True)
            if not path.is_dir():
                raise ValueError('CLEANYFIN_NUGET_FEED must be a local package directory')
            sdk_options = ['--network', 'none', '-v', f'{path}:/packages:ro,z']
            build_options = ['--source', '/packages', '-p:NuGetAudit=false']
        docker('run', '--rm', '--pull=never', '--user', USER, '-e', 'HOME=/out/home',
                       '-v', f'{w}:/out:z', '-w', '/out/src', *sdk_options, sdk,
                       'dotnet', 'build', 'plugin', '-c', 'Release', '-p:JellyfinTarget='+self.jf['target'],
                       '--artifacts-path', '/out/artifacts',
                       '-v:q', *build_options)
        install = w/'config/plugins/Cleanyfin_0.1.0.0'
        install.mkdir(parents=True)
        for dll in ('Jellyfin.Plugin.Cleanyfin.dll', 'Cleanyfin.Delivery.dll', 'Cleanyfin.Marking.dll'):
            src, = (w/'artifacts/bin').glob(f'Jellyfin.Plugin.Cleanyfin/release/{dll}')
            shutil.copyfile(src, install/dll)
        self.build_disk = sum(p.stat().st_blocks*512 for p in w.rglob('*') if p.is_file())
        for folder in ('home','artifacts','src'):
            shutil.rmtree(w/folder)
        self.plugin_build = sha((install/'Jellyfin.Plugin.Cleanyfin.dll').read_bytes())
        print('Plugin SHA-256', self.plugin_build, flush=True)
        subprocess.run([sys.executable, LAB/'hard-fixture.py', w/'fixture'], check=True, stdout=subprocess.DEVNULL)
        (w/'config/config/network.xml').write_text(f'''<NetworkConfiguration><BaseUrl>{BASE}</BaseUrl>
<InternalHttpPort>{self.port}</InternalHttpPort><PublicHttpPort>{self.port}</PublicHttpPort>
<LocalNetworkAddresses><string>127.0.0.1</string></LocalNetworkAddresses><EnableUPnP>false</EnableUPnP><EnableIPv6>false</EnableIPv6></NetworkConfiguration>''')
        # Stock 10.11.11 logs the viewer's raw native access token at Information on
        # Logout (SessionManager), even without this plugin. Leave all request categories
        # at defaults; don't produce those credential-bearing messages in the fixture.
        (w/'config/config/logging.json').write_text(json.dumps({'Serilog':{'MinimumLevel':{
            'Override':{'Emby.Server.Implementations.Session.SessionManager':'Warning'}}}}))
        self.cid = docker('create', '--pull=never', '--name', self.name, '--network', 'none',
                          '--user', USER,
                          '-v', f'{w}/config:/config:z', '-v', f'{w}/cache:/cache:z',
                          '-v', f'{w}/fixture/source.mp4:/fixture-library/source.mp4:ro,z',
                          '-v', f'{w}/other:/other:ro,z', '-v', f'{w}/plans:/cleanyfin-fixture:ro,z', *self.mounts, image).decode().strip()
        docker('start', self.cid)
        for _ in range(90):
            try:
                if self.request('/System/Info/Public')[0] == 200 and b'Startup complete' in docker('logs',self.cid,stderr=subprocess.STDOUT):
                    break
            except (OSError, subprocess.CalledProcessError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('disposable loopback Jellyfin unavailable')
        self.server_version = json.loads(self.request('/System/Info/Public')[2])['Version']
        check(f"server reports {self.jf['server']} (image pinned by digest)", self.server_version == self.jf['server'])

    def request(self, path, method='GET', data=None, user=None, headers=None):
        if path.startswith('http'):
            if not path.startswith(self.origin + '/'):
                raise RuntimeError('unexpected origin')
            path = path[len(self.origin):]
        # Container-local curl only: all requests go to the same isolated loopback as mpv.
        # No host publication, proxy, redirect-following, cookie jar or token-bearing argv.
        args = ['url = '+json.dumps(self.origin+path), 'head' if method=='HEAD' else 'request = '+method]
        h = dict(headers or {})
        if user and not any(k.lower() == 'authorization' for k in h):
            device, token = user
            h['Authorization'] = f'MediaBrowser Client="{self.client}", Device="fixture", DeviceId="{device}", Version="{self.version}"' + (f', Token="{token}"' if token else '')
        if data is not None:
            h['Content-Type'] = 'application/json'
            args.append('data-binary = '+json.dumps(data if isinstance(data, str) else json.dumps(data)))
        args.extend('header = '+json.dumps(k+': '+v) for k,v in h.items())
        raw = docker('exec','-i',self.cid,'curl','-q','-si','--noproxy','*','--max-time','20','--config','-',input=('\n'.join(args)+'\n').encode(),stderr=subprocess.DEVNULL)
        head,body=raw.split(b'\r\n\r\n',1); lines=head.decode('iso-8859-1').split('\r\n')
        return int(lines[0].split()[1]), {k.lower():v.strip() for k,v in (line.split(':',1) for line in lines[1:])}, body

    def api(self, path, *args, **kw):
        status, _, body = self.request(path, *args, **kw)
        if not 200 <= status < 300:
            # No query, credentials, bodies or capability-bearing paths in output.
            raise RuntimeError(f'fixture setup {path.split("?")[0]} status {status}')
        return json.loads(body) if body else None

    def users_library(self):
        api = self.api
        adminpw, p1, p2 = [secrets.token_urlsafe(24) for _ in range(3)]
        api('/Startup/Configuration', 'POST', {'UICulture': 'en-US', 'MetadataCountryCode':'US', 'PreferredMetadataLanguage':'en'})
        api('/Startup/User')
        api('/Startup/User', 'POST', {'Name':'admin', 'Password':adminpw})
        api('/Startup/RemoteAccess', 'POST', {'EnableRemoteAccess':True, 'EnableAutomaticPortMapping':False})
        api('/Startup/Complete', 'POST')

        def login(name, pw, device=None):
            device = device or name
            ans = api('/Users/AuthenticateByName', 'POST', {'Username':name,'Pw':pw}, user=(device,''))
            return (device, ans['AccessToken']), ans['User']['Id'].replace('-', '')
        self.passwords = {'admin': adminpw, 'one': p1, 'two': p2}
        self.admin, _ = login('admin', adminpw)
        for name, pw in [('one', p1), ('two', p2)]:
            api('/Users/New', 'POST', {'Name':name, 'Password':pw}, user=self.admin)
        for name, loc in [('Original', '/fixture-library'), ('Other', '/other')]:
            self.folder(name,loc)
        if self.client == 'auth-range':
            api('/Library/Refresh', 'POST', user=self.admin)
        else:
            folders = api('/Library/VirtualFolders',user=self.admin)
            self.refresh(next(f['ItemId'] for f in folders if f['Name']=='Original'))
        for _ in range(60):
            items = api('/Items?Recursive=true&IncludeItemTypes=Video&Fields=Path,MediaSources', user=self.admin)['Items']
            match = [i for i in items if i['Path'] == '/fixture-library/source.mp4' and i.get('MediaSources')]
            if match: break
            time.sleep(1)
        else: raise RuntimeError('original not indexed')
        self.item = match[0]['Id'].replace('-', '')
        folders = {f['Name']:f['ItemId'] for f in api('/Library/VirtualFolders', user=self.admin)}
        self.one, self.viewer = login('one', p1)
        if isinstance(self, EligibilityLab): self.relogin = lambda device: login('one', p1, device)[0]
        self.two, viewer2 = login('two', p2)
        self.viewer2 = viewer2
        self.other_device, _ = login('one', p1, 'unenrolled')
        for uid, folder in [(self.viewer, 'Original'), (viewer2, 'Other')]:
            policy = api('/Users/'+uid, user=self.admin)['Policy']
            policy.update(EnableAllFolders=False, EnabledFolders=[folders[folder]])
            api(f'/Users/{uid}/Policy', 'POST', policy, user=self.admin)
        assert not api('/Items?Recursive=true&IncludeItemTypes=Video', user=self.two)['Items']

    def folder(self, name, loc):
        self.api('/Library/VirtualFolders?' + urlencode({'name':name,'collectionType':'homevideos','refreshLibrary':'false'}), 'POST',
            {'LibraryOptions':{'PathInfos':[{'Path':loc}], 'EnableRealtimeMonitor':False, 'EnableChapterImageExtraction':False,
                               'EnableTrickplayImageExtraction':False, 'EnableInternetProviders':False,
                               'TypeOptions':[{'Type':'Video','ImageFetchers':[], 'ImageFetcherOrder':[],
                                               'ImageOptions':[{'Type':'Primary','Limit':0}]}]}}, user=self.admin)

    def refresh(self, item):
        self.api('/Items/'+item+'/Refresh?Recursive=true&MetadataRefreshMode=FullRefresh&ImageRefreshMode=None', 'POST', user=self.admin)

    def configure(self):
        w = self.work
        self.original = (w/'fixture/source.mp4').read_bytes()
        lab = json.loads((w/'fixture/plan.json').read_bytes())
        assert sha(self.original) == lab['source']['sha256'] and lab['cuts'][0]['muxedVideo']['timeBase'] == '1/12800'
        plan = json.loads((ROOT/'tests/Delivery.Contract.Tests/Fixtures/hard-fixture.v1.json').read_bytes())
        now = datetime.now(timezone.utc)
        for k, dt in [('issuedAt', now-timedelta(seconds=3)), ('expiresAt', now+timedelta(minutes=30))]:
            plan[k] = dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        plan['source'].update(serverRef='auth-range', itemId=self.item, mediaSourceId=self.item, sizeBytes=len(self.original), sha256=sha(self.original))
        plan['authority']['viewerRef'] = self.viewer
        raw = json.dumps(plan).encode(); (w/'plans/plan.json').write_bytes(raw)
        self.configurl = '/Plugins/'+PLUGIN+'/Configuration'
        self.cfg = self.api(self.configurl, user=self.admin)
        self.cfg.update(EnableAuthRangeLab=True, EnableLegacyRangeFixture=True, LabOrigin=self.origin, LabItemId=self.item, LabViewerId=self.viewer, LabDeviceId=self.one[0],
                        LabSourceSha256=sha(self.original), LabPlanSha256=sha(raw), LabLifetimeSeconds=300)
        self.update()

    def update(self):
        self.api(self.configurl, 'POST', self.cfg, user=self.admin)

    def offer(self):
        status, h, raw = self.request('/Cleanyfin/lab/issue','POST',user=self.one)
        assert status == 200 and h.get('cache-control') == 'private, no-store', 'non-admin issuance'
        return self.verify_offer(json.loads(raw)['ListUrl'])

    def verify_offer(self, url, start=5480, end=10520):
        m = re.fullmatch(re.escape(self.origin)+r'/Cleanyfin/play/([0-9a-f]{32})/list\.edl\?t=([0-9a-f]{32})', url)
        assert m, 'list URL format'
        slot, a = m.groups(); self.slots.add(slot); self.tokens.add(a)
        s, h, edl = self.request(url)
        assert s == 200 and h['content-type'] == 'text/plain; charset=utf-8' and h['cache-control'] == 'private, no-store', f'descriptor status {s}'
        assert int(h['content-length']) == len(edl)
        status, hh, data = self.request(url,'HEAD'); assert status == 200 and not data and int(hh['content-length']) == len(edl)
        lines=edl.decode().splitlines(); assert lines.pop(0)=='# mpv EDL v0'
        if isinstance(self,PointerLab): assert lines.pop(0)=='!no_chapters'
        first,second=lines
        m=re.fullmatch(r'%(\d+)%([^,]+),0\.000,'+re.escape(f'{start/1000:.3f}'),first); assert m
        n,source=m.groups(); assert len(source.encode())==int(n) and second==f'%{n}%{source},{end/1000:.3f},{(20000-end)/1000:.3f}'
        m = re.fullmatch(re.escape(self.origin)+f'/Cleanyfin/play/{slot}/source'+r'\?t=([0-9a-f]{32})', source); assert m
        self.tokens.add(m[1]); return url, source

    def denied(self, path, **kw):
        s, _, data = self.request(path, **kw)
        return s in (401,403,404,410) and not data

    def exercise(self):
        self.users_library()
        issue = '/Cleanyfin/lab/issue'
        check('issuance: flag off and anonymous denied', self.denied(issue, method='POST',user=self.one) and self.request(issue,'POST')[0] in (401,403))
        self.configure()
        check('library-denied viewer refused', self.denied(issue,method='POST',user=self.two) and self.request(issue,'POST')[0] in (401,403))
        check('unenrolled device refused', self.denied(issue, method='POST', user=self.other_device))
        self.cfg['LabLifetimeSeconds'] = 2; self.update()
        short = self.offer(); time.sleep(2.1)
        check('expired list and range refused', self.denied(short[0]) and self.denied(short[1],headers={'Range':'bytes=0-99'}))
        self.cfg['LabLifetimeSeconds'] = 300; self.update()
        url, src = self.offer()
        check('non-admin list GET/HEAD, same-origin EDL, exact kept ranges', True)
        original = self.original; size = len(original)
        s,h,data = self.request(src)
        check('headerless full GET: original SHA-256', s==200 and sha(data)==sha(original) and h.get('cache-control')=='private, no-store')
        validator = h['last-modified']
        for label, spec, start, stop, extra in [('prefix','0-99',0,100,{}),('middle','251-411',251,412,{}),('suffix','-113',size-113,size,{}),('If-Range match','0-99',0,100,{'If-Range':validator})]:
            s,h,data=self.request(src,headers={'Range':'bytes='+spec,**extra})
            check(label+' 206', s==206 and h.get('content-range')==f'bytes {start}-{stop-1}/{size}' and data==original[start:stop])
        s,h,data=self.request(src,headers={'Range':'bytes=0-99','If-Range':'Sat, 01 Jan 2000 00:00:00 GMT'})
        check('If-Range stale: full 200', s==200 and data==original)
        s,h,data=self.request(src,headers={'Range':f'bytes={size}-'})
        check('unsatisfiable 416',s==416 and h.get('content-range')==f'bytes */{size}' and not data)
        s,h,data=self.request(src,'HEAD'); check('source HEAD',s==200 and not data and int(h['content-length'])==size)
        check('purpose separation', self.denied(url.split('?')[0]+'?'+src.split('?')[1]) and self.denied(src.split('?')[0]+'?'+url.split('?')[1]))
        check('tampered, truncated and unknown denied',all(self.denied(x) for u in (url,src) for x in [u[:-1]+('0' if u[-1]!='0' else '1'),u[:-1],u.split('=')[0]+'='+secrets.token_hex(16)]))
        check('conflicting viewer denied on both legs',self.denied(url,user=self.two) and self.denied(src,user=self.two))
        native=f'/Videos/{self.item}/stream?static=true&mediaSourceId={self.item}'
        info=self.api('/Items/'+self.item+'/PlaybackInfo',user=self.one)
        check('normal PlaybackInfo and native static playback', info['MediaSources'][0]['Id']==self.item and self.request(native,user=self.one)[2]==original)
        self.player(url)
        planfile=self.work/'plans/plan.json'; raw=planfile.read_bytes(); planfile.write_bytes(raw+b' ')
        changed=self.denied(src,headers={'Range':'bytes=0-99'}); planfile.write_bytes(raw)
        check('changed plan invalidates both tickets', changed and self.denied(url))
        url,src=self.offer()
        user=self.api('/Users/'+self.viewer,user=self.admin); policy=user['Policy']; allowed=policy['EnabledFolders']; policy['EnabledFolders']=[]
        self.api('/Users/'+self.viewer+'/Policy','POST',policy,user=self.admin)
        denied=self.denied(url) and self.denied(src,headers={'Range':'bytes=1-2'}); policy['EnabledFolders']=allowed
        self.api('/Users/'+self.viewer+'/Policy','POST',policy,user=self.admin)
        check('library removal revokes outstanding offer',denied and self.denied(src))
        url,src=self.offer()
        assert self.request(src,headers={'Range':'bytes=0-99'})[2] == original[:100]
        self.api('/Sessions/Logout','POST',user=self.one)
        check('logout: list, subsequent range and conditional HEAD denied', self.denied(url) and self.denied(src,headers={'Range':'bytes=100-199'}) and self.denied(src,method='HEAD',headers={'If-Modified-Since':validator}))

    def mpv(self,source,mounts,driver):
        docker('run','-i','--rm','--pull=never','--network','container:'+self.cid,'--user',USER,'-e','HOME=/tmp',*mounts,
                       '--entrypoint','sh',MPV,'-c','IFS= read -r src; exec mpv --no-config --no-terminal --load-scripts=no --hwdec=no --audio-file-auto=no --sub-auto=no --framedrop=no --hr-seek=yes --untimed "$@" -- "$src"','mpv',*driver,
                       input=(source+'\n').encode(),stderr=subprocess.DEVNULL,timeout=120)

    def capture_av(self, label, source, *, local=False):
        capture = self.work/'capture'
        frames, wav = capture/(label+'-frames'), capture/(label+'.wav')
        frames.mkdir()
        mounts = ['-v', f'{capture}:/capture:z']
        if local:
            mounts += ['-v', f'{self.work}/fixture/source.mp4:/source.mp4:ro,z']
        # Both tracks in one playback, in the same isolated network namespace.
        # Supply URL through stdin, not docker create args, mpv logs or a playlist file.
        self.mpv(source, mounts, ('--vo=image', '--vf=scale=160:90', '--vo-image-format=png',
                 f'--vo-image-outdir=/capture/{frames.name}', '--ao=pcm', '--ao-pcm-waveheader=yes',
                 f'--ao-pcm-file=/capture/{wav.name}', '--audio-samplerate=48000',
                 '--audio-channels=stereo', '--audio-format=s16'))
        return frames, wav

    def score_av(self, plan, frames, wav=None):
        args = ['--audio', wav] if wav is not None else []
        result = subprocess.run([sys.executable, LAB/'analyze-capture.py', '--plan', plan, '--video',
                                 frames, '--json', *args], capture_output=True, text=True, timeout=120)
        lines = result.stdout.splitlines()
        report = json.loads(lines[-1])
        if 'error' in report:
            raise RuntimeError(report['error'])
        return report, lines[:-2]

    def check_pcm(self, label, plan, wav):
        report = compare_cut(plan, self.control_pcm, read_pcm(wav))
        check(f'{label}: {summary(report)}', report['exact'])

    def player(self, url):
        docker('image','inspect',MPV)
        plan = self.work/'fixture/plan.json'
        frames, wav = self.capture_av('control', '/source.mp4', local=True)
        metrics, _ = self.score_av(plan, frames, wav)
        v, a = metrics['video'], metrics['audio']
        check('mpv positive control: 500 frames, 126 marked and marked tone',
              v['frames']==500 and v['markedCount']==126 and not v['missingCount']
              and not v['unreadableCount'] and not v['duplicateCount'] and v['monotonic']
              and abs(a['leakMs']-5040)<=5)
        self.control_pcm = read_pcm(wav)
        frames, wav = self.capture_av('cut', url)
        metrics, detail = self.score_av(plan, frames, wav)
        print('\n'.join(detail), flush=True)
        v, a = metrics['video'], metrics['audio']
        check('mpv handoff: all 374 kept, zero marked frames or detected tone',
              v['pass'] and v['frames']==374 and not v['duplicateCount'] and a['leakMs']==0)
        self.check_pcm('mpv handoff joint PCM', json.loads(plan.read_text()), wav)
        check('mpv handoff tone-window cross-check', a['pass'])

    def inspect(self):
        log=docker('logs',self.cid,stderr=subprocess.STDOUT)
        secrets_seen=[s.encode() for s in self.tokens|self.slots]
        outputs=[]; blobs=[log]
        for tree in ('config','cache'):
            for p in (self.work/tree).rglob('*'):
                if not p.is_file(): continue
                raw=p.read_bytes(); blobs.append(raw)
                mime = mimetypes.guess_type(p.name)[0] or ''
                if (mime.startswith(('video/', 'audio/', 'image/')) or p.suffix.lower() in {'.ts','.m4s','.m3u8','.srt','.ass','.ssa','.vtt','.sup','.sub','.idx'}
                        or raw[4:8] == b'ftyp' or raw.startswith((b'\x1aE\xdf\xa3', b'\xff\xd8\xff', b'\x89PNG', b'RIFF'))
                        or self.original in raw):
                    outputs.append(p)
        native_tokens = [u[1].encode() for u in (self.admin,self.one,self.two,self.other_device)]
        log_files = [p.read_bytes() for p in (self.work/'config/log').rglob('*') if p.is_file()]
        check('server logs/config/cache: no grant values, copies or transcodes',not outputs and not any(re.search(rb'/Cleanyfin/play/[a-f0-9]{32}/(?:list\.edl|source|subtitle-\d+\.(?:srt|ass))\?t=[a-f0-9]{32}',blob,re.I) for blob in blobs) and not any(s in blob for s in secrets_seen for blob in blobs) and not any(s in blob for s in native_tokens+[t.encode() for t in getattr(self,'extra_tokens',[])] for blob in [log,*log_files]))
        print('Default HTTP logging, SessionManager=Warning: refusal-code lines',log.count(b'Auth-range lab refused:'),'ticket-route lines',log.lower().count(b'/cleanyfin/play/'),flush=True)


class PointerLab(Lab):
    shelf = '/config/cleanyfin-lab/shelf'
    profile = {'Name':'mpv', 'DirectPlayProfiles':[{'Type':'Video','Container':'mp4','VideoCodec':'h264','AudioCodec':'aac'}],
               'TranscodingProfiles':[{'Type':'Video','Context':'Streaming','Protocol':'hls','Container':'ts','VideoCodec':'h264','AudioCodec':'aac'}]}

    def __init__(self, work, jellyfin=DEFAULT_JELLYFIN):
        super().__init__(work, jellyfin)
        self.seed = json.loads(REGISTER.read_bytes())
        self.wholphin, = [r for r in self.seed['rows'] if r['tuple']['appId']=='Wholphin' and r['tuple']['backend']=='mpv']
        self.client,self.version = (self.wholphin['tuple'][k] for k in ('appId', 'appVersion'))
        self.name='cleanyfin-pointer-'+secrets.token_hex(6)

    def shelf_access(self, original):
        policy=self.api('/Users/'+self.viewer,user=self.admin)['Policy']
        policy.update(EnableAllFolders=False,EnabledFolders=[self.folders['Shelf']]+([self.folders['Original']] if original else []))
        self.api('/Users/'+self.viewer+'/Policy','POST',policy,user=self.admin)

    @staticmethod
    def put(path, raw):
        path.parent.mkdir(exist_ok=True,parents=True); tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);os.replace(tmp,path)

    def policy(self, uid, device, categories):
        root=self.data/'profiles'/uid; self.put(root/'device.txt',device.encode())
        self.enroll_evidence(uid)
        doc={'policyVersion':1,'profileRef':uid,'policyRev':'policy:7','trustRev':'trust:2',
             'rules':[{'category':cat,'minimumSeverity':1,'filter':True} for cat in categories]}
        self.put(root/'policy.json',json.dumps(doc).encode())

    def enroll_evidence(self, uid):
        row = self.wholphin['tuple']
        self.put(self.data/'profiles'/uid/'evidence-device.txt', f"{row['appBuild']}\n{row['deviceMode']}\n".encode())

    def measured_source(self, tracks=None, duration_ms=20000):
        """The evidence source this build must derive for the original (independent Python mirror of the plugin)."""
        src = self.api('/Items/'+self.item+'/PlaybackInfo', user=self.admin)['MediaSources'][0]
        tracks = tracks or [dict(index=0, kind='video', timeBase=[1, 12800], anchorSourceMs=0),
                            dict(index=1, kind='audio', timeBase=[1, 48000], anchorSourceMs=0)]
        return describe_source(src['Container'], src['MediaStreams'], tracks, duration_ms)[0]

    def stub_register(self, source=None):
        # This new build did not make the historical capture. Use an explicitly unmeasured,
        # disposable row to exercise the gate, never rewrite the historical evidence.
        row = json.loads(json.dumps(self.wholphin))
        row['tuple']['source'] = source or self.measured_source()
        row['tuple']['adapterRevision'] = '2'  # MpvEdlRenderer.AdapterRevision
        row['tuple']['pluginBuild'] = self.plugin_build
        # Exercise the gate on the server under test; the historical row names 10.11.11 only.
        row['tuple']['jellyfinVersion'] = self.server_version
        row['observation'] = 'Synthetic integration-test setup only; no claims about playback fidelity on this build.'
        row['evidenceRef'] = 'tools/playback-lab/check-plugin.py'
        row['controls'] = []
        for result in row['results'].values():
            result.update(outcome='not-measured',limit='Only the offer gate is exercised; no presentation measurement.')
        return dict(registerVersion=1, rows=[row])

    def inventory(self):
        return json.loads((self.data/'inventory.json').read_text())

    def find_pointer(self, uid):
        row,=[r for r in self.inventory() if r['Viewer']==uid]; key=row['Key']
        for _ in range(40):
            items=self.api('/Items?Recursive=true&IncludeItemTypes=Video&Fields=Path',user=self.admin)['Items']
            hit=[i for i in items if i.get('Path','').endswith('/Playback sample '+key+'.strm') and i.get('RunTimeTicks')==row['Ticks']]
            if len(hit)==1:return hit[0]['Id'].replace('-',''),row
            time.sleep(.25)
        raise RuntimeError('owned pointer not indexed with filtered runtime')

    def setup_pointer(self, *, stub_evidence=True):
        self.users_library();self.original=(self.work/'fixture/source.mp4').read_bytes()
        self.configurl='/Plugins/'+PLUGIN+'/Configuration';self.cfg=self.api(self.configurl,user=self.admin)
        self.data=self.work/'config/plugins/configurations/cleanyfin-shelves-v1'
        self.snapshot=self.data/'snapshots'/(self.item+'.json')
        self.register_path = self.data/'evidence-register-v1.json'
        if stub_evidence:
            self.put(self.register_path, json.dumps(self.stub_register()).encode())
        doc=json.loads((ROOT/'tests/Delivery.Contract.Tests/Fixtures/hard-fixture.snapshot.v1.json').read_bytes())
        now=datetime.now(timezone.utc)
        for k,t in [('acceptedAt',now-timedelta(minutes=1)),('expiresAt',now+timedelta(hours=2))]:doc[k]=t.strftime('%Y-%m-%dT%H:%M:%SZ')
        doc['binding'].update(serverRef=self.api('/System/Info/Public')['Id'],itemId=self.item,mediaSourceId=self.item,sizeBytes=len(self.original),sha256=sha(self.original))
        if isinstance(self,ShelfLab):
            # Independent, frame-exact witnesses: A excludes [137,263), B excludes [137,200).
            doc['annotations']=[self.mark('one','violence','skip',5480,8000),self.mark('two','gore','skip',8000,10520),self.mark('three','profanity','mute',1000,2000)]
            self.policy(self.viewer2,self.two[0],['violence'])
        self.policy(self.viewer,self.one[0],['violence','gore']);self.put(self.snapshot,json.dumps(doc).encode())
        self.cfg.update(EnableAuthRangeLab=True,LabOrigin=getattr(self,'public_origin',self.origin),LabPointerKey=secrets.token_hex(16),LabLifetimeSeconds=300,LabSourceSha256=sha(self.original));self.update()
        self.api('/Cleanyfin/lab/reconcile','POST',user=self.admin)
        self.pointer,row=self.find_pointer(self.viewer);self.locator='/Cleanyfin/pointer/'+row['Key']
        self.folders={f['Name']:f['ItemId'] for f in self.api('/Library/VirtualFolders',user=self.admin)}
        self.folders['Shelf']=self.folders['Playback samples '+self.viewer];self.shelf_access(True)
        self.body={'DeviceProfile':self.profile,'MediaSourceId':self.pointer,'MaxStreamingBitrate':100000000,'AutoOpenLiveStream':True}
        hidden=self.api('/Items?Recursive=true&IncludeItemTypes=Video',user=self.two)['Items']
        check('enrolled viewer sees distinct pointer',self.pointer!=self.item and all(i['Id'].replace('-','')!=self.pointer for i in hidden))

    @staticmethod
    def mark(ref,cat,action,start,end):
        content=f'annotation-v1|{ref}|{cat}|2|{action}|{start}|{end}'.encode()
        return dict(ref=ref,category=cat,severity=2,action=action,startMs=start,endMs=end,contentSha256=sha(content))

    def browse(self):
        item=self.api('/Users/'+self.viewer+'/Items/'+self.pointer+'?Fields=MediaSources',user=self.one)
        return item.get('RunTimeTicks')==149600000 and [s['Codec'] for s in item.get('MediaStreams',[])]==['h264','aac']

    def posted(self,body=None):
        return self.api('/Items/'+self.pointer+'/PlaybackInfo','POST',body or self.body,user=self.one)

    def projection(self, body=None):
        info=self.posted(body);sources=info.get('MediaSources',[])
        if len(sources)!=1:raise RuntimeError('one direct-play source (got '+str(len(sources))+')')
        s=sources[0];assert s['Protocol']=='Http' and s['IsRemote'] and s['Id']==self.pointer and s['SupportsDirectPlay'] and not s['SupportsDirectStream'] and not s['SupportsTranscoding'] and not s.get('TranscodingUrl') and not s['RequiresOpening'] and s['RunTimeTicks']==149600000 and s['Container']=='mp4','direct-only filtered source'
        facts='Index Type Codec Profile Level Width Height AverageFrameRate RealFrameRate BitRate SampleRate Channels ChannelLayout IsDefault'.split()
        original=self.api('/Items/'+self.item+'/PlaybackInfo',user=self.one)['MediaSources'][0]
        rows=s['MediaStreams']; assert len(rows)==2 and all([r.get(k) for k in facts]==[o.get(k) for k in facts] and not r.get('Path') and not r.get('DeliveryUrl') for r,o in zip(rows,original['MediaStreams'])),'whitelisted original stream facts'
        assert self.item not in json.dumps(info) and self.one[1] not in json.dumps(info),'original identity exposed'
        return self.verify_offer(s['Path'])

    def check_wholphin_containers(self):
        # Replay the relevant fields observed on the real TV POST: explicit
        # selection, audio index, unbounded channels, native MPV flags and both
        # MP4 spellings. The synthetic legacy POST below only exercised mp4.
        def request(container):
            direct = [dict(self.profile['DirectPlayProfiles'][0], Container=container)]
            return dict(self.body, DeviceProfile=dict(self.profile, DirectPlayProfiles=direct),
                        AudioStreamIndex=1, MaxStreamingBitrate=104857600,
                        EnableDirectPlay=True, EnableDirectStream=True, EnableTranscoding=True)
        observed=request('mov,mp4')
        native=self.api('/Items/'+self.item+'/PlaybackInfo','POST',dict(observed,MediaSourceId=self.item),user=self.one)
        check('Wholphin-shaped original PlaybackInfo negotiates mov',len(native['MediaSources'])==1 and native['MediaSources'][0]['Container']=='mov' and native['MediaSources'][0]['SupportsDirectPlay'])
        self.projection(observed)
        self.projection(request('mov'))
        check('Wholphin-shaped and mov-only profiles get the pinned filtered source',True)
        refused=self.posted(request('mkv,matroska'))
        check('Matroska-only profile still gets NoCompatibleStream for the same pointer',refused.get('ErrorCode')=='NoCompatibleStream' and refused['MediaSources']==[])

    def original_shape(self):
        status,_,body=self.request('/Items/'+self.item+'/PlaybackInfo','POST',{'DeviceProfile':self.profile},user=self.one)
        assert status==200 and self.item.encode() in body
        return re.sub(rb'("PlaySessionId"\s*:\s*")[0-9a-f]{32}',rb'\g<1>SESSION',body)

    def exercise(self):
        self.setup_pointer()
        def probe_count(): return docker('logs',self.cid,stderr=subprocess.STDOUT).count(b'/Cleanyfin/pointer/{id} 410')
        count=probe_count()
        probe=subprocess.run(['docker','exec',self.cid,'/usr/lib/jellyfin-ffmpeg/ffprobe','-v','error','-show_streams',self.origin+self.locator],capture_output=True,timeout=10)
        check('inert locator: anonymous, viewer, admin, probe',probe.returncode!=0 and probe_count()>count and not probe.stdout and all(self.request(self.locator,user=u)[0] in (404,410) for u in (None,self.one,self.admin)))
        check('browse: filtered runtime and tracks',self.browse())
        url,source=self.projection();check('one HTTP direct-play list; filtered runtime; H.264/AAC',True)
        self.check_wholphin_containers()
        self.player(url)
        original=self.original_shape()
        uri='/Items/'+self.pointer+'/PlaybackInfo'
        check('GET, Exo, incompatible codec and low bitrate refused',not self.api(uri,user=self.one)['MediaSources'] and all(not self.posted(dict(self.body,**delta))['MediaSources'] for delta in [ {'DeviceProfile':dict(self.profile,Name='ExoPlayer')},{'DeviceProfile':dict(self.profile,DirectPlayProfiles=[{'Type':'Video','Container':'mp4','VideoCodec':'hevc','AudioCodec':'aac'}])},{'MaxStreamingBitrate':1}]))
        self.cfg['LabWithholdProjection']=True;self.update();check('withheld streams: no source',not self.posted()['MediaSources']);self.cfg['LabWithholdProjection']=False;self.update()
        self.cfg['LabLifetimeSeconds']=2;self.update();short=self.projection();time.sleep(2.1)
        check('expired pointer list and range refused',self.denied(short[0]) and self.denied(short[1],headers={'Range':'bytes=0-99'}))
        self.cfg['LabLifetimeSeconds']=300;self.update()
        self.shelf_access(False);check('original ACL denied: no source',not self.posted()['MediaSources']);self.shelf_access(True)
        before=self.refresh_count()
        self.refresh(self.folders['Shelf']); self.refresh(self.pointer)
        # Native refresh queues work: wait for the pointer provider to actually run again.
        self.wait_refresh(before)
        url,_=self.projection();check('refresh preserves browse and playback',self.browse())
        docker('restart',self.cid)
        for _ in range(90):
            try:
                if self.request('/System/Info/Public')[0]==200:break
            except subprocess.CalledProcessError:pass
            time.sleep(.5)
        fresh=self.projection()
        check('restart revokes old list; fresh projection survives',self.denied(url) and self.browse() and fresh[0]!=url)
        self.generic_routes()
        self.cfg['EnableAuthRangeLab']=False;self.update();check('flag off: no source',not self.posted()['MediaSources'] and self.denied(fresh[0]) and self.denied(fresh[1]))
        check('ordinary PlaybackInfo unchanged except session nonce',self.original_shape()==original)
        self.generic_routes()
        check('generic stream and transcode do not deliver media',True)
        # Invalid inputs may retire the item at the next reconcile. Test this *after* stable-ID refresh.
        self.cfg['EnableAuthRangeLab']=True;self.update();active=self.projection();self.put(self.snapshot,b'{}')
        status,_,raw=self.request(uri,'POST',self.body,user=self.one)
        check('changed plan: no source; active tickets revoked', (status==404 or (status==200 and json.loads(raw)['MediaSources']==[])) and all(self.denied(leg) for leg in active))

    def generic_routes(self):
        for path in ['/Videos/'+self.pointer+'/stream.mp4?static=false','/Videos/'+self.pointer+'/master.m3u8']:
            status,_,data=self.request(path,user=self.one)
            # A master manifest alone is not a transcode. Request its child as well, bounded to the same pointer.
            if status==200 and data.startswith(b'#EXTM3U'):
                from urllib.parse import urljoin,urlsplit
                child=next((x for x in data.decode().splitlines() if x and not x.startswith('#')),None)
                assert child,'empty master manifest'
                target=urlsplit(urljoin(self.origin+path,child)); assert target.netloc==urlsplit(self.origin).netloc and self.pointer in target.path and target.path.startswith(BASE+'/Videos/'),'off-target native playlist'
                status,_,data=self.request(target.path[len(BASE):]+'?'+target.query,user=self.one)
            assert status>=400 and self.original[:32] not in data,'generic route returned playable content'

    def refresh_count(self):
        return docker('logs',self.cid,stderr=subprocess.STDOUT).count(b'Fixture pointer metadata checked')

    def wait_refresh(self,before):
        for _ in range(60):
            if self.refresh_count()>before and self.browse():return
            time.sleep(.5)
        raise RuntimeError('pointer projection lost on refresh')


def missing_eligible_evidence(register):
    # Review tripwire only, not attestation: an existing path does not prove its contents.
    missing = []
    for row in register['rows']:
        if row['status'] == 'eligible':
            path = (ROOT/row['evidenceRef']).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                missing.append(row['evidenceRef'])
    return missing


class EligibilityLab(PointerLab):
    def play(self, body=None, headers=None):
        return self.request('/Items/'+self.pointer+'/PlaybackInfo', 'POST', self.body if body is None else body,
                            user=self.one, headers=headers)

    def refusal(self, reason, **kw):
        before = self.logs().count(f'Fixture projection refused: {reason}'.encode())
        status, headers, body = self.play(**kw)
        payload = json.loads(body)
        return (status == 200 and payload.get('MediaSources') == [] and payload.get('ErrorCode') == 'NoCompatibleStream'
                and headers.get('x-cleanyfin-reason') == reason and 'x-cleanyfin-evidence' not in headers
                and self.logs().count(f'Fixture projection refused: {reason}'.encode()) > before)

    def logs(self):
        return docker('logs', self.cid, stderr=subprocess.STDOUT)

    def identity_refusal(self, client, version):
        original = self.client, self.version
        device = self.one[0]
        try:
            self.client, self.version = client, version
            self.extra_tokens.append(self.one[1])
            self.one = self.relogin(device)
            return self.refusal('EvidenceTupleUnknown')
        finally:
            self.client, self.version = original
            self.extra_tokens.append(self.one[1])
            self.one = self.relogin(device)

    def exercise(self):
        self.setup_pointer(stub_evidence=False)
        check('missing reviewed register: no offer', self.refusal('EvidenceRegisterInvalid'))
        self.put(self.register_path, REGISTER.read_bytes())
        check('historical plugin build differs: no offer', self.refusal('EvidenceTupleUnknown'))
        stub = self.stub_register()
        # The same synthetic row naming any other server version must not admit this server.
        foreign = json.loads(json.dumps(stub))
        foreign['rows'][0]['tuple']['jellyfinVersion'] = next(j['server'] for j in JELLYFIN.values() if j['server'] != self.server_version)
        self.put(self.register_path, json.dumps(foreign).encode())
        check('row for another Jellyfin version: EvidenceTupleUnknown', self.refusal('EvidenceTupleUnknown'))
        self.put(self.register_path, json.dumps(stub).encode())
        status, headers, raw = self.play()
        sources = json.loads(raw).get('MediaSources', [])
        check('lab flag: exact synthetic Wholphin offer marked experimental', status == 200 and len(sources) == 1
              and headers.get('x-cleanyfin-evidence') == 'experimental' and sources[0].get('Name') == 'Cleanyfin sample (experimental)')
        self.verify_offer(sources[0]['Path'])
        self.extra_tokens = []
        check('changed Wholphin version: EvidenceTupleUnknown', self.identity_refusal(self.client, '1.1.3'))
        check('stock Android TV: EvidenceTupleUnknown', self.identity_refusal('Jellyfin Android TV', '0.20.0'))
        check('unknown client: EvidenceTupleUnknown', self.identity_refusal('Unknown Player', '1'))
        check('changed backend: EvidenceTupleUnknown', self.refusal('EvidenceTupleUnknown',
              body=dict(self.body, DeviceProfile=dict(self.profile, Name='ExoPlayer'))))
        check('token without current app claims: EvidenceIdentityUnavailable', self.refusal('EvidenceIdentityUnavailable',
              headers={'Authorization':f'MediaBrowser Token="{self.one[1]}"'}))
        enrollment = self.data/'profiles'/self.viewer/'evidence-device.txt'
        enrollment.unlink()
        check('missing output enrollment: EvidenceIdentityUnavailable', self.refusal('EvidenceIdentityUnavailable'))
        self.enroll_evidence(self.viewer)
        urls = self.projection()
        self.put(self.register_path, b'{"registerVersion":1,"rows":[],"unknown":1}')
        check('invalid register: EvidenceRegisterInvalid with UnknownField log', self.refusal('EvidenceRegisterInvalid')
              and b'Evidence register rejected: UnknownField' in self.logs())
        check('invalid register revokes both previously issued links', all(self.denied(url) for url in urls))
        self.put(self.register_path, json.dumps(stub).encode())
        self.cfg['EnableProductionEligibility'] = True
        self.update()
        check('production wins over lab: EvidenceExperimentalOnly', self.refusal('EvidenceExperimentalOnly'))
        self.cfg['EnableAuthRangeLab'] = False
        self.update()
        check('production only: EvidenceExperimentalOnly and zero eligible rows', self.refusal('EvidenceExperimentalOnly')
              and not any(row['status']=='eligible' for row in self.seed['rows']))
        fake = json.loads(json.dumps(stub))
        row = fake['rows'][0]
        row['tuple']['appVersion'] = 'unmeasured'
        row['evidenceRef'] = 'delivery/evidence/no-such-presentation-receipt.md'
        row['status'] = 'eligible'
        row['controls'] = ['straight-play', 'seek-forward', 'seek-back', 'resume', 'restart']
        for result in row['results'].values():
            result.update(outcome='pass', limit='Fabricated for the missing-evidence test; never load this document into the plugin.')
        check('evidence reference tripwire catches fabricated eligible row',
              missing_eligible_evidence(self.seed) == [] and missing_eligible_evidence(fake) == [row['evidenceRef']])


class ShelfLab(PointerLab):
    def reconcile(self):
        return self.api('/Cleanyfin/lab/reconcile','POST',user=self.admin)['Count']

    def issued(self):
        return self.api('/Cleanyfin/lab/status',user=self.admin)['Issued']

    def posted_as(self, user, item):
        return self.request('/Items/'+item+'/PlaybackInfo','POST',dict(self.body,MediaSourceId=item),user=user)

    def refused(self,user,item,reason=None):
        status,h,raw=self.posted_as(user,item)
        return status==200 and json.loads(raw).get('MediaSources')==[] and json.loads(raw).get('ErrorCode')=='NoCompatibleStream' and (reason is None or h.get('x-cleanyfin-reason')==reason)

    def views(self,user,uid):
        folders=self.api('/UserViews?userId='+uid,user=user)['Items'];items=self.api('/Items?Recursive=true&IncludeItemTypes=Video',user=user)['Items']
        return ({f['Id'].replace('-','') for f in folders},{i['Id'].replace('-','') for i in items})

    def allow(self,uid,folders):
        p=self.api('/Users/'+uid,user=self.admin)['Policy'];p.update(EnableAllFolders=False,EnabledFolders=folders)
        self.api('/Users/'+uid+'/Policy','POST',p,user=self.admin)

    def restart(self):
        docker('restart',self.cid)
        for _ in range(90):
            try:
                if self.request('/System/Info/Public')[0]==200:return
            except subprocess.CalledProcessError:pass
            time.sleep(.5)
        raise RuntimeError('disposable server did not restart')

    def state(self,user,uid,item):
        return self.api('/Users/'+uid+'/Items/'+item,user=user)['UserData']

    def second_av(self, url):
        oracle = json.loads((self.work/'fixture/plan.json').read_text())
        oracle['cuts'] = [dict(startFrame=137, endFrame=200, startMs=5480, endMs=8000)]
        plan = self.work/'capture/profile-two.v0.json'
        plan.write_text(json.dumps(oracle))
        frames, wav = self.capture_av('profile-two', url)
        # This cut retains part of the 880 Hz tone; the first cut's tone oracle does not apply.
        metrics, _ = self.score_av(plan, frames)
        v = metrics['video']
        check('profile B: 437 kept frames, zero cut frames',
              v['pass'] and v['frames']==437 and v['markedCount']==0 and v['missingCount']==0)
        self.check_pcm('profile B joint PCM', oracle, wav)

    def exercise(self):
        media=lambda:docker('exec',self.cid,'find','/fixture-library','-type','f','-exec','sha256sum','{}',';')
        before=media();self.setup_pointer();a=self.pointer;b,row_b=self.find_pointer(self.viewer2);row_a=next(r for r in self.inventory() if r['Viewer']==self.viewer)
        fa=self.folders['Shelf'];fb=self.folders['Playback samples '+self.viewer2];orig=self.folders['Original']
        self.allow(self.viewer2,[fb,orig]); va,ia=self.views(self.one,self.viewer);vb,ib=self.views(self.two,self.viewer2)
        check('explicit ACL: each viewer sees only their shelf and pointer',fa in va and fb not in va and fb in vb and fa not in vb and a in ia and b not in ia and b in ib and a not in ib)
        pw=secrets.token_urlsafe(24);new=self.api('/Users/New','POST',{'Name':'not-enrolled','Password':pw},user=self.admin)
        login=self.api('/Users/AuthenticateByName','POST',{'Username':'not-enrolled','Pw':pw},user=('guest',''));guest=('guest',login['AccessToken']);self.extra_tokens=[guest[1]]
        views,items=self.views(guest,new['Id']);check('default all-folders viewer can browse both shelves',not new['Policy']['IsAdministrator'] and new['Policy']['EnableAllFolders'] and {fa,fb}<=views and {a,b}<=items)
        count=self.issued();check('unenrolled: reason-coded no source, no grant on either pointer',all(self.refused(guest,i,'AuthorityMismatch') for i in [a,b]) and self.issued()==count)
        self.allow(new['Id'],[orig]);views,items=self.views(guest,new['Id']);check('explicit folder list hides both shelves',not {fa,fb}&views and not {a,b}&items)
        pw=secrets.token_urlsafe(24);denied_uid=self.api('/Users/New','POST',{'Name':'denied','Password':pw},user=self.admin)['Id'].replace('-','')
        denied_login=self.api('/Users/AuthenticateByName','POST',{'Username':'denied','Pw':pw},user=('denied',''));denied_user=('denied',denied_login['AccessToken']);self.extra_tokens.append(denied_user[1]);self.policy(denied_uid,'denied',['violence'])
        assert self.reconcile()==3;denied_ptr,_=self.find_pointer(denied_uid);denied_folder=next(f['ItemId'] for f in self.api('/Library/VirtualFolders',user=self.admin) if f['Name']=='Playback samples '+denied_uid)
        self.allow(denied_uid,[denied_folder]);check('original ACL denial: no source or grant',self.refused(denied_user,denied_ptr,'AuthorityMismatch') and self.issued()==count)
        (self.data/'profiles'/denied_uid/'policy.json').unlink();assert self.reconcile()==2
        _,_,raw=self.posted_as(self.two,b);info=json.loads(raw);source,=info['MediaSources'];assert source['Id']==b and source['SupportsDirectPlay'] and not source['SupportsTranscoding'] and not source.get('TranscodingUrl')
        other=self.verify_offer(source['Path'],5480,8000); first=self.projection()
        item_b=self.api('/Users/'+self.viewer2+'/Items/'+b,user=self.two)
        check('browse and play: 14.96 s and 17.48 s',self.browse() and source['RunTimeTicks']==item_b['RunTimeTicks']==174800000)
        extra=self.api('/Users/'+self.viewer+'/Items/'+a+'?Fields=Chapters,Trickplay,MediaSources',user=self.one)
        check('no chapters, previews, subtitles, alternate audio or download',not extra.get('Chapters') and not extra.get('Trickplay') and not extra.get('ImageTags') and [x['Type'] for x in extra['MediaStreams']]==['Video','Audio'] and self.request('/Items/'+a+'/Download',user=self.one)[0]==404 and self.request('/Items/'+a+'/Images/Primary',user=self.one)[0]==404)
        self.player(first[0]);self.second_av(other[0]);saved=self.state(self.one,self.viewer,self.item)
        cfg=self.api('/System/Configuration',user=self.admin);cfg.update(MinResumeDurationSeconds=1,MinResumePct=1,MaxResumePct=95);self.api('/System/Configuration','POST',cfg,user=self.admin)
        event=dict(ItemId=a,MediaSourceId=a,PlaySessionId=self.posted()['PlaySessionId'],CanSeek=True,PlayMethod='DirectPlay',PositionTicks=0)
        for path,pos in [('/Sessions/Playing',0),('/Sessions/Playing/Progress',70000000),('/Sessions/Playing/Stopped',70000000)]:
            self.api(path,'POST',dict(event,PositionTicks=pos),user=self.one)
        check('stop at filtered 7 s: only pointer resume changed',self.state(self.one,self.viewer,a)['PlaybackPositionTicks']==70000000 and self.state(self.one,self.viewer,self.item)==saved and self.state(self.two,self.viewer2,b)['PlaybackPositionTicks']==0)
        inventory=(self.data/'inventory.json').read_bytes()
        def modification_times():return {p:p.stat().st_mtime_ns for p in [self.data/'inventory.json',*(self.data/'shelves').rglob('*.strm')]}
        mtimes=modification_times();self.reconcile();self.reconcile();check('reconcile idempotent: no inventory or pointer rewrites',mtimes==modification_times())
        self.api('/Library/Refresh','POST',user=self.admin)
        for _ in range(80):
            tasks=self.api('/ScheduledTasks',user=self.admin)
            if all(t['State']=='Idle' for t in tasks if t.get('Key')=='RefreshLibrary'):break
            time.sleep(.25)
        self.restart();va,ia=self.views(self.one,self.viewer);vb,ib=self.views(self.two,self.viewer2);assert fa in va and fb not in va and fb in vb and fa not in vb and a in ia and b not in ia and b in ib and a not in ib
        check('rescan, restart, reconcile: same two IDs, isolated resume, no duplicates',self.reconcile()==2 and (self.data/'inventory.json').read_bytes()==inventory and self.find_pointer(self.viewer)[0]==a and self.find_pointer(self.viewer2)[0]==b and len(list((self.data/'shelves').rglob('*.strm')))==2 and self.state(self.one,self.viewer,a)['PlaybackPositionTicks']==70000000 and self.denied(first[0]))
        # Completion is reported on the projected clock, not the original clock.
        event['PlaySessionId']=self.posted()['PlaySessionId'];self.api('/Sessions/Playing','POST',event,user=self.one);self.api('/Sessions/Playing/Stopped','POST',dict(event,PositionTicks=149600000),user=self.one)
        check('watched flag isolated from original and other viewer',self.state(self.one,self.viewer,a)['Played'] and self.state(self.one,self.viewer,self.item)==saved and not self.state(self.two,self.viewer2,b)['Played'])
        first=self.projection();other=self.verify_offer(json.loads(self.posted_as(self.two,b)[2])['MediaSources'][0]['Path'],5480,8000)
        self.policy(self.viewer,self.one[0],['violence']);check('policy edit revokes A only',self.denied(first[0]) and self.denied(first[1]) and self.request(other[0])[0]==200)
        for _ in range(80):
            if any(r['Viewer']==self.viewer and r['Timeline']!=row_a['Timeline'] for r in self.inventory()):break
            time.sleep(.2)
        a2,changed=self.find_pointer(self.viewer);newstate=self.state(self.one,self.viewer,a2)
        check('automatic reconcile: new timeline starts unwatched at zero; B unchanged',a2!=a and changed['Timeline']!=row_a['Timeline'] and self.find_pointer(self.viewer2)==(b,row_b) and newstate['PlaybackPositionTicks']==0 and not newstate['Played'])
        self.policy(self.viewer,self.one[0],['profanity']);check('required mute: no source',self.refused(self.one,a2,'RequiredMute') or self.request('/Users/'+self.viewer+'/Items/'+a2,user=self.one)[0]==404)
        check('mute reconcile removes affected pointer only',self.reconcile()==1 and self.find_pointer(self.viewer2)==(b,row_b))
        raw=self.snapshot.read_bytes();doc=json.loads(raw);doc['annotations'][0]['severity']=1;self.put(self.snapshot,json.dumps(doc).encode());check('same-ref digest mismatch refuses and revokes',self.denied(other[1]) and self.reconcile()==0)
        self.put(self.snapshot,raw);self.policy(self.viewer,self.one[0],['violence','gore']);assert self.reconcile()==2
        b,_=self.find_pointer(self.viewer2);a,_=self.find_pointer(self.viewer);first=self.verify_offer(json.loads(self.posted_as(self.one,a)[2])['MediaSources'][0]['Path']);file=self.work/'fixture/source.mp4';st=file.stat()
        # Mutate only a muxer label, between requests (no concurrent transfers). Preserve size and mtime.
        altered=file.read_bytes().replace(b'Lavf',b'Xavf',1); assert altered!=self.original and len(altered)==len(self.original)
        with file.open('r+b') as f:f.write(altered)
        os.utime(file,ns=(st.st_atime_ns,st.st_mtime_ns));duration=docker('exec',self.cid,'/usr/lib/jellyfin-ffmpeg/ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0','/fixture-library/source.mp4')
        check('changed same-size, same-duration bytes refuse with restored mtime',float(duration)==20 and self.denied(first[1]) and self.reconcile()==0)
        # Also remount a replacement inode at exactly the same path and check the old snapshot at startup.
        self.put(file,altered);self.restart();check('same-path replacement inode gets no projection',self.reconcile()==0)
        self.put(file,self.original);self.restart();assert self.reconcile()==2;a,_=self.find_pointer(self.viewer);b,_=self.find_pointer(self.viewer2)
        self.cfg['EnableAuthRangeLab']=False;self.update();check('flag off: both pointers have no source',self.refused(self.one,a) and self.refused(self.two,b))
        rows=self.inventory();install=self.work/'config/plugins/Cleanyfin_0.1.0.0';offline=self.work/'detached-plugin'
        docker('stop',self.cid);install.rename(offline);self.restart()
        assert self.request('/Cleanyfin/lab/status',user=self.admin)[0]==404
        for pid,uid,identity in [(a,self.viewer,self.one),(b,self.viewer2,self.two)]:
            loc=next(r['Locator'] for r in rows if r['Viewer']==uid)
            assert self.request(loc)[0]==404 and self.request(loc,user=identity)[0]==404
            status,_,response=self.posted_as(identity,pid)
            assert self.item.encode() not in response and b'/Cleanyfin/play/' not in response
            if status==200:
                for src in json.loads(response).get('MediaSources',[]):assert src.get('Path')==loc
        self.pointer=a;self.generic_routes();check('uninstall: bare pointers are dead, no media fallback',True)
        docker('stop',self.cid);offline.rename(install);self.restart()
        foreign=self.data/'shelves'/self.viewer/'operator-note.txt';foreign.write_text('retain me')
        check('cleanup admin only',self.request('/Cleanyfin/lab/cleanup','POST',user=self.one)[0] in (401,403))
        self.api('/Cleanyfin/lab/cleanup','POST',user=self.admin)
        check('cleanup: only owned pointers and inventory removed; original bytes unchanged',foreign.read_text()=='retain me' and self.snapshot.read_bytes()==raw and not list((self.data/'shelves').rglob('*.strm')) and not (self.data/'inventory.json').exists() and media()==before)


class LoopbackForward:
    """Gives the headless browser a host-loopback origin for the network-less lab.

    Listens on 127.0.0.1 only. Each browser connection becomes one `docker exec` that opens the lab
    server's own loopback socket inside its namespace; the container keeps --network none and nothing
    is published to any interface.
    """
    def __init__(self, cid, port):
        import socket
        import threading
        self.threading = threading
        self.cid, self.target, self.procs = cid, port, []
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.threading.Thread(target=self.pipe, args=(conn,), daemon=True).start()

    def pipe(self, conn):
        p = subprocess.Popen(['docker', 'exec', '-i', self.cid, 'bash', '-c',
                              f'exec 3<>/dev/tcp/127.0.0.1/{self.target}; cat <&3 & cat >&3; wait'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.procs.append(p)

        def up():
            try:
                while data := conn.recv(65536):
                    p.stdin.write(data)
                    p.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                p.kill()
        self.threading.Thread(target=up, daemon=True).start()
        try:
            while data := p.stdout.read1(65536):
                conn.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
            p.kill()

    def close(self):
        self.sock.close()
        for p in self.procs:
            p.kill()


DESIGN = ROOT/'knowledge-base/01-working/marking-foundation-2026-11-24'


class MarkingLab(PointerLab):
    """Slice 2: phone capture -> household store in the plugin -> review page refinement -> accepted
    snapshot -> the existing filtered shelf, with role, bypass and audit checks on the way."""

    def __init__(self, work, jellyfin=DEFAULT_JELLYFIN):
        super().__init__(work, jellyfin)
        self.name = 'cleanyfin-marking-' + secrets.token_hex(6)
        self.forward = None

    def m(self, path, method='GET', data=None, user=None, headers=None):
        status, h, body = self.request('/Cleanyfin/marking/' + path, method, data, user, headers)
        parsed = json.loads(body) if body and h.get('content-type', '').startswith('application/json') else body
        return status, parsed, h

    def ok(self, path, method='GET', data=None, user=None, expect=(200,)):
        status, body, _ = self.m(path, method, data, user)
        if status not in expect:
            raise RuntimeError(f'marking {method} {path.split("/")[0]} status {status} {body.get("refusal") if isinstance(body, dict) else ""}')
        return body

    def refused(self, path, method, data, user, status, reason=None):
        got, body, h = self.m(path, method, data, user)
        return got == status and (reason is None or (isinstance(body, dict) and body.get('refusal') == reason
                                                     and h.get('x-cleanyfin-refusal') == reason))

    def capture(self, author, ref, at, *, kind='point', end=None, category=None, surface='phone-remote', item=None, server=None):
        doc = {'captureVersion': 1, 'ref': ref, 'capturedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
               'author': author, 'surface': surface,
               'target': {'serverRef': server or self.server_ref, 'itemId': item or self.item, 'mediaSourceId': item or self.item},
               'clock': {'kind': 'source'}, 'kind': kind, 'atMs': at, 'window': {'beforeMs': 4000, 'afterMs': 1500}}
        if end is not None:
            doc['endAtMs'] = end
        if category:
            doc['category'] = category
        return doc

    def governance(self, self_approval=True):
        doc = {'governanceVersion': 1, 'rev': f'gov:lab:{secrets.token_hex(4)}',
               'members': [{'user': self.ref['admin'], 'roles': ['admin', 'reviewer']},
                           {'user': self.ref['one'], 'roles': ['marker']},
                           {'user': self.ref['two'], 'roles': ['viewer']},
                           {'user': self.ref['svc'], 'roles': ['suggester']}],
               'profiles': [], 'approval': {'selfApproval': self_approval, 'snapshotExpiryDays': 30}}
        self.put(self.mdata/'governance.json', json.dumps(doc).encode())

    def browser(self, scenario, user, **extra):
        cfg = {'origin': f'http://127.0.0.1:{self.forward.port}{BASE}', 'itemId': self.item, 'scratch': str(self.work),
               'user': {'name': user, 'password': self.passwords[user]}, **extra}
        r = subprocess.run(['node', LAB/'marking-browser.mjs', scenario], input=json.dumps(cfg).encode(),
                           stdout=subprocess.PIPE, timeout=300)
        out = json.loads(r.stdout.decode().strip().splitlines()[-1])
        if 'error' in out:
            raise RuntimeError(f'browser {scenario}: {out["error"]}')
        return out

    def audit_lines(self):
        return [l for l in (self.mdata/'audit.jsonl').read_text().split('\n') if l]

    def exercise(self):
        self.users_library()
        api = self.api
        self.original = (self.work/'fixture/source.mp4').read_bytes()
        self.configurl = '/Plugins/'+PLUGIN+'/Configuration'
        self.cfg = api(self.configurl, user=self.admin)
        self.data = self.work/'config/plugins/configurations/cleanyfin-shelves-v1'
        self.mdata = self.work/'config/plugins/configurations/cleanyfin-marking-v1'
        self.snapshot = self.data/'snapshots'/(self.item+'.json')
        self.register_path = self.data/'evidence-register-v1.json'
        self.put(self.register_path, json.dumps(self.stub_register()).encode())
        self.policy(self.viewer, self.one[0], ['violence'])
        self.cfg.update(EnableAuthRangeLab=True, LabOrigin=self.origin, LabPointerKey=secrets.token_hex(16),
                        LabLifetimeSeconds=300, LabSourceSha256=sha(self.original))
        self.update()
        self.server_ref = api('/System/Info/Public')['Id']
        pw = secrets.token_urlsafe(24)
        api('/Users/New', 'POST', {'Name': 'svc', 'Password': pw}, user=self.admin)
        login = api('/Users/AuthenticateByName', 'POST', {'Username': 'svc', 'Pw': pw}, user=('svc', ''))
        self.svc = ('svc', login['AccessToken'])
        self.extra_tokens = [self.svc[1]]
        uid = lambda u: api('/Users/Me', user=u)['Id'].replace('-', '')
        self.ref = {'admin': 'jf:user:'+uid(self.admin), 'one': 'jf:user:'+self.viewer, 'two': 'jf:user:'+self.viewer2,
                    'svc': 'jf:user:'+uid(self.svc)}

        # Pages are static and same-origin; every data route needs a person.
        pages = [self.request('/Cleanyfin/marking/'+p) for p in ('capture', 'review', 'assets/common.js', 'assets/review.js')]
        check('pages served same-origin without data, with a strict CSP',
              all(s == 200 for s, _, _ in pages) and pages[0][1]['content-type'].startswith('text/html')
              and "default-src 'self'" in pages[1][1].get('content-security-policy', '')
              and self.request('/Cleanyfin/marking/assets/..%2Fconfig')[0] == 404
              and all(self.request('/Cleanyfin/marking/'+p)[0] == 401 for p in ('me', 'items', 'audit', 'timing')))

        # Default roles from Jellyfin: administrators review; everyone else may only flag a moment.
        me_admin, me_one = self.ok('me', user=self.admin), self.ok('me', user=self.one)
        check('no governance file: admin is admin+reviewer, others viewer (Jellyfin default)',
              me_admin['roles'] == ['admin', 'reviewer'] and me_one['roles'] == ['viewer']
              and me_admin['governanceSource'] == 'jellyfin-default' and me_one['serverRef'] == self.server_ref)
        flag = self.capture(self.ref['one'], 'cap:lab:flag', 3000)
        check('viewer may flag a moment (capture) but not draft a mark',
              self.m('captures', 'POST', flag, self.one)[0] == 201
              and self.refused('marks', 'POST', {'captureRef': 'cap:lab:flag'}, self.one, 403, 'NotPermitted'))
        api('/Auth/Keys?app=cleanyfin-lab', 'POST', user=self.admin)
        key, = [k['AccessToken'] for k in api('/Auth/Keys', user=self.admin)['Items'] if k['AppName'] == 'cleanyfin-lab']
        self.extra_tokens.append(key)
        keyed = {'Authorization': f'MediaBrowser Token="{key}"'}
        check('an API key names no person: refused on every marking route',
              all(self.request('/Cleanyfin/marking/'+p, m, d, headers=keyed)[0] == 403
                  for p, m, d in [('me', 'GET', None), ('items/'+self.item+'/publish', 'POST', {}), ('captures', 'POST', flag)]))

        self.governance()
        check('governance file is authoritative', self.ok('me', user=self.one)['roles'] == ['marker']
              and self.ok('me', user=self.admin)['governanceSource'] == 'file')
        check('a capture cannot name another author', self.refused('captures', 'POST', self.capture(self.ref['admin'], 'cap:lab:spoof', 4000), self.one, 403, 'NotPermitted'))
        check('a capture for another server or a hidden item is refused',
              self.refused('captures', 'POST', self.capture(self.ref['one'], 'cap:lab:srv', 4000, server='elsewhere'), self.one, 422, 'BindingMismatch')
              and self.refused('captures', 'POST', self.capture(self.ref['two'], 'cap:lab:hidden', 4000), self.two, 404, 'UnknownItem'))
        check('malformed and oversized bodies refused', self.m('captures', 'POST', '{"captureVersion":1,', self.one)[0] == 400
              and self.m('captures', 'POST', 'x'*70000, self.one)[0] == 413)
        check('suggester: service captures only; no drafts',
              self.refused('captures', 'POST', self.capture(self.ref['svc'], 'cap:lab:svc1', 4000), self.svc, 403, 'NotPermitted')
              and self.m('captures', 'POST', self.capture(self.ref['svc'], 'cap:lab:svc2', 4000, surface='service'), self.svc)[0] == 201
              and self.refused('marks', 'POST', {'captureRef': 'cap:lab:svc2'}, self.svc, 403, 'NotPermitted'))
        check('a phone-remote capture from a person cannot use the service surface',
              self.refused('captures', 'POST', self.capture(self.ref['one'], 'cap:lab:fake-svc', 4000, surface='service'), self.one, 403, 'NotPermitted'))

        # The phone page, served by the plugin, while a "TV" session of the same account plays the original.
        tv = self.api('/Users/AuthenticateByName', 'POST', {'Username': 'one', 'Pw': self.passwords['one']}, user=('tv', ''))
        tv = ('tv', tv['AccessToken']); self.extra_tokens.append(tv[1])
        self.api('/Sessions/Capabilities/Full', 'POST', {'PlayableMediaTypes': ['Video'], 'SupportedCommands': [], 'SupportsMediaControl': True}, user=tv)
        play = self.api('/Items/'+self.item+'/PlaybackInfo', user=tv)['PlaySessionId']
        self.api('/Sessions/Playing', 'POST', {'ItemId': self.item, 'MediaSourceId': self.item, 'PlaySessionId': play, 'CanSeek': True,
                                               'PlayMethod': 'DirectPlay', 'PositionTicks': 56_000_000, 'IsPaused': False}, user=tv)
        self.forward = LoopbackForward(self.cid, self.port)
        phone = self.browser('capture', 'one', screenshot=str(DESIGN/'capture-page-plugin.png'))
        print('Phone page:', phone['browser'], '|', ' / '.join(phone['sentAfterReconnect']), flush=True)
        stored = self.ok(f'items/{self.item}', user=self.admin)['captures']
        mine = [c for c in stored if c['author'] == self.ref['one'] and c['surface'] == 'phone-remote' and c['ref'].startswith('cap:') and not c['ref'].startswith('cap:lab:')]
        span = [c for c in mine if c['kind'] == 'span']
        check('phone page: span, mark and an offline press all reach the household store',
              len(mine) == 3 and len(span) == 1 and span[0]['category'] == 'violence' and span[0]['endAtMs'] > span[0]['atMs'] >= 5600
              and sorted(c.get('category', '') for c in mine) == ['', 'language', 'violence'] and 'waiting to send' in phone['queuedOffline'])
        self.api('/Sessions/Playing/Stopped', 'POST', {'ItemId': self.item, 'MediaSourceId': self.item, 'PlaySessionId': play, 'PositionTicks': 60_000_000}, user=tv)

        # A marker drafts and roughs in; only a reviewer decides.
        draft = self.ok('marks', 'POST', {'captureRef': span[0]['ref']}, self.one, expect=(201,))
        mark = draft['ref']
        check('marker drafts a mark from their capture over its search window, unclassified',
              draft['state'] == 'proposed' and draft['boundsSource'] == 'capture-window' and 'category' not in draft and not draft['fileChanged'])
        rough = {'rev': 1, 'startMs': 5000, 'endMs': 11000, 'category': 'violence', 'severity': 2, 'action': 'skip'}
        edited = self.ok('marks/'+mark, 'PUT', rough, self.one)
        check('marker edits their own proposed mark', edited['rev'] == 2 and edited['editedBy'] == self.ref['one'])
        check('stale revision refused', self.refused('marks/'+mark, 'PUT', rough, self.one, 409, 'StaleRevision'))
        check('marker cannot accept, reject, withdraw or publish',
              all(self.refused(f'marks/{mark}/{d}', 'POST', {'rev': 2}, self.one, 403, 'NotPermitted') for d in ('accept', 'reject', 'withdraw'))
              and self.refused(f'items/{self.item}/publish', 'POST', {}, self.one, 403, 'NotPermitted'))
        check('a body cannot carry an actor or state', self.refused(f'marks/{mark}/accept', 'POST', '{"rev":2,"actor":"%s"}' % self.ref['admin'], self.one, 400, 'UnknownField')
              and self.refused('marks/'+mark, 'PUT', dict(rough, rev=2, state='accepted'), self.one, 400, 'UnknownField'))
        check('suggester and library-less viewer cannot decide',
              self.refused(f'marks/{mark}/accept', 'POST', {'rev': 2}, self.svc, 403, 'NotPermitted')
              and self.refused(f'marks/{mark}/accept', 'POST', {'rev': 2}, self.two, 404, 'UnknownMark'))
        check('a rough (capture-window) mark cannot be accepted, even by a reviewer',
              self.refused('marks', 'POST', {'captureRef': 'cap:lab:flag'}, self.two, 404) and
              self.ok('marks', 'POST', {'captureRef': 'cap:lab:flag'}, self.admin, expect=(201,))['boundsSource'] == 'capture-window')
        flagged = [m for m in self.ok(f'items/{self.item}', user=self.admin)['marks'] if m['provenance']['from'] == ['cap:lab:flag']][0]
        check('...a draft carries no guessed classification, so accept refuses it', self.refused(f'marks/{flagged["ref"]}/accept', 'POST', {'rev': 1}, self.admin, 422, 'IncompleteMark'))
        self.ok(f'marks/{flagged["ref"]}/withdraw', 'POST', {'rev': 1}, self.admin)
        self.governance(self_approval=False)
        own = self.ok('marks', 'POST', {'captureRef': 'cap:lab:svc2', 'exact': {'startMs': 100, 'endMs': 200, 'category': 'gore', 'severity': 1, 'action': 'skip'}}, self.admin, expect=(201,))
        check('second-reviewer setting: a reviewer cannot accept their own mark', self.refused(f'marks/{own["ref"]}/accept', 'POST', {'rev': 1}, self.admin, 403, 'SecondReviewerRequired'))
        self.ok(f'marks/{own["ref"]}/withdraw', 'POST', {'rev': 1}, self.admin)
        self.governance()

        # Review page: refuses exact boundaries until this browser's frame timing is measured on the fixture.
        self.put(self.mdata/'review-timing-v1.json', b'{"timingVersion":1,"rows":[]}')
        measured = self.browser('measure', 'admin')
        print('Review page timing:', measured['product'], json.dumps(measured['browser']), f"{measured['framesChecked']} frames, {measured['mismatches']} off", flush=True)
        check('unmeasured browser: exact boundaries refused (I does nothing)',
              measured['before']['timing'] == 'unmeasured' and measured['before']['refine'] == '0' and measured['afterKeyI'] == '–')
        bad = [c for c in measured['checks'] if c['page'] != c['expected'] or c['pixels'] != c['expected']]
        check(f"browser frame timing on the barcode fixture: {measured['framesChecked']} seeks/steps/nudges, page frame = decoded barcode",
              not bad and measured['framesChecked'] >= 60 and measured['msExact'] and measured['redExact'] and measured['fault'] == 'unmeasured')
        row = dict(measured['browser'], frameRates=[25], result='pass', framesChecked=measured['framesChecked'], mismatches=0,
                   product=measured['product'], jellyfin=self.server_version,
                   fixture='tools/playback-lab/hard-fixture.py (500 frames, 25 fps, H.264 High with B-frames, AAC)',
                   measuredAt=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), evidenceRef='tools/playback-lab/check-plugin.py --case marking')
        self.timing_row = row
        print('Timing register row:', json.dumps(row), flush=True)
        self.put(self.mdata/'review-timing-v1.json', json.dumps({'timingVersion': 1, 'rows': [row]}).encode())

        refined = self.browser('refine', 'admin', markRef=mark, accept=True, publish=True, screenshot=str(DESIGN/'review-page.png'))
        print('Refined:', json.dumps(refined['form']), '|', refined.get('accepted'), '|', refined.get('published'), flush=True)
        check('measured browser: reviewer steps to the first and last red frame by eye',
              refined['timing'] == 'measured' and refined['refineEnabled'] == '1' and refined['firstRed'] == {'page': 137, 'pixels': 137}
              and refined['lastRed'] == {'page': 262, 'pixels': 262, 'color': 'red'})
        latest = next(m for m in self.ok(f'items/{self.item}', user=self.admin)['marks'] if m['ref'] == mark)
        check('proposed, accepted and published from the page: [5480, 10520) violence/2/skip',
              (latest['startMs'], latest['endMs'], latest['category'], latest['severity'], latest['action'], latest['state'], latest['boundsSource'], latest['editedBy'])
              == (5480, 10520, 'violence', 2, 'skip', 'accepted', 'reviewer', self.ref['admin']) and 'Published 1' in refined['published'])
        snap = json.loads(self.snapshot.read_bytes())
        ann, = snap['annotations']
        check('snapshot written where the resolver reads it, bound to the exact original',
              snap['binding']['sha256'] == sha(self.original) and snap['binding']['serverRef'] == self.server_ref
              and snap['binding']['itemId'] == self.item and ann['ref'] == mark and (ann['startMs'], ann['endMs']) == (5480, 10520)
              and ann['contentSha256'] == sha(f'annotation-v1|{mark}|violence|2|skip|5480|10520'.encode()))

        # The existing filtered shelf now plays the published plan, through the same checks as hand-placed snapshots.
        check('reconcile: one filtered pointer from the published snapshot', self.api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 1)
        self.pointer, row_ = self.find_pointer(self.viewer)
        self.folders = {f['Name']: f['ItemId'] for f in self.api('/Library/VirtualFolders', user=self.admin)}
        self.folders['Shelf'] = self.folders['Playback samples '+self.viewer]
        self.shelf_access(True)
        self.body = {'DeviceProfile': self.profile, 'MediaSourceId': self.pointer, 'MaxStreamingBitrate': 100000000, 'AutoOpenLiveStream': True}
        check('capture on a filtered item refused until its clock can be mapped',
              self.refused('captures', 'POST', self.capture(self.ref['one'], 'cap:lab:filtered', 4000, item=self.pointer), self.one, 422, 'FilteredItemNotSupported'))
        url, _ = self.projection()
        self.player(url)

        # Audit: the chain, its recorded head, and the trail from plan to decision.
        lines = self.audit_lines()
        head = self.ok('audit', user=self.admin)
        prev = '0'*64
        for line in lines:
            assert json.loads(line)['prev'] == prev, 'audit chain'
            prev = sha(line.encode())
        types = [(json.loads(l)['type'], json.loads(l)['actor']) for l in lines]
        accept = [json.loads(l) for l in lines if json.loads(l)['type'] == 'mark.accept']
        check('audit: chain verifies; head matches disk; accept names admin and the published digest',
              head == {'verified': True, 'count': len(lines), 'head': sha(lines[-1].encode())}
              and ('snapshot.publish', self.ref['admin']) in types and ('mark.edit', self.ref['one']) in types
              and accept[-1]['subject'] == f'{mark}@{latest["rev"]}' and accept[-1]['digest'] == ann['contentSha256']
              and accept[-1]['actor'] == self.ref['admin'])
        check('audit head readable by markers, not viewers', self.m('audit', user=self.one)[0] == 200 and self.refused('audit', 'GET', None, self.two, 403, 'NotPermitted'))
        raw = (self.mdata/'audit.jsonl').read_bytes()
        middle = len(lines)//2
        tampered = lines[:]; tampered[middle] = tampered[middle].replace('"actor":"jf:user:', '"actor":"jf:user:0', 1)
        (self.mdata/'audit.jsonl').write_text('\n'.join(tampered)+'\n')
        # A rewritten line still parses; the next line's prev no longer matches it.
        check(f'edited audit line {middle+1} detected at line {middle+2}; publish refused', self.refused('audit', 'GET', None, self.admin, 409, 'AuditChainBroken')
              and self.m('audit', user=self.admin)[1]['line'] == middle+2 and self.refused(f'items/{self.item}/publish', 'POST', {}, self.admin, 409, 'AuditChainBroken'))
        (self.mdata/'audit.jsonl').write_text('\n'.join(lines[:-1])+'\n')
        check('dropped newest audit line detected against the recorded head', self.refused('audit', 'GET', None, self.admin, 409, 'AuditTruncated'))
        head_file = (self.mdata/'audit.head').read_bytes()
        (self.mdata/'audit.jsonl').write_bytes(raw); (self.mdata/'audit.head').unlink()
        check('removed head detected', self.refused('audit', 'GET', None, self.admin, 409, 'AuditHeadMissing'))
        (self.mdata/'audit.head').write_bytes(head_file)
        check('restored record verifies again', self.ok('audit', user=self.admin) == head)

        # Editing an accepted mark takes it out of the next snapshot until it is accepted again.
        again = self.ok('marks/'+mark, 'PUT', {'rev': latest['rev'], 'startMs': 5480, 'endMs': 10520, 'category': 'violence', 'severity': 3, 'action': 'skip'}, self.admin)
        pub = self.ok(f'items/{self.item}/publish', 'POST', {}, self.admin)
        check('edit after accept: proposed rev, empty snapshot, shelf withdrawn',
              again['state'] == 'proposed' and pub['annotations'] == 0 and json.loads(self.snapshot.read_bytes())['annotations'] == []
              and self.api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 0)
        logs = docker('logs', self.cid, stderr=subprocess.STDOUT)
        check('no ApiKey= query strings in the server log', b'apikey=' not in logs.lower())
        self.forward.close()


class HouseholdBindingLab(MarkingLab):
    """An ordinary household library file (a folder mount, not an operator-pinned inode) is checked in the
    background, marked, reviewed, published and played filtered; then a touch keeps it and a replacement
    withdraws the filtered item and asks for review again. The file's bytes are the hard fixture's, because
    playback is only offered for a measured stream layout; its path, mount and verifier are the household ones."""

    MOVIE = 'Lab Movie (2026)/Lab Movie (2026).mp4'
    PACE = 100_000  # bytes per second, so the background check is slow enough to watch

    def __init__(self, work, jellyfin=DEFAULT_JELLYFIN):
        super().__init__(work, jellyfin)
        self.name = 'cleanyfin-household-' + secrets.token_hex(6)
        self.household = work/'household'
        self.household.mkdir()
        self.mounts = ['-v', f'{self.household}:/media/movies:ro,z']

    def governance(self, self_approval=True):
        doc = {'governanceVersion': 1, 'rev': f'gov:lab:{secrets.token_hex(4)}',
               'members': [{'user': self.ref['admin'], 'roles': ['admin', 'reviewer']},
                           {'user': self.ref['one'], 'roles': ['marker']},
                           {'user': self.ref['two'], 'roles': ['viewer']}],
               'profiles': [], 'approval': {'selfApproval': self_approval, 'snapshotExpiryDays': 30}}
        self.put(self.mdata/'governance.json', json.dumps(doc).encode())

    def detail(self):
        return self.ok(f'items/{self.item}', user=self.admin)

    def wait_edition(self, label, until, limit=90):
        seen, started = [], time.monotonic()
        while time.monotonic() - started < limit:
            t0 = time.monotonic(); d = self.detail(); latency = time.monotonic() - t0
            seen.append((d['edition']['state'], d['edition']['bytesDone'], d['edition']['bytesTotal'], latency))
            if until(d):
                return d, seen, time.monotonic() - started
            time.sleep(0.25)
        raise RuntimeError(f'{label}: edition check did not settle')

    def offer_now(self):
        url, src = self.projection()
        assert self.request(src, headers={'Range': 'bytes=0-99'})[2] == self.original[:100]
        return url, src

    def exercise(self):
        self.users_library()
        api = self.api
        self.original = (self.work/'fixture/source.mp4').read_bytes()
        movie = self.household/self.MOVIE
        movie.parent.mkdir()
        shutil.copyfile(self.work/'fixture/source.mp4', movie)
        inside = '/media/movies/' + self.MOVIE
        self.folder('Movies', '/media/movies')
        folders = {f['Name']: f['ItemId'] for f in api('/Library/VirtualFolders', user=self.admin)}
        self.refresh(folders['Movies'])
        for _ in range(60):
            items = api('/Items?Recursive=true&IncludeItemTypes=Movie,Video&Fields=Path,MediaSources', user=self.admin)['Items']
            match = [i for i in items if i.get('Path') == inside and i.get('MediaSources')]
            if match: break
            time.sleep(1)
        else: raise RuntimeError('household movie not indexed')
        self.item = match[0]['Id'].replace('-', '')
        policy = api('/Users/'+self.viewer, user=self.admin)['Policy']
        policy.update(EnableAllFolders=False, EnabledFolders=[folders['Movies']])
        api(f'/Users/{self.viewer}/Policy', 'POST', policy, user=self.admin)
        check('an ordinary library file: folder mount, spaces in its path, not under the pinned fixture path',
              match[0]['Path'] == inside and not inside.startswith('/fixture-library/'))

        self.configurl = '/Plugins/'+PLUGIN+'/Configuration'
        self.cfg = api(self.configurl, user=self.admin)
        self.data = self.work/'config/plugins/configurations/cleanyfin-shelves-v1'
        self.mdata = self.work/'config/plugins/configurations/cleanyfin-marking-v1'
        self.editions = self.work/'config/plugins/configurations/cleanyfin-editions-v1'
        self.snapshot = self.data/'snapshots'/(self.item+'.json')
        self.register_path = self.data/'evidence-register-v1.json'
        self.put(self.register_path, json.dumps(self.stub_register()).encode())
        self.policy(self.viewer, self.one[0], ['violence'])
        self.cfg.update(EnableAuthRangeLab=True, LabOrigin=self.origin, LabPointerKey=secrets.token_hex(16),
                        LabLifetimeSeconds=300, LabSourceSha256=sha(self.original), EditionHashBytesPerSecond=self.PACE)
        self.update()
        self.server_ref = api('/System/Info/Public')['Id']
        uid = lambda u: api('/Users/Me', user=u)['Id'].replace('-', '')
        self.ref = {'admin': 'jf:user:'+uid(self.admin), 'one': 'jf:user:'+self.viewer, 'two': 'jf:user:'+self.viewer2}
        self.extra_tokens = []
        self.governance()

        # Before the background check finishes: captures are fine, binding anything is refused with a reason.
        check('captures need no file identity', self.m('captures', 'POST', self.capture(self.ref['one'], 'cap:hh:span', 5600, kind='span', end=10000, category='violence'), self.one)[0] == 201)
        first, seen, took = self.wait_edition('first check', lambda d: d['binding'] is not None)
        pending = [s for s in seen if s[0] in ('queued', 'verifying')]
        early = seen[0]
        print(f'Edition check: {len(seen)} looks over {took:.1f} s at {self.PACE} B/s pace; slowest look {max(s[3] for s in seen)*1000:.0f} ms', flush=True)
        check('first look: queued or verifying in the background, refused as VerificationPending, request not blocked',
              early[0] in ('queued', 'verifying') and pending and max(s[3] for s in seen) < 2.0)
        check('progress is reported while the file is read', any(0 < s[1] < s[2] for s in seen if s[0] == 'verifying'))
        b = first['binding']
        record = json.loads((self.editions/(self.item+'.json')).read_bytes())
        edition = sha(f"edition-v1|{record['sha256']}|{record['layoutSha256']}".encode())
        check('verified: binding = full SHA-256 of the bytes, size, 20 s, exact clock, edition digest in bindingRev',
              first['edition']['state'] == 'verified' and b['sha256'] == sha(self.original) and b['sizeBytes'] == len(self.original)
              and b['durationMs'] == 20000 and b['bindingRev'] == f'bind:{self.item}:{edition}'
              and [(t['index'], t['kind'], t['timeBase'], t['anchorPts'], t['anchorSourceMs']) for t in b['tracks']]
              == [(0, 'video', [1, 12800], 0, 0), (1, 'audio', [1, 48000], 0, 0)])
        check('edition record stored privately (path, signals, hash, layout), no media',
              record['path'] == inside and record['sha256'] == sha(self.original) and record['stamp']['size'] == len(self.original)
              and len((self.editions/(self.item+'.json')).read_bytes()) < 4096)

        # A marker drafts, the administrator refines frame-exactly in the browser, accepts and publishes.
        span = next(c for c in self.detail()['captures'] if c['ref'] == 'cap:hh:span')
        draft = self.ok('marks', 'POST', {'captureRef': span['ref']}, self.one, expect=(201,))
        mark = draft['ref']
        self.ok('marks/'+mark, 'PUT', {'rev': 1, 'startMs': 5000, 'endMs': 11000, 'category': 'violence', 'severity': 2, 'action': 'skip'}, self.one)
        self.forward = LoopbackForward(self.cid, self.port)
        self.put(self.mdata/'review-timing-v1.json', b'{"timingVersion":1,"rows":[]}')
        measured = self.browser('measure', 'admin')
        bad = [c for c in measured['checks'] if c['page'] != c['expected'] or c['pixels'] != c['expected']]
        check(f"review page on the household file: {measured['framesChecked']} seeks/steps/nudges, page frame = decoded barcode",
              not bad and measured['framesChecked'] >= 60 and measured['msExact'] and measured['redExact'])
        row = dict(measured['browser'], frameRates=[25], result='pass', framesChecked=measured['framesChecked'], mismatches=0,
                   product=measured['product'], jellyfin=self.server_version,
                   fixture='tools/playback-lab/hard-fixture.py (500 frames, 25 fps, H.264 High with B-frames, AAC)',
                   measuredAt=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), evidenceRef='tools/playback-lab/check-plugin.py --case household-binding')
        self.put(self.mdata/'review-timing-v1.json', json.dumps({'timingVersion': 1, 'rows': [row]}).encode())
        refined = self.browser('refine', 'admin', markRef=mark, accept=True, publish=True)
        latest = next(m for m in self.detail()['marks'] if m['ref'] == mark)
        print('Refined:', json.dumps({k: refined.get(k) for k in ('timing', 'refineEnabled', 'firstRed', 'lastRed', 'form', 'accepted', 'published')}),
              '|', (latest['startMs'], latest['endMs'], latest['state']), flush=True)
        check('reviewer refines to [5480, 10520), accepts and publishes on the household file',
              refined['firstRed'] == {'page': 137, 'pixels': 137} and (latest['startMs'], latest['endMs'], latest['state']) == (5480, 10520, 'accepted')
              and 'Published 1' in refined['published'])
        snap = json.loads(self.snapshot.read_bytes())
        check('snapshot bound to the household edition', snap['binding'] == b and [a['ref'] for a in snap['annotations']] == [mark])

        # The filtered item plays exactly (same mpv sample-exact checks as the pinned fixture).
        check('reconcile: one filtered item for the household file', api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 1)
        self.pointer, row_ = self.find_pointer(self.viewer)
        self.folders = {f['Name']: f['ItemId'] for f in api('/Library/VirtualFolders', user=self.admin)}
        self.folders['Shelf'] = self.folders['Playback samples '+self.viewer]
        self.folders['Original'] = self.folders['Movies']
        self.shelf_access(True)
        self.body = {'DeviceProfile': self.profile, 'MediaSourceId': self.pointer, 'MaxStreamingBitrate': 100000000, 'AutoOpenLiveStream': True}
        url, _ = self.projection()
        self.player(url)

        # Touch (same bytes, new mtime): the old proof lapses at once; a background re-hash gives the same edition back.
        url, src = self.offer_now()
        now = time.time_ns(); os.utime(movie, ns=(now, now))
        lapsed = self.denied(url) and self.denied(src, headers={'Range': 'bytes=0-99'})
        kept = api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 1 and self.find_pointer(self.viewer)[0] == self.pointer
        back, seen, took = self.wait_edition('touch', lambda d: d['binding'] is not None)
        check(f'touch: outstanding offer refused at once; same edition after re-hash ({took:.1f} s); filtered item kept',
              lapsed and kept and back['binding'] == b and not any(m['fileChanged'] for m in back['marks']) and back['reviewAgain'] == 0)
        url, src = self.offer_now()
        check('touch: the same filtered item plays again', self.find_pointer(self.viewer)[0] == self.pointer)

        # Replace with a same-size, same-duration copy (new inode, mtime preserved), as a re-download would.
        altered = self.original.replace(b'Lavf', b'Xavf', 1); assert altered != self.original and len(altered) == len(self.original)
        st = movie.stat(); part = movie.with_name('.incoming.part'); part.write_bytes(altered)
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns)); os.replace(part, movie)
        duration = docker('exec', self.cid, '/usr/lib/jellyfin-ffmpeg/ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', inside)
        refused_now = self.denied(url) and self.denied(src, headers={'Range': 'bytes=0-99'})
        changed, seen, took = self.wait_edition('replace', lambda d: d['binding'] is not None and d['binding']['bindingRev'] != b['bindingRev'])
        stale = next(m for m in changed['marks'] if m['ref'] == mark)
        check(f'replace (same size, duration {float(duration):g} s, mtime): offer refused at once; new edition after re-hash ({took:.1f} s)',
              float(duration) == 20 and refused_now and changed['binding']['sha256'] == sha(altered) and changed['binding']['sizeBytes'] == len(self.original))
        withdrawn = api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 0
        status, _, body = self.request('/Items/'+self.pointer+'/PlaybackInfo', 'POST', self.body, user=self.one)
        no_fallback = b'/Cleanyfin/play/' not in body and inside.encode() not in body and b'/media/movies' not in body
        check('replace: filtered item withdrawn, no unfiltered fallback', withdrawn and no_fallback and json.loads(self.snapshot.read_bytes())['binding'] == b)
        check('replace: the mark stays on its edition, flagged "file changed, review again"; publishing refuses',
              stale['fileChanged'] and stale['bindingRev'] == b['bindingRev'] and stale['state'] == 'accepted' and changed['reviewAgain'] == 1
              and self.refused(f'items/{self.item}/publish', 'POST', {}, self.admin, 422, 'FileChanged'))
        seen_page = self.browser('look', 'admin', screenshot=str(DESIGN/'household-review-again.png'))
        print('Review page says:', seen_page['text'], flush=True)
        check('review page surfaces "review again" and the changed mark',
              seen_page['reviewAgain'] == 1 and 'Review again' in seen_page['text'] and any('FILE CHANGED, review again' in t for t in seen_page['marks']))
        # The reviewer withdraws the old mark; a new mark is bound to the new edition. Nothing moved across.
        self.ok(f'marks/{mark}/withdraw', 'POST', {'rev': latest['rev']}, self.admin)
        self.m('captures', 'POST', self.capture(self.ref['one'], 'cap:hh:again', 7000), self.one)
        fresh = self.ok('marks', 'POST', {'captureRef': 'cap:hh:again'}, self.one, expect=(201,))
        pub = self.ok(f'items/{self.item}/publish', 'POST', {}, self.admin)
        check('after review: new mark bound to the new edition; publish has nothing accepted, so nothing is offered',
              fresh['bindingRev'] == changed['binding']['bindingRev'] and pub['annotations'] == 0
              and api('/Cleanyfin/lab/reconcile', 'POST', user=self.admin)['Count'] == 0)
        logs = docker('logs', self.cid, stderr=subprocess.STDOUT)
        for line in re.findall(rb'Edition verified: [^\n]*', logs):
            print('Plugin log:', line.decode(errors='replace'), flush=True)
        check('no ApiKey= query strings in the server log', b'apikey=' not in logs.lower())
        self.forward.close()


def main():
    if not __debug__:
        raise SystemExit('Run without Python optimization: fixture assertions are required.')
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--case',choices=['auth-range','pointer-refresh','shelf-isolation','eligibility','marking','household-binding'],required=True)
    p.add_argument('--jellyfin',choices=sorted(JELLYFIN),default=DEFAULT_JELLYFIN,help='server line under test: image, plugin target and SDK together')
    args=p.parse_args();case=args.case
    scratch=ROOT/'plugin/obj';scratch.mkdir(exist_ok=True)
    w=Path(tempfile.mkdtemp(prefix=f'{case}-{args.jellyfin}-',dir=scratch));lab={'auth-range':Lab,'pointer-refresh':PointerLab,'shelf-isolation':ShelfLab,'eligibility':EligibilityLab,'marking':MarkingLab,'household-binding':HouseholdBindingLab}[case](w,args.jellyfin);ok=False
    try:
        lab.setup();lab.exercise();docker('stop',lab.cid);lab.inspect();ok=True
    except Exception as e:
        # CalledProcessError may include arguments. Do not print capabilities or passwords.
        msg=str(e) if isinstance(e,(AssertionError,RuntimeError)) else type(e).__name__
        print('FAIL',msg or 'fixture assertion',flush=True)
    finally:
        if getattr(lab, 'forward', None):
            lab.forward.close()
        if lab.cid:
            docker('stop',lab.cid)
            if ok: docker('rm','-v',lab.cid)
        disk=sum(p.stat().st_blocks*512 for p in w.rglob('*') if p.is_file()); print(f'Lab disk: build {lab.build_disk} bytes; end {disk} bytes',flush=True)
        if ok: shutil.rmtree(w)
        else: print('Failure retained:',w,lab.name,flush=True)
    return 0 if ok else 1


if __name__=='__main__':
    sys.exit(main())
