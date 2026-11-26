"""Opt-in playback operations on the qualified ADB transport. AdbLab.probe stays read-only.

No adb shell interface or default server. APK inputs are copied, digest checked and exposed
as a single read-only client file. Only owned synthetic captures or public package code may
leave the guest. Fresh-install cleanup can never remove a preexisting family installation.
"""
import hashlib
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import shutil
import subprocess as sp
import time

from adb_lab import AdbLab, LabError, PRIVATE_PORT
from tv_device import APP, ASSETS, TOUCHED, CONTEXT
from tv_update import OLD_SHA

ACT = APP+'/.MainActivity'
KEYS = {'DPAD_UP','DPAD_DOWN','DPAD_LEFT','DPAD_RIGHT','DPAD_CENTER','BACK','ENTER','DEL'}
PUBLIC = re.compile(r'/data/app/(?:[\w~+=.-]+/)*'+re.escape(APP)+r'-[\w~+=.-]+/base\.apk')
READS = {('get-state',),('shell','getprop'),('shell','getprop','sys.boot_completed'),('shell','getprop','ro.build.fingerprint'),
         ('shell','wm','size'),('shell','pm','list','features'),('shell','pm','list','packages'),
         ('shell','pm','list','packages','--show-versioncode'),('shell','dumpsys','package',APP),('shell','pm','path',APP),
         ('shell','ss','-ltn'),('shell','ls','/data/local/tmp'),('shell','cmd','media_session','volume','--stream','3','--get'),
         ('shell','dumpsys','SurfaceFlinger','--list'),('shell','dumpsys','media_session'),('shell','pidof',APP)}
READS.update(('shell','settings','get',ns,key) for ns,key in TOUCHED+CONTEXT)

def refused(): raise LabError('Operation is outside the playback session scope.')

class Playback:
    def __init__(self,lab,work,*,guest,family):
        self.lab,self.work,self.guest,self.family=lab,Path(work).resolve(),guest,family
        self.capture=set();self.xml=set();self.public=set();self.ports=set();self.owned_install=False
        self.logpid=None;self.timing_layers=set();self.active=False;self.source_abi=None
        self.local_inputs=set();self.baseline_lines=frozenset()

    @classmethod
    def guest_session(cls,stack,work,sdk,*,family=False,receipt=None):
        # Each dry run has its own disposable neutral key, not the profile worker's persistent key/lock.
        # Bind the observation to the actual namespace keeper, not a reusable PID.
        state={}
        def observed_exit():
            if receipt is None or 'fd' not in state:return
            fd=state['fd']
            try:receipt['privateAdbExited']=bool(select.select([fd],[],[],0)[0] and lab.server.poll() is not None and not lab.tmp.exists())
            finally:os.close(fd)
        stack.callback(observed_exit)
        lab=stack.enter_context(AdbLab(state=Path(work)/'adb-state',adb=Path(sdk)/'platform-tools/adb'))
        if receipt is not None:state['fd']=os.pidfd_open(int(lab.ns[lab.ns.index('-t')+1]))
        return cls(lab,work,guest=True,family=family)

    def boot_guest(self,tv):
        if not self.guest or self.lab.connected:refused()
        # The emulator reads its injected key from this disposable AVD's own Android home.
        # Both copies live only in the same run's private scratch. Never copy a physical-session key.
        if self.lab.state!=self.work/'adb-state' or tv.work.resolve()!=self.work:refused()
        for name in ('adbkey','adbkey.pub'):
            dest=tv.home/'.android'/name
            with dest.open('xb') as f:f.write((self.lab.android/name).read_bytes())
            dest.chmod(0o600)
        for k in list(tv.env):
            if k.startswith(('ADB_','ANDROID_ADB_')) or k in ('ANDROID_SERIAL','ANDROID_SDK_HOME','ANDROID_PREFS_ROOT'):tv.env.pop(k)
        tv.env.update(HOME=str(tv.home),USER='cleanyfin',LOGNAME='cleanyfin',HOSTNAME='lab',ADB_MDNS='0',
                      ADB_MDNS_AUTO_CONNECT='0',ADB_LOCAL_TRANSPORT_MAX_PORT='0',ANDROID_ADB_SERVER_PORT=str(PRIVATE_PORT))

    def attach_guest(self,tv):
        if not self.guest or self.lab.connected or tv.emu.poll() is not None or tv.work.resolve()!=self.work:refused()
        # The relay is FROM the private adb namespace TO this cloned guest's private QEMU port only.
        dest=self.lab._relay(self.lab.stack,'127.0.0.1',5581,15040,outer=tv.prefix)
        end=time.monotonic()+120
        while time.monotonic()<end and tv.emu.poll() is None:
            try:
                self.lab._command('connect',dest)
                props=[self.lab._command('-s',dest,'shell','getprop',k).strip() for k in ('ro.boot.qemu','ro.build.version.sdk','ro.product.cpu.abi')]
                if props==[b'1',b'36',b'x86_64']:break
            except LabError:pass
            time.sleep(1)
        else:raise LabError('Private neutral transport did not reach the owned emulator.')
        self.lab.target=['-s',dest];self.lab.connected=True;self.lab.stack.callback(self.lab._disconnect,dest)
        self.source_abi='x86_64';self.active=True

    def qualify(self,abi):
        if abi not in ASSETS or (self.guest and abi!='x86_64') or not self.lab.connected:refused()
        self.source_abi=abi;self.active=True

    def bridge(self,port,sock):
        # No TCP forwarding into the host. The only destination is the exact lab-owned AF_UNIX server.
        expected={18096:self.work/'server/config/tv.sock',18097:self.work/'synthetic-profile/config/tv.sock'}
        if port not in expected or (port==18097 and not (self.guest and self.family)) or Path(sock)!=expected[port] or not sock.is_socket():refused()
        # tv-wire observes PlaybackInfo for the scorer; its TCP listener lives in the lab namespace.
        return self.lab.ns

    def ui_path(self):
        path='/data/local/tmp/cleanyfin-ui-'+secrets.token_hex(12)+'.xml';self.xml.add(path);return path

    def bind_capture(self,paths):self.capture={paths.wav,paths.video}

    def bind_original_options(self,visible):
        if not self.family or not self.active:refused()
        self.baseline_lines=frozenset(visible.validate().options.splitlines())

    def permitted_text(self,text):
        origins={'http://127.0.0.1:18096/labbase'}
        if self.guest:origins.add('http://10.0.2.2:18096/labbase')
        if self.guest and self.family:origins.add('http://127.0.0.1:18097/labbase')
        options={'hwdec=no','vo=gpu','gpu-context=android','opengl-es=yes','msg-level=all=v',
                 'ad-lavc-o=cons_noisegen=1','ao=pcm','ao-pcm-waveheader=yes','audio-format=s16',
                 'audio-channels=stereo','audio-samplerate=48000'}
        options.update('ao-pcm-file='+p for p in self.capture if p.endswith('.wav'))
        return text in origins or text in options or text in self.baseline_lines

    def _call(self,args,*,timeout=30,bind=None):
        if not self.lab.connected or self.lab.server.poll() is not None:refused()
        cmd=self.lab.client if bind is None else self.lab.bin[:-2]+['--ro-bind',str(bind),'/payload.apk','--','/adb','-H','127.0.0.1','-P',str(PRIVATE_PORT)]
        return sp.run(cmd+self.lab.target+list(args),env={'PATH':'/usr/bin:/bin'},stdin=sp.DEVNULL,stdout=sp.PIPE,stderr=sp.DEVNULL,timeout=min(240,timeout),check=True).stdout

    def _install(self,args,timeout):
        src=Path(args[-1]);old=self.guest and self.family and src.name=='Wholphin-x86_64.apk' and src.parent.name=='v1.0.9'
        helper=self.guest and src==self.work/'input/input.apk' and src in self.local_inputs
        expected=OLD_SHA if old else ASSETS[self.source_abi][1]
        if not self.active or args[1:-1] not in (('--no-streaming',),('-r','--no-streaming')):refused()
        updating='-r' in args
        if updating and (old or not (self.family or helper)):refused()
        if not updating and self.family and not old:refused()
        dest=self.work/('verified-input-'+secrets.token_hex(8)+'.apk')
        try:
            with dest.open('xb') as f,src.open('rb') as inp:shutil.copyfileobj(inp,f)
            if not helper and hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:refused()
            if not updating and self._call(('shell','pm','list','packages',APP)).strip():refused()
            result=self._call((*args[:-1],'/payload.apk'),timeout=timeout,bind=dest)
            if not self.family and not helper and result.strip().splitlines()[-1:]==[b'Success']:self.owned_install=True
            return result
        finally:dest.unlink(missing_ok=True)

    def run(self,*args,timeout=30):
        a=tuple(map(str,args));s=a[1:] if a[:1]==('shell',) else ();safe=a in READS
        if a==('shell','pm','path',APP):
            data=self._call(a,timeout=timeout);self.public={t[8:] for t in data.decode().splitlines() if t.startswith('package:') and PUBLIC.fullmatch(t[8:])};return data
        if a==('shell','pidof',APP):
            data=self._call(a,timeout=timeout);self.logpid=data.decode().strip();return data
        if a==('shell','dumpsys','SurfaceFlinger','--list'):
            data=self._call(a,timeout=timeout);self.timing_layers={shlex.quote(l) for l in data.decode(errors='replace').splitlines() if APP in l and 'SurfaceView' in l};return data
        if not safe and not self.active:refused()
        if a[:1]==('install',):return self._install(a,timeout)
        if a[:1]==('pull',) and len(a)==3:
            src,dst=a[1],Path(a[2]);permitted=(src in self.capture or src in self.public)
            if not permitted or dst.parent!=self.work or dst.is_symlink() or dst.exists():refused()
            size=int(self._call(('shell','stat','-c','%s',src)))
            if not 0<size<=80_000_000:refused()
            data=self._call(('exec-out','cat',shlex.quote(src)),timeout=timeout)
            if len(data)!=size:raise LabError('Incomplete owned file transfer.')
            with dst.open('xb') as f:f.write(data)
            return b''
        if a[:1]==('reverse',):
            if len(a)==4 and a[1]=='--no-rebind' and a[2]==a[3] and a[2] in self.allowed_ports():
                data=self._call(a);self.ports.add(a[2]);return data
            if len(a)==3 and a[1]=='--remove' and a[2] in self.ports:
                data=self._call(a);self.ports.remove(a[2]);return data
            refused()
        if a[:2]==('exec-out','cat') and len(a)==3:safe=a[2] in self.xml
        if a==('exec-out','screencap','-p'):safe=True
        if a in (('uninstall',APP),('shell','pm','clear',APP)):safe=self.owned_install and not self.family
        if a[:1]==('logcat',) and len(a)==5:safe=(a[1]=='-d' and self.logpid and self.logpid.isdecimal() and a[2]=='--pid='+self.logpid and a[3]=='-v' and a[4] in ('threadtime','tag'))
        if a==('logcat','-c'):safe=self.guest
        if s in (('am','force-stop',APP),('am','start','-n',ACT),('am','start','-W','-n',ACT),('input','keycombination','113','29')):safe=True
        if len(s)==15 and s[:5]==('am','start','-n',ACT,'-a') and s[5] in (APP+'.PLAYBACK','android.intent.action.VIEW'):
            safe=(s[6:8]==('--es','itemId') and s[9:11]==('--es','userId') and s[12:14]==('--es','serverId') and all(re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',s[i]) for i in (8,11,14)))
        if s[:2]==('input','keyevent'):safe=s[2:] in [(k,) for k in KEYS] or s[2:]==('--longpress','DPAD_CENTER')
        if s[:2]==('input','text') and len(s)==3:safe=self.permitted_text(s[2])
        if len(s)==3 and s[:2]==('uiautomator','dump'):safe=s[2] in self.xml
        if len(s)==3 and s[:2]==('rm','-f'):
            safe=s[2] in self.capture or s[2] in self.xml
            if safe:
                result=self._call(a,timeout=timeout);self.xml.discard(s[2]);return result
        if len(s)==3 and s[:2]==('test','-f'):safe=s[2] in self.capture
        if len(s)==3 and s[:2]==('ls','-1a'):safe=s[2] in {str(Path(v).parent) for v in self.capture}
        if len(s)==4 and s[:3]==('stat','-c','%s'):safe=s[3] in self.capture
        if len(s)==4 and s[:3]==('dumpsys','SurfaceFlinger','--latency'):safe=s[3] in self.timing_layers
        if len(s)==4 and s[:3]==('cmd','media_session','dispatch'):safe=s[3] in ('pause','play')
        if self.guest:
            if s==('cat','/proc/meminfo'):safe=True
            if s[:5]==('cmd','media_session','volume','--stream','3') and len(s)==7:safe=(s[5]=='--set' and s[6].isdecimal() and int(s[6])<=100)
            if s[:2]==('input','keyevent') and s[2:] and len(s)<=102 and set(s[2:])=={'KEYCODE_VOLUME_UP'}:safe=True
            if len(s)==10 and s[:5]==('am','instrument','-w','-e','op') and s[5] in ('pause','play','state','save','click') and s[6:8]==('-e','value') and re.fullmatch('[A-Za-z0-9+/=]{1,4096}',s[8]) and s[9]=='org.cleanyfin.fixture/.TvInput':safe=True
        if not safe:refused()
        return self._call(a,timeout=timeout)

    def allowed_ports(self):return {'tcp:18096','tcp:18097'} if self.guest and self.family else {'tcp:18096'}

    def recorder(self,path,seconds):
        if not self.active or path not in self.capture or not path.endswith('.mp4') or not 1<=seconds<=180:refused()
        shell='printf "%s\\n" "$$"; exec '+shlex.join(['screenrecord','--verbose','--size','320x180','--bit-rate','4000000','--time-limit',str(seconds),path])
        return [*self.lab.client,*self.lab.target,'shell','sh','-c',shlex.quote(shell)]

    def stop_recorder(self,path,pid):
        if path not in self.capture or not re.fullmatch('[1-9][0-9]{0,7}',pid):refused()
        # Refuse a recycled PID or an unrelated recording, even after loss of the remote process.
        cmd=self._call(('exec-out','cat',f'/proc/{pid}/cmdline')).rstrip(b'\0').split(b'\0')
        if not cmd or Path(os.fsdecode(cmd[0])).name!='screenrecord' or cmd[-1]!=path.encode():refused()
        return self._call(('shell','kill','-INT',pid))
