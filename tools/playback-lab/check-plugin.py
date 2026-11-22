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
import wave

from hard_fixture_lab import IMAGE

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
SDK = 'mcr.microsoft.com/dotnet/sdk:9.0'
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
    def __init__(self, work):
        self.work = work
        self.name = 'cleanyfin-auth-' + secrets.token_hex(6)
        self.cid = None
        self.port = 8096  # private loopback in a new --network none namespace
        self.origin = f'http://127.0.0.1:{self.port}{BASE}'
        self.tokens, self.slots = set(), set()
        self.build_disk = 0
        self.client, self.version = 'auth-range', '1'

    def setup(self):
        for img in (IMAGE, SDK):
            docker('image', 'inspect', img)
        w = self.work
        for name in ('config/config', 'cache', 'plans', 'other', 'capture'):
            (w/name).mkdir(parents=True)
        for folder in ('plugin', 'delivery/Cleanyfin.Delivery', 'delivery/evidence'):
            shutil.copytree(ROOT/folder, w/'src'/folder, ignore=shutil.ignore_patterns('bin','obj'))
        docker('run', '--rm', '--pull=never', '--user', USER, '-e', 'HOME=/out/home',
                       '-v', f'{w}:/out:z', '-w', '/out/src', SDK,
                       'dotnet', 'build', 'plugin', '-c', 'Release', '--artifacts-path', '/out/artifacts', '-v:q')
        install = w/'config/plugins/Cleanyfin_0.1.0.0'
        install.mkdir(parents=True)
        for dll in ('Jellyfin.Plugin.Cleanyfin.dll', 'Cleanyfin.Delivery.dll'):
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
                          '-v', f'{w}/other:/other:ro,z', '-v', f'{w}/plans:/cleanyfin-fixture:ro,z', IMAGE).decode().strip()
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
            args.append('data-binary = '+json.dumps(json.dumps(data)))
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
                       '--entrypoint','sh',MPV,'-c','IFS= read -r src; exec mpv --no-config --no-terminal --load-scripts=no --hwdec=no --untimed "$@" -- "$src"','mpv',*driver,
                       input=(source+'\n').encode(),stderr=subprocess.DEVNULL,timeout=120)

    def player(self, url):
        docker('image','inspect',MPV)
        w=self.work; capture=w/'capture'; plan=w/'fixture/plan.json'
        for label, source in [('control','/source.mp4'),('cut',url)]:
            frames=capture/(label+'-frames'); frames.mkdir()
            for driver in [('--no-audio','--vo=image','--vo-image-format=png',f'--vo-image-outdir=/capture/{frames.name}'),('--no-video','--ao=pcm','--ao-pcm-waveheader=yes',f'--ao-pcm-file=/capture/{label}.wav','--audio-samplerate=48000','--audio-channels=stereo','--audio-format=s16')]:
                mounts=['-v',f'{capture}:/capture:z']
                if label=='control': mounts += ['-v',f'{w}/fixture/source.mp4:/source.mp4:ro,z']
                # Same isolated network namespace. No host networking or second bind. Supply URL
                # through stdin, not docker create args, mpv logs or a playlist file.
                self.mpv(source,mounts,driver)
            scored=subprocess.run([sys.executable,LAB/'analyze-capture.py','--plan',plan,'--video',frames,'--audio',capture/(label+'.wav'),'--json'],capture_output=True,text=True,timeout=120)
            lines=scored.stdout.splitlines(); metrics=json.loads(lines[-1]); v=metrics['video']; a=metrics['audio']
            if label=='control':
                check('mpv positive control: 500 frames, 126 marked and marked tone',v['frames']==500 and v['markedCount']==126 and not v['missingCount'] and abs(a['leakMs']-5040)<=5)
            else:
                # Presentation is a separate verdict, including known kept-audio loss. Never
                # overwrite the scorer or silently drop its negative result from the receipt.
                print('\n'.join(lines[:-1]), flush=True)
                check('mpv handoff: all 374 kept, zero marked frames or detected tone',v['pass'] and v['frames']==374 and v['markedCount']==0 and a['leakMs']==0)
        def pcm(label):
            with wave.open(str(capture/(label+'.wav')),'rb') as f:
                assert (f.getframerate(),f.getnchannels(),f.getsampwidth())==(48000,2,2)
                return f.readframes(f.getnframes())
        full=pcm('control'); actual=pcm('cut'); expected=full[:5480*48*4]+full[10520*48*4:20000*48*4]
        delta=[i//4 for i in range(0,min(len(actual),len(expected)),4) if actual[i:i+4]!=expected[i:i+4]]
        print(f'PCM: {len(delta)} unequal frames; first/last {delta[:1]}/{delta[-1:]}; length delta {(len(actual)-len(expected))//4}',flush=True)

    def inspect(self):
        log=docker('logs',self.cid,stderr=subprocess.STDOUT)
        secrets_seen=[s.encode() for s in self.tokens|self.slots]
        outputs=[]; blobs=[log]
        for tree in ('config','cache'):
            for p in (self.work/tree).rglob('*'):
                if not p.is_file(): continue
                raw=p.read_bytes(); blobs.append(raw)
                mime = mimetypes.guess_type(p.name)[0] or ''
                if (mime.startswith(('video/', 'audio/', 'image/')) or p.suffix.lower() in {'.ts','.m4s','.m3u8'}
                        or raw[4:8] == b'ftyp' or raw.startswith((b'\x1aE\xdf\xa3', b'\xff\xd8\xff', b'\x89PNG', b'RIFF'))
                        or self.original in raw):
                    outputs.append(p)
        native_tokens = [u[1].encode() for u in (self.admin,self.one,self.two,self.other_device)]
        log_files = [p.read_bytes() for p in (self.work/'config/log').rglob('*') if p.is_file()]
        check('server logs/config/cache: no grant values, copies or transcodes',not outputs and not any(re.search(rb'/Cleanyfin/play/[a-f0-9]{32}/(?:list\.edl|source)\?t=[a-f0-9]{32}',blob,re.I) for blob in blobs) and not any(s in blob for s in secrets_seen for blob in blobs) and not any(s in blob for s in native_tokens+[t.encode() for t in getattr(self,'extra_tokens',[])] for blob in [log,*log_files]))
        print('Default HTTP logging, SessionManager=Warning: refusal-code lines',log.count(b'Auth-range lab refused:'),'ticket-route lines',log.lower().count(b'/cleanyfin/play/'),flush=True)


class PointerLab(Lab):
    shelf = '/config/cleanyfin-lab/shelf'
    profile = {'Name':'mpv', 'DirectPlayProfiles':[{'Type':'Video','Container':'mp4','VideoCodec':'h264','AudioCodec':'aac'}],
               'TranscodingProfiles':[{'Type':'Video','Context':'Streaming','Protocol':'hls','Container':'ts','VideoCodec':'h264','AudioCodec':'aac'}]}

    def __init__(self, work):
        super().__init__(work)
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

    def stub_register(self):
        # This new build did not make the historical capture. Use an explicitly unmeasured,
        # disposable row to exercise the gate, never rewrite the historical evidence.
        row = json.loads(json.dumps(self.wholphin))
        row['tuple']['pluginBuild'] = self.plugin_build
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
        check('historical plugin build differs: no offer', self.refusal('EvidenceTupleUnknown'))
        stub = self.stub_register()
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

    def second_video(self,url):
        oracle=json.loads((self.work/'fixture/plan.json').read_text());oracle['cuts']=[dict(startFrame=137,endFrame=200,startMs=5480,endMs=8000)]
        plan=self.work/'capture/profile-two.v0.json';plan.write_text(json.dumps(oracle));frames=self.work/'capture/profile-two';frames.mkdir()
        self.mpv(url,['-v',f'{frames}:/capture:z'],('--no-audio','--vo=image','--vo-image-format=png','--vo-image-outdir=/capture'))
        scored=subprocess.run([sys.executable,LAB/'analyze-capture.py','--plan',plan,'--video',frames,'--json'],capture_output=True,text=True,timeout=120)
        v=json.loads(scored.stdout.splitlines()[-1])['video']
        check('profile B: 437 kept frames, zero cut frames',scored.returncode==0 and v['frames']==437 and v['markedCount']==0 and v['missingCount']==0)

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
        self.player(first[0]);self.second_video(other[0]);saved=self.state(self.one,self.viewer,self.item)
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


def main():
    if not __debug__:
        raise SystemExit('Run without Python optimization: fixture assertions are required.')
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--case',choices=['auth-range','pointer-refresh','shelf-isolation','eligibility'],required=True);case=p.parse_args().case
    scratch=ROOT/'plugin/obj';scratch.mkdir(exist_ok=True)
    w=Path(tempfile.mkdtemp(prefix=case+'-',dir=scratch));lab={'auth-range':Lab,'pointer-refresh':PointerLab,'shelf-isolation':ShelfLab,'eligibility':EligibilityLab}[case](w);ok=False
    try:
        lab.setup();lab.exercise();docker('stop',lab.cid);lab.inspect();ok=True
    except Exception as e:
        # CalledProcessError may include arguments. Do not print capabilities or passwords.
        msg=str(e) if isinstance(e,(AssertionError,RuntimeError)) else type(e).__name__
        print('FAIL',msg or 'fixture assertion',flush=True)
    finally:
        if lab.cid:
            docker('stop',lab.cid)
            if ok: docker('rm','-v',lab.cid)
        disk=sum(p.stat().st_blocks*512 for p in w.rglob('*') if p.is_file()); print(f'Lab disk: build {lab.build_disk} bytes; end {disk} bytes',flush=True)
        if ok: shutil.rmtree(w)
        else: print('Failure retained:',w,lab.name,flush=True)
    return 0 if ok else 1


if __name__=='__main__':
    sys.exit(main())
