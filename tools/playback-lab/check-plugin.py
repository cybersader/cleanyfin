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
        for folder in ('plugin', 'delivery/Cleanyfin.Delivery'):
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
        print('Plugin SHA-256', sha((install/'Jellyfin.Plugin.Cleanyfin.dll').read_bytes()), flush=True)
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
        if user:
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
        self.two, viewer2 = login('two', p2)
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
        self.cfg.update(EnableAuthRangeLab=True, LabOrigin=self.origin, LabItemId=self.item, LabViewerId=self.viewer, LabDeviceId=self.one[0],
                        LabSourceSha256=sha(self.original), LabPlanSha256=sha(raw), LabLifetimeSeconds=300)
        self.update()

    def update(self):
        self.api(self.configurl, 'POST', self.cfg, user=self.admin)

    def offer(self):
        status, h, raw = self.request('/Cleanyfin/lab/issue','POST',user=self.one)
        assert status == 200 and h.get('cache-control') == 'private, no-store', 'non-admin issuance'
        return self.verify_offer(json.loads(raw)['ListUrl'])

    def verify_offer(self, url):
        m = re.fullmatch(re.escape(self.origin)+r'/Cleanyfin/play/([0-9a-f]{32})/list\.edl\?t=([0-9a-f]{32})', url)
        assert m, 'list URL format'
        slot, a = m.groups(); self.slots.add(slot); self.tokens.add(a)
        s, h, edl = self.request(url)
        assert s == 200 and h['content-type'] == 'text/plain; charset=utf-8' and h['cache-control'] == 'private, no-store', f'descriptor status {s}'
        assert int(h['content-length']) == len(edl)
        status, hh, data = self.request(url,'HEAD'); assert status == 200 and not data and int(hh['content-length']) == len(edl)
        header, first, second = edl.decode().splitlines(); assert header == '# mpv EDL v0'
        m = re.fullmatch(r'%(\d+)%([^,]+),0\.000,5\.480', first); assert m
        n, source = m.groups(); assert len(source.encode()) == int(n) and second == f'%{n}%{source},10.520,9.480'
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
                docker('run','-i','--rm','--pull=never','--network','container:'+self.cid,'--user',USER,'-e','HOME=/tmp',*mounts,
                       '--entrypoint','sh',MPV,'-c','IFS= read -r src; exec mpv --no-config --no-terminal --load-scripts=no --hwdec=no --untimed "$@" -- "$src"','mpv',*driver,
                       input=(source+'\n').encode(),stderr=subprocess.DEVNULL,timeout=120)
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
        check('server logs/config/cache: no grant values, copies or transcodes',not outputs and not any(re.search(rb'/Cleanyfin/play/[a-f0-9]{32}/(?:list\.edl|source)\?t=[a-f0-9]{32}',blob,re.I) for blob in blobs) and not any(s in blob for s in secrets_seen for blob in blobs) and not any(s in blob for s in native_tokens for blob in [log,*log_files]))
        print('Default HTTP logging, SessionManager=Warning: refusal-code lines',log.count(b'Auth-range lab refused:'),'ticket-route lines',log.lower().count(b'/cleanyfin/play/'),flush=True)


class PointerLab(Lab):
    shelf = '/config/cleanyfin-lab/shelf'
    profile = {'Name':'mpv', 'DirectPlayProfiles':[{'Type':'Video','Container':'mp4','VideoCodec':'h264','AudioCodec':'aac'}],
               'TranscodingProfiles':[{'Type':'Video','Context':'Streaming','Protocol':'hls','Container':'ts','VideoCodec':'h264','AudioCodec':'aac'}]}

    def __init__(self, work):
        super().__init__(work)
        self.client,self.version='Wholphin','1.1.2-0-gc530f5c5'
        self.name='cleanyfin-pointer-'+secrets.token_hex(6)

    def shelf_access(self, original):
        policy=self.api('/Users/'+self.viewer,user=self.admin)['Policy']
        policy.update(EnableAllFolders=False,EnabledFolders=[self.folders['Shelf']]+([self.folders['Original']] if original else []))
        self.api('/Users/'+self.viewer+'/Policy','POST',policy,user=self.admin)

    def setup_pointer(self):
        self.users_library(); self.configure()
        self.cfg.update(LabPointerKey=secrets.token_hex(16), LabOrigin=getattr(self,'public_origin',self.origin));self.update()
        self.api('/Cleanyfin/lab/create-pointer','POST',user=self.admin)
        self.folder('Shelf',self.shelf)
        self.folders={f['Name']:f['ItemId'] for f in self.api('/Library/VirtualFolders', user=self.admin)}
        self.shelf_access(True);self.refresh(self.folders['Shelf'])
        for _ in range(60):
            items=self.api('/Items?Recursive=true&IncludeItemTypes=Video&Fields=Path,MediaSources',user=self.one)['Items']
            matches=[i for i in items if i.get('Path')==self.shelf+'/Playback sample.strm']
            if matches:break
            time.sleep(.5)
        else:raise RuntimeError('viewer cannot browse generated item')
        self.pointer=matches[0]['Id'].replace('-','');self.cfg['LabPointerId']=self.pointer;self.update()
        self.locator='/Cleanyfin/pointer/'+self.cfg['LabPointerKey']
        self.body={'DeviceProfile':self.profile, 'MediaSourceId':self.pointer, 'MaxStreamingBitrate':100000000,'AutoOpenLiveStream':True}
        hidden=self.api('/Items?Recursive=true&IncludeItemTypes=Video',user=self.two)['Items']
        check('enrolled viewer sees distinct pointer',self.pointer!=self.item and all(i['Id'].replace('-','')!=self.pointer for i in hidden))

    def browse(self):
        item=self.api('/Users/'+self.viewer+'/Items/'+self.pointer+'?Fields=MediaSources',user=self.one)
        return item.get('RunTimeTicks')==149600000 and [s['Codec'] for s in item.get('MediaStreams',[])]==['h264','aac']

    def posted(self,body=None):
        return self.api('/Items/'+self.pointer+'/PlaybackInfo','POST',body or self.body,user=self.one)

    def projection(self):
        info=self.posted();sources=info.get('MediaSources',[])
        if len(sources)!=1:raise RuntimeError('one direct-play source (got '+str(len(sources))+')')
        s=sources[0];assert s['Protocol']=='Http' and s['IsRemote'] and s['Id']==self.pointer and s['SupportsDirectPlay'] and not s['SupportsDirectStream'] and not s['SupportsTranscoding'] and not s.get('TranscodingUrl') and not s['RequiresOpening'] and s['RunTimeTicks']==149600000 and s['Container']=='mp4','direct-only filtered source'
        facts='Index Type Codec Profile Level Width Height AverageFrameRate RealFrameRate BitRate SampleRate Channels ChannelLayout IsDefault'.split()
        original=self.api('/Items/'+self.item+'/PlaybackInfo',user=self.one)['MediaSources'][0]
        rows=s['MediaStreams']; assert len(rows)==2 and all([r.get(k) for k in facts]==[o.get(k) for k in facts] and not r.get('Path') and not r.get('DeliveryUrl') for r,o in zip(rows,original['MediaStreams'])),'whitelisted original stream facts'
        assert self.item not in json.dumps(info) and self.one[1] not in json.dumps(info),'original identity exposed'
        return self.verify_offer(s['Path'])

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
        self.player(url)
        original=self.original_shape()
        uri='/Items/'+self.pointer+'/PlaybackInfo'
        check('GET, Exo, incompatible codec and low bitrate refused',not self.api(uri,user=self.one)['MediaSources'] and all(not self.posted(dict(self.body,**delta))['MediaSources'] for delta in [ {'DeviceProfile':dict(self.profile,Name='ExoPlayer')},{'DeviceProfile':dict(self.profile,DirectPlayProfiles=[{'Type':'Video','Container':'mp4','VideoCodec':'hevc','AudioCodec':'aac'}])},{'MaxStreamingBitrate':1}]))
        self.cfg['LabWithholdProjection']=True;self.update();check('withheld streams: no source',not self.posted()['MediaSources']);self.cfg['LabWithholdProjection']=False;self.update()
        self.cfg['LabLifetimeSeconds']=2;self.update();short=self.projection();time.sleep(2.1)
        check('expired pointer list and range refused',self.denied(short[0]) and self.denied(short[1],headers={'Range':'bytes=0-99'}))
        self.cfg['LabLifetimeSeconds']=300;self.update()
        self.shelf_access(False);check('original ACL denied: no source',not self.posted()['MediaSources']);self.shelf_access(True)
        plan=self.work/'plans/plan.json';raw=plan.read_bytes();plan.write_bytes(raw+b' ')
        check('changed plan: no source',not self.posted()['MediaSources']);plan.write_bytes(raw)
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


def main():
    if not __debug__:
        raise SystemExit('Run without Python optimization: fixture assertions are required.')
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--case',choices=['auth-range','pointer-refresh'],required=True);case=p.parse_args().case
    scratch=ROOT/'plugin/obj';scratch.mkdir(exist_ok=True)
    w=Path(tempfile.mkdtemp(prefix=case+'-',dir=scratch));lab=(Lab if case=='auth-range' else PointerLab)(w);ok=False
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
