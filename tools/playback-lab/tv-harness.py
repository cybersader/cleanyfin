#!/usr/bin/env python3
"""Synthetic TV lab; only a fresh cloned AVD in a loopback-only network namespace."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
import shlex
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess as sp
import sys
import tempfile
import time
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
# Permit isolated Python while keeping imports limited to this trusted lab.
if str(HERE) not in sys.path:
    sys.path.insert(0,str(HERE))
APK = 'Wholphin-x86_64.apk'
DIGEST = '60305215500d033ffd55333adc0066a8ab15dc146f2262735cbb6e7e60ce692d'
REPO = 'damontecres/Wholphin'
TAG = 'v1.1.2'
MPV_OPTIONS = 'hwdec=no\nvo=gpu\ngpu-context=android\nopengl-es=yes\nmsg-level=all=v'
PICTURE_SPEED = .25
DISPLAY_CAPTURE_SIZE = '320x180'
MEDIA_MARGIN_SECONDS = 14
DISPLAY_PREROLL_SECONDS = 2


CGROUP_ROOT = Path('/sys/fs/cgroup')
CGROUP = next((CGROUP_ROOT/line[3:].lstrip('/') for line in Path('/proc/self/cgroup').read_text().splitlines()
                if line.startswith('0::')),None)


def cpu_capacity():
    count=len(os.sched_getaffinity(0))
    if CGROUP is not None:
        for group in (CGROUP,*CGROUP.parents):
            if group!=CGROUP_ROOT and CGROUP_ROOT not in group.parents:break
            try:quota,period=(group/'cpu.max').read_text().split()
            except FileNotFoundError:continue
            if quota!='max':count=min(count,int(quota)/int(period))
    return count


CPU_CAPACITY = cpu_capacity()


def pressure_full(path):
    try:row=next(line for line in path.read_text().splitlines() if line.startswith('full '))
    except (FileNotFoundError,StopIteration):return None
    return float(row.split()[1].split('=')[1])


def host_health():
    # MemAvailable includes reclaimable cache; load alone does not distinguish
    # CPU contention from tasks stuck in storage I/O. Record both.
    load=round(os.getloadavg()[0],2); cpus=CPU_CAPACITY
    mem=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))/1048576
    full=pressure_full(Path('/proc/pressure/io'))
    scope_mem=pressure_full(CGROUP/'memory.pressure') if CGROUP is not None else None
    scope_io=pressure_full(CGROUP/'io.pressure') if CGROUP is not None else None
    return dict(load1=load,cpus=cpus,availableGiB=round(mem,2),ioFull10sPct=full,
                scopeMemoryFull10sPct=scope_mem,scopeIOFull10sPct=scope_io,
                busy=load>cpus or mem<3 or any((p or 0)>=5 for p in (full,scope_mem,scope_io)))


def host_label(h):
    result=f"1m load {h['load1']:g}/{h['cpus']:g} CPUs, {h['availableGiB']:g} GiB available, I/O full PSI {h['ioFull10sPct']}%"
    if h.get('scopeMemoryFull10sPct') is not None:
        result+=f", run scope memory/I/O PSI {h['scopeMemoryFull10sPct']}%/{h['scopeIOFull10sPct']}%"
    return result


def wait_for_host(limit=180):
    end=time.monotonic()+limit; stable=0; announced=False
    while True:
        h=host_health(); stable=0 if h['busy'] else stable+1
        if stable>=3:
            print('Host ready:',host_label(h),file=sys.stderr,flush=True)
            return h
        if time.monotonic()>=end:
            raise RuntimeError('Host too busy to start TV harness: '+host_label(h))
        if h['busy'] and not announced:
            print('Waiting for host load and memory to settle:',host_label(h),file=sys.stderr,flush=True)
            announced=True
        time.sleep(5)


def timeout_label(stage,history):
    recent=list(history);current=host_health()
    busy=sum(s['busy'] for s in recent)>=2 or current['busy']
    detail=host_label(current)
    previous=next((s for s in reversed(recent) if s['busy']),None)
    if busy and not current['busy'] and previous:detail+='; recently '+host_label(previous)
    return f"{'Host too busy; ' if busy else ''}timed out at {stage} ({detail})"


def run(*args, timeout=30, **kwargs):
    return sp.run(list(map(str, args)), check=True, timeout=timeout, stdout=sp.PIPE,
                  stderr=sp.PIPE, **kwargs).stdout


def release(work):
    folder = work/'release'
    folder.mkdir()
    row, = [a for a in json.loads(run('gh', 'release', 'view', TAG, '-R', REPO,
                                   '--json', 'assets'))['assets'] if a['name'] == APK]
    if row['digest'] != 'sha256:'+DIGEST or row['size'] != 53836582:
        raise RuntimeError('Official release metadata differs from pinned x86_64 build')
    run('gh', 'release', 'download', TAG, '-R', REPO, '-p', APK, '-D', folder, timeout=120)
    file = folder/APK
    if hashlib.sha256(file.read_bytes()).hexdigest() != DIGEST:
        raise RuntimeError('APK differs from published SHA-256')
    with zipfile.ZipFile(file) as z:
        z.getinfo('lib/x86_64/libmpv.so')
    return file


@contextmanager
def owned(cmd, env, logfile):
    with logfile.open('wb') as out:
        proc = sp.Popen(list(map(str, cmd)), env=env, stdin=sp.DEVNULL,
                        stdout=out, stderr=sp.STDOUT, start_new_session=True)
        try:
            yield proc
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(timeout=5)
                except sp.TimeoutExpired:
                    continue
                # Terminate helpers in the owned group even if the leader exited.
                if sig == signal.SIGTERM:
                    continue
            proc.wait(timeout=5)


def eventually(fn, limit, reason):
    end = time.monotonic()+limit
    while time.monotonic() < end:
        try:
            val = fn()
            if val:
                return val
        except (sp.CalledProcessError, sp.TimeoutExpired):
            pass
        time.sleep(.25)
    raise RuntimeError(reason)


class TV:
    def __init__(self, work, sdk, stack, gpu='swangle', ram_mib=2048, llvmpipe_threads=None):
        self.work, self.sdk, self.stack, self.gpu = work, sdk, stack, gpu
        self.ram_mib,self.llvmpipe_threads=ram_mib,llvmpipe_threads
        self.home = work/'home'; self.runtime = work/'runtime'
        for d in (self.home, self.runtime, work/'tmp'):
            d.mkdir(mode=0o700)
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(work/'tmp'),
                        ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
                        ANDROID_USER_HOME=str(self.home/'.android'),
                        ANDROID_EMULATOR_HOME=str(self.home/'.android'),
                        ANDROID_AVD_HOME=str(self.home/'.android/avd'),
                        XDG_RUNTIME_DIR=str(self.runtime),
                        PULSE_SERVER='unix:'+str(self.runtime/'pulse/native'), PULSE_SINK='fixture',
                        DBUS_SESSION_BUS_ADDRESS='unix:path=/dev/null',
                        DISPLAY='', ADB_MDNS_AUTO_CONNECT='0', ADB_LOCAL_TRANSPORT_MAX_PORT='0')
        self.env.pop('WAYLAND_DISPLAY', None)
        avd = Path(self.env['ANDROID_AVD_HOME'])/'lab.avd'; avd.mkdir(parents=True)
        source = Path.home()/'.android/avd/cleanyfin-tv.avd/config.ini'
        ini = source.read_text()
        if 'image.sysdir.1=system-images/android-36/google-tv/x86_64/' not in ini:
            raise RuntimeError('Need API 36 Google TV x86_64 AVD')
        for k,v in {'disk.dataPartition.size':'2048M', 'hw.sdCard':'no', 'sdcard.size':'0',
                    'hw.lcd.width':'1280', 'hw.lcd.height':'720', 'hw.lcd.density':'213',
                    'AvdId':'lab', 'avd.ini.displayname':'lab'}.items():
            ini = re.sub(rf'(?m)^{re.escape(k)}=.*\n?', '', ini)
            ini = ini.rstrip()+'\n'+k+'='+v+'\n'
        (avd/'config.ini').write_text(ini)
        (avd.parent/'lab.ini').write_text(f'avd.ini.encoding=UTF-8\npath={avd}\ntarget=android-36\n')
        self.ns = self.spawn(['unshare','-Urn','sh','-ec','ip link set lo up; exec sleep 1800'], 'namespace')
        eventually(lambda: os.readlink(f'/proc/{self.ns.pid}/ns/net') != os.readlink('/proc/self/ns/net'),
                   5, 'Private network namespace unavailable')
        self.prefix = ['nsenter','--preserve-credentials','-t',str(self.ns.pid),'-U','-n','--']
        eventually(lambda: b'127.0.0.1' in self.call('ip','-4','address','show','lo'), 5, 'Private loopback unavailable')
        if self.call('ip','route','show','default').strip():
            raise RuntimeError('TV namespace has external network route')

    def spawn(self, cmd, label):
        return self.stack.enter_context(owned(cmd, self.env, self.work/(label+'.log')))

    def call(self, *args, **kw):
        return run(*self.prefix, *args, env=self.env, **kw)

    def adb_args(self, *args):
        return [*self.prefix, str(self.sdk/'platform-tools/adb'), '-P', '5037', '-s', 'emulator-5580', *map(str,args)]

    def adb(self, *args, **kw):
        return run(*self.adb_args(*args), env=self.env, **kw)

    def boot(self):
        self.spawn(self.prefix+['pipewire'], 'pipewire')
        eventually(lambda: (self.runtime/'pipewire-0').is_socket(), 8, 'Private PipeWire unavailable')
        self.spawn(self.prefix+['wireplumber','-p','policy'], 'audio-policy')
        self.spawn(self.prefix+['pipewire-pulse'], 'pulse')
        eventually(lambda: self.call('pactl','info'), 8, 'Private Pulse server unavailable')
        self.call('pactl','load-module','module-null-sink','sink_name=fixture','rate=48000','channels=2')
        self.env.update(QEMU_AUDIO_DRV='pa', QEMU_PA_SERVER=self.env['PULSE_SERVER'], QEMU_PA_SINK='fixture',
                        QEMU_AUDIO_DAC_FIXED_SETTINGS='1',QEMU_AUDIO_DAC_FIXED_FREQ='48000',QEMU_AUDIO_DAC_FIXED_CHANNELS='2')
        self.spawn(self.prefix+[self.sdk/'platform-tools/adb','-L','tcp:5037','server','nodaemon'], 'adb')
        eventually(lambda: b':5037' in self.call('ss','-ltn'), 8, 'Private adb server unavailable')
        # SDK 37.2.12's -headless binary has stub PulseAudio functions. The
        # full engine supports -no-window too, and actually links libpulse.
        em=self.sdk/'emulator'; libs=em/'lib64'
        import secrets
        display=next(i for i in range(200,1000) if not Path(f'/tmp/.X11-unix/X{i}').exists() and not Path(f'/tmp/.X{i}-lock').exists())
        self.env.update(DISPLAY=f':{display}', XAUTHORITY=str(self.work/'xauthority'))
        # Cookie on stdin, not visible to other users in argv.
        self.call('xauth','-q',input=f'add :{display} . {secrets.token_hex(16)}\n'.encode())
        self.spawn(self.prefix+['Xvfb',f':{display}','-auth',self.env['XAUTHORITY'],'-screen','0','1280x720x24','-nolisten','tcp'],'display')
        eventually(lambda:self.call('xdpyinfo'),10,'Private framebuffer unavailable')
        self.env.update(ANDROID_EMULATOR_LAUNCHER_DIR=str(em), QT_QPA_PLATFORM='xcb',
                        ANDROID_EMU_CRASH_REPORTING_DATABASE=str(self.work/'emulator-crash'),
                        QT_QPA_PLATFORM_PLUGIN_PATH=str(libs/'qt/plugins/platforms'),
                        LD_LIBRARY_PATH=':'.join(map(str,[libs/'vulkan',libs/'gles_angle',libs,libs/'qt/lib'])))
        if self.llvmpipe_threads is not None:
            # Bound only the owned emulator's software raster workers, not
            # other host processes or the app's own output modes.
            self.env['LP_NUM_THREADS']=str(self.llvmpipe_threads)
        self.emu = self.spawn(self.prefix+[em/'qemu/linux-x86_64/qemu-system-x86_64','-avd','lab','-accel','on','-no-window',
                              '-no-snapshot','-no-boot-anim','-port','5580','-no-metrics','-gpu',self.gpu,'-feature','-Vulkan',
                              '-audio','pa','-crash-report-mode','disabled','-show-kernel','-memory',str(self.ram_mib),'-cores','4'], 'emulator')
        def ready():
            if self.emu.poll() is not None:
                raise RuntimeError('Emulator exited before Android boot completed')
            return self.adb('shell','getprop','sys.boot_completed', timeout=3).strip() == b'1'
        eventually(ready, 240, 'Android did not complete boot in 240 s')
        log=(self.work/'emulator.log').read_text(errors='replace')
        self.graphicsAdapter=next((m[1].strip() for m in re.finditer(r'Graphics Adapter (?!Vendor\b)([^\r\n]{1,120})',log)),None)
        self.graphicsClass='llvmpipe' if self.graphicsAdapter and 'llvmpipe' in self.graphicsAdapter.lower() else self.gpu
        if self.llvmpipe_threads is not None:
            self.graphicsClass+=f' with {self.llvmpipe_threads} requested llvmpipe workers'


# Keep Jellyfin's own network namespace closed: even the remote peer seen by its
# grant gate is loopback. The private AF_UNIX socket crosses the two namespaces.
RELAY = '''use IO::Socket::UNIX; use IO::Socket::INET;
my $l = IO::Socket::UNIX->new(Type=>SOCK_STREAM,Local=>'/config/tv.sock',Listen=>16) or die;
$SIG{CHLD}='IGNORE'; while (my $a=$l->accept()) { next if fork(); close $l;
my $b=IO::Socket::INET->new(PeerAddr=>'127.0.0.1:8096') or exit;
my $kid=fork(); my ($src,$dst)=$kid ? ($a,$b) : ($b,$a);
while (sysread($src,my $s,65536)) { while (length $s) { my $n=syswrite($dst,$s); exit unless $n; substr($s,0,$n,''); } }
shutdown($dst,1); exit; }'''


def module(name):
    import importlib.util
    if str(HERE) not in sys.path:
        sys.path.insert(0,str(HERE))
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),HERE/(name+'.py'))
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def setup_lab(work,tv,stack):
    kit=module('check-plugin')
    lab=kit.PointerLab(work/'server'); lab.work.mkdir()
    def cleanup():
        if lab.cid:
            run('docker','rm','-f',lab.cid,timeout=30)
    stack.callback(cleanup)
    lab.setup()
    lab.wholphin['tuple'].update(appBuild=DIGEST,deviceMode=f'Google TV API 36 x86_64 emulator 1280x720, {tv.ram_mib} MiB, {tv.graphicsClass}, software video vo-gpu Android GLES, stereo PCM')
    lab.wholphin['status']='experimental'
    lab.public_origin='http://10.0.2.2:18096'+kit.BASE
    lab.setup_pointer()
    cfg=lab.api('/System/Configuration',user=lab.admin)
    cfg.update(MinResumeDurationSeconds=1,MinResumePct=1,MaxResumePct=95)
    lab.api('/System/Configuration','POST',cfg,user=lab.admin)
    run('docker','exec','-d',lab.cid,'perl','-e',RELAY)
    sock=lab.work/'config/tv.sock'
    eventually(sock.is_socket,8,'Jellyfin Unix relay did not start')
    lab.wire_log=tv.work/'relay.log'
    tv.spawn(tv.prefix+[sys.executable,'-I',HERE/'tv-wire.py',str(sock),lab.item,lab.pointer],'relay')
    eventually(lambda: b'10.11.11' in tv.call('curl','--noproxy','*','-fsS','--max-time','2','http://127.0.0.1:18096'+kit.BASE+'/System/Info/Public'),10,'TV lab origin unavailable')
    return lab


APP='com.github.damontecres.wholphin'
INPUT='org.cleanyfin.fixture'

def install_input(tv):
    root=tv.work/'input'; root.mkdir(exist_ok=True)
    apk=root/'input.apk'; android=tv.sdk/'platforms/android-36/android.jar'; tools=tv.sdk/'build-tools/36.0.0'
    if not apk.exists():
        manifest=root/'AndroidManifest.xml'
        manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.cleanyfin.fixture"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/><application android:label="Disposable TV fixture input"/><instrumentation android:name=".TvInput" android:targetPackage="org.cleanyfin.fixture"/></manifest>')
        run('java','-m','jdk.compiler/com.sun.tools.javac.Main','-source','11','-target','11','-classpath',android,'-d',root,HERE/'TvInput.java')
        run(tools/'d8','--lib',android,'--min-api','26','--output',root,root/'org/cleanyfin/fixture/TvInput.class')
        plain=root/'plain.apk'; run(tools/'aapt2','link','--manifest',manifest,'-I',android,'-o',plain)
        with zipfile.ZipFile(plain,'a') as z:z.write(root/'classes.dex','classes.dex')
        key=root/'test.jks'; run('keytool','-genkeypair','-keystore',key,'-alias','fixture','-keyalg','RSA','-keysize','2048','-validity','2','-storepass','disposable','-keypass','disposable','-dname','CN=Disposable Fixture')
        run(tools/'apksigner','sign','--ks',key,'--ks-pass','pass:disposable','--out',apk,plain)
    tv.adb('install','-r','--no-streaming',apk,timeout=60)

class Screen:
    def __init__(self,tv):
        self.tv=tv

    def tree(self):
        import xml.etree.ElementTree as ET
        # Dump to the owned guest, then read the finished XML (do not parse logcat).
        self.tv.adb('shell','uiautomator','dump','/data/local/tmp/lab-ui.xml',timeout=12)
        nodes=list(ET.fromstring(self.tv.adb('exec-out','cat','/data/local/tmp/lab-ui.xml')).iter('node'))
        self.nodes=nodes
        return nodes

    def get(self,*labels,edit=None):
        nodes=self.tree()
        if edit is not None:
            inputs=[n for n in nodes if n.get('class')=='android.widget.EditText' and n.get('package')==APP]
            return inputs[edit] if len(inputs)>edit else None
        matches=[n for n in nodes if n.get('package')==APP and any(s.casefold() in {n.get(k,'').casefold() for k in ('text','content-desc')} for s in labels)]
        return min(matches,key=lambda n:(not n.get('content-desc'),n.get('clickable')!='true'),default=None)

    def input_result(self,op,value='0'):
        import base64
        val=base64.b64encode(str(value).encode()).decode()
        response=self.tv.adb('shell','am','instrument','-w','-e','op',op,'-e','value',val,INPUT+'/.TvInput',timeout=12)
        match=re.search(rb'^INSTRUMENTATION_RESULT: result=([a-z0-9:-]{1,40})\r?$',response,re.M)
        return match[1].decode() if match else 'no-result'

    def action(self,op,value='0'):
        return self.input_result(op,value)=='applied'

    def paused(self):
        return self.input_result('state').startswith('2:')  # Android PlaybackState.STATE_PAUSED.

    def activate(self,*labels):
        def step():
            n=self.get(*labels)
            if n is None:return False
            def center(n):
                l,t,r,b=map(int,re.findall(r'\d+',n.get('bounds','')));return (l+r)//2,(t+b)//2
            x,y=center(n);f=next((e for e in self.nodes if e.get('focused')=='true' and e.get('package')==APP),None)
            if f is None:self.key('DPAD_RIGHT');return False
            l,t,r,b=map(int,re.findall(r'\d+',f.get('bounds','')))
            if l<=x<=r and t<=y<=b:self.key('DPAD_CENTER');return True
            direction=('DPAD_UP' if y<t else 'DPAD_DOWN' if y>b else 'DPAD_LEFT' if x<l else 'DPAD_RIGHT')
            self.key(direction);return False
        eventually(step,28,'Focus did not reach '+str(labels))

    def dpad(self,label,*,step='DPAD_DOWN',cap=42):
        # Compose moves the selected subtree to the end of document order. A
        # visible row is not the selected row. Follow actual input focus only.
        for i in range(cap+1):
            nodes=self.tree(); focus=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            texts=([s for x in focus.iter() for s in (x.get('text',''),x.get('content-desc','')) if s] if focus is not None else [])
            if label.casefold() in [s.casefold() for s in texts]:
                self.key('DPAD_CENTER');print(f'Focused {label} after {i} {step} moves',file=sys.stderr,flush=True);return
            if i==cap:raise RuntimeError(f'No focused {label} row after {i} {step} moves')
            self.key(step)

    def wait_label(self,label):
        eventually(lambda:self.get(label) is not None,18,'Wholphin did not display '+label)

    def key(self,key): self.tv.adb('shell','input','keyevent',key)
    def text(self,text):
        if not re.fullmatch(r'[a-zA-Z0-9:/._=-]+',text): raise ValueError('Unexpected lab text')
        self.tv.adb('shell','input','text',text)

    def setup(self,lab):
        self.tv.adb('shell','am','start','-n',APP+'/.MainActivity')
        self.activate('Add Server');self.activate('Enter server address')
        eventually(lambda:self.get(edit=0) is not None,20,'Server URL field unavailable')
        self.text(lab.public_origin);self.key('ENTER');self.activate('Add User')
        def code():
            return next((n.get('text') for n in self.tree() if n.get('package')==APP and re.fullmatch(r'\d{6}',n.get('text',''))),None)
        qc=eventually(code,30,'No Wholphin Quick Connect code')
        lab.api('/QuickConnect/Authorize?code='+qc,'POST',user=lab.one)
        def session():
            return next((s for s in lab.api('/Sessions',user=lab.admin) if s.get('UserId','').replace('-','')==lab.viewer and s['Client']==lab.client and s['ApplicationVersion']==lab.version and s['DeviceId']!=lab.one[0] and s['DeviceId']!='unenrolled'),None)
        actual=eventually(session,30,'No signed-in Wholphin session of the required version')
        lab.tv_device=actual['DeviceId']; lab.policy(lab.viewer,lab.tv_device,['violence','gore']); lab.api('/Cleanyfin/lab/reconcile','POST',user=lab.admin)
        lab.tv_identity={'client':actual['Client'],'version':actual['ApplicationVersion'],
                         'deviceIdSha256':hashlib.sha256(lab.tv_device.encode()).hexdigest(),
                         'exactDeviceEnrollment':(lab.data/'profiles'/lab.viewer/'device.txt').read_text()==lab.tv_device,
                         'labTuple':lab.stub_register()['rows'][0]['tuple']}
        self.enter_advanced()
        self.dpad('ExoPlayer');self.wait_label('Prefer MPV');self.dpad('MPV',cap=5)
        self.wait_label('Edit mpv.conf');self.key('BACK');self.dpad('Advanced Settings')
        self.save_mpv(MPV_OPTIONS)

    def enter_settings(self):
        self.wait_label('Home')
        def sidebar():
            nodes=self.tree();f=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if f is not None and any((n.get('text') or n.get('content-desc')) in ('Home','Search','Favorites','Settings','Original') for n in f.iter()) and any(n.get('text')=='Settings' for n in nodes):return True
            self.key('DPAD_LEFT');return False
        eventually(sidebar,40,'D-pad did not enter the navigation drawer');self.dpad('Settings',cap=14)
        self.wait_label('Remember selected tabs')

    def enter_advanced(self):
        self.enter_settings();self.dpad('Advanced Settings');self.wait_label('Show Clock')

    def skip_forward_five_seconds(self):
        # The release's native settings slider starts at 30 seconds. Its
        # focused canvas is below the text label, not its ancestor.
        self.enter_settings()
        def bounds(n):return tuple(map(int,re.findall(r'\d+',n.get('bounds',''))))
        def focus():
            label=self.get('Skip forward')
            focused=next((n for n in self.nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if label is not None and focused is not None:
                l,t,r,b=bounds(label);x,y,w,h=bounds(focused)
                if r>l and x>=l-20 and w>r and b<=y<h<=b+50:return (l,t,r,b)
            self.key('DPAD_DOWN');return None
        try:
            l,t,r,b=eventually(focus,24,'No focused native Skip forward slider')
            def read_value():
                for n in self.tree():
                    if n.get('package')!=APP or not re.fullmatch(r'\d+ seconds?',n.get('text','')):continue
                    x,y,w,h=bounds(n)
                    if x>l and b<=y<=b+50:return int(n.get('text').split()[0])
                return None
            start=eventually(read_value,5,'Native Skip forward slider value unavailable')
            if start not in (5,30):raise RuntimeError('Unexpected native Skip forward starting value')
            for _ in range((start-5)//5):
                self.key('DPAD_LEFT');time.sleep(.2)
            eventually(lambda:read_value()==5,6,'Native Skip forward slider did not read back 5 seconds')
        finally:
            self.tv.adb('shell','am','force-stop',APP)
        return 5000

    def slow_playback(self):
        self.key('DPAD_DOWN')  # show stock playback controls while paused
        def gear():
            buttons=[]
            for node in self.tree():
                if node.get('package')!=APP or node.get('clickable')!='true':continue
                l,t,r,b=map(int,re.findall(r'\d+',node.get('bounds','')))
                # The gear is the only far-left, unlabeled button in the
                # bottom control row of the fixed 1280x720 lab display.
                if 0<=l<160 and 520<t<b<=720 and 0<r-l<180 and not (node.get('text') or node.get('content-desc')):
                    buttons.append((l,t,r,b))
            return max(buttons,key=lambda bounds:(bounds[1],-bounds[0]),default=None)
        def focus_gear():
            target=gear()
            if target is None:
                self.key('DPAD_DOWN');return False
            f=next((n for n in self.nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if f is None:
                self.key('DPAD_DOWN');return False
            l,t,r,b=target
            x,y,w,h=map(int,re.findall(r'\d+',f.get('bounds','')))
            fx,fy=(x+w)//2,(y+h)//2
            if l<=fx<=r and t<=fy<=b and w-x<180:
                self.key('DPAD_CENTER');return True
            direction=('DOWN' if fy<t else 'UP' if fy>b else 'LEFT' if fx>r else 'RIGHT')
            self.key('DPAD_'+direction);return False
        eventually(focus_gear,14,'Playback controls did not expose a focused stock settings button')
        self.wait_label('Playback Speed')
        self.dpad('Playback Speed',cap=4)
        self.wait_label('1.0')
        self.dpad('.25',step='DPAD_UP',cap=5)
        self.wait_label('Playback Speed')
        self.wait_label('0.25')
        self.key('BACK')  # Return from stock settings; controls auto-hide.
        time.sleep(5.5)  # Stock controller timeout is five seconds in this clone.
        return {'backend':'MPV','playbackSpeed':.25,'verification':'stock playback settings read back 0.25 while paused'}

    def save_mpv(self, opts):
        # Verify the persisted MPV-only editor, not the menu's initial focus.
        self.dpad('Edit mpv.conf');self.wait_label('Save')
        # Reproduce the validated native TV editor sequence; ACTION_SET_TEXT
        # opened the IME again on a later direction key on this image.
        self.tv.adb('shell','input','keycombination','113','29');self.key('DEL')
        for i,line in enumerate(opts.splitlines()):
            if i:self.key('ENTER')
            self.text(line)
            expected='\n'.join(opts.splitlines()[:i+1]);eventually(lambda:any(n.get('class')=='android.widget.EditText' and n.get('text')==expected for n in self.tree()),12,'Native editor text differs from requested options')
        self.key('BACK');self.wait_label('Save')
        self.key('DPAD_DOWN')
        try:self.dpad('Save',step='DPAD_RIGHT',cap=4)
        except RuntimeError:
            # The TV keyboard can keep focus in the text field. A label click
            # is allowed only in this dialog with the exact verified contents.
            install_input(self.tv)
            if not self.action('save',opts):raise RuntimeError('Wholphin did not expose Save for the verified mpv.conf text')
        eventually(lambda:self.get(edit=0) is None,12,'Save did not close the mpv.conf editor')
        self.dpad('Edit mpv.conf');self.wait_label('Save')
        if not any(n.get('class')=='android.widget.EditText' and n.get('text')==opts for n in self.nodes):raise RuntimeError('Reopened mpv.conf does not match')
        self.tv.adb('shell','am','force-stop',APP);self.verified={'backend':'MPV','mpvOptions':opts.splitlines(),'verification':'MPV-only preferences visible and saved mpv.conf reopened'}
        print('Verified MPV:',', '.join(opts.splitlines()),file=sys.stderr)


def play(tv,lab,item):
    from uuid import UUID
    tv.adb('shell','am','start','-n',APP+'/.MainActivity','-a',APP+'.PLAYBACK',
           '--es','itemId',str(UUID(item)),'--es','userId',str(UUID(lab.viewer)),
           '--es','serverId',str(UUID(lab.api('/System/Info/Public')['Id'])))


def now(lab,item):
    return next((s for s in lab.api('/Sessions',user=lab.admin) if s.get('DeviceId')==lab.tv_device and s.get('NowPlayingItem',{}).get('Id','').replace('-','')==item),None)


@contextmanager
def take_deadline(seconds):
    """Bound even a stuck ADB/server probe; preserve the outer run alarm."""
    class TakeExpired(Exception):
        pass
    previous=signal.getsignal(signal.SIGALRM)
    remaining,_=signal.getitimer(signal.ITIMER_REAL)
    expiry=time.monotonic()+remaining
    if remaining and remaining<=seconds:
        yield TakeExpired
        return
    def expire(_sig,_frame):
        raise TakeExpired
    signal.signal(signal.SIGALRM,expire)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:
        yield TakeExpired
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)
        if remaining:
            signal.setitimer(signal.ITIMER_REAL,max(.001,expiry-time.monotonic()))


def capture_display(tv,stack,label,limit,info):
    # Android's virtual display records the presented composition, not decoded
    # frames. MP4 preserves the observed per-sample timestamps. The legacy host
    # VP9 tap remains diagnostic fallback only when Android recording won't start.
    device_path=f'/data/local/tmp/cleanyfin-{label}.mp4'
    video=tv.work/(label+'.mp4'); log=tv.work/(label+'-display.log')
    args=['screenrecord','--verbose','--size',DISPLAY_CAPTURE_SIZE,'--bit-rate','4000000',
          '--time-limit',str(math.ceil(limit+8)),device_path]
    shell='printf "%s\\n" "$$"; exec '+shlex.join(args)
    def console(*args):
        result=tv.adb('emu','screenrecord',*args,timeout=5)
        return b'OK' in result and b'KO:' not in result
    with ExitStack() as trial:
        proc=trial.enter_context(owned(tv.adb_args('shell','sh','-c',shlex.quote(shell)),tv.env,log))
        def identify():
            lines=log.read_bytes().splitlines()
            return lines[0].decode() if lines and lines[0].isdigit() else None
        def finish():
            pid=identify()
            if pid and proc.poll() is None:
                try: tv.adb('shell','kill','-INT',pid,timeout=3)
                except (sp.CalledProcessError,sp.TimeoutExpired):pass
            try:proc.wait(timeout=6)
            except sp.TimeoutExpired:info['displayStop']='Android recorder did not acknowledge SIGINT'
            try:tv.adb('pull',device_path,video,timeout=12)
            except (sp.CalledProcessError,sp.TimeoutExpired):pass
            finally:
                try:tv.adb('shell','rm','-f',device_path,timeout=3)
                except (sp.CalledProcessError,sp.TimeoutExpired):pass
        trial.callback(finish)
        def ready():
            if proc.poll() is not None:raise RuntimeError('Android compositor recorder exited before playback')
            return identify() and tv.adb('shell','test','-f',device_path,timeout=2)==b''
        try: eventually(ready,8,'Android compositor recorder did not open its MP4')
        except RuntimeError as exc:info['displayFallbackReason']=str(exc)
        else:
            stack.enter_context(trial.pop_all())
            info['displayTap']=f'Android compositor screenrecord {DISPLAY_CAPTURE_SIZE} MP4'
            return video
    video=tv.work/(label+'.webm')
    if not console('start','--size','640x360','--time-limit',str(math.ceil(limit+8)),'--fps','60',str(video)):
        raise RuntimeError('Neither Android compositor nor host display recorder started')
    def stop_console():
        try:console('stop')
        except (sp.CalledProcessError,sp.TimeoutExpired):pass
    stack.callback(stop_console)
    info['displayTap']='Emulator host screenrecord 640x360 VP9, 60 fps requested'
    return video


def app_runtime(logs,info):
    a=re.findall(r'\bcplayer,\s+AO:\s+\[(audiotrack)\]\s+(\d+Hz stereo 2ch [\w-]+)',logs)
    v=re.findall(r'\bcplayer,\s+VO:\s+\[(gpu)\]\s+(\d+x\d+ [\w-]+)',logs)
    if a:info['libmpvAO']=' '.join(a[-1])
    if v:info['libmpvVO']=' '.join(v[-1])
    info['audioUnderruns']=logs.count('cplayer, Audio device underrun detected')
    info['libmpvEof']=bool(re.search(r'\bcplayer,\s+EOF code:\s*1\b|\bMPV_EVENT_END_FILE:\s+0\s+0\b',logs))


def player_surface(tv):
    layers=tv.adb('shell','dumpsys','SurfaceFlinger','--list',timeout=2).decode(errors='replace')
    return any(APP in line and 'SurfaceView' in line for line in layers.splitlines())


def record(tv,lab,item,label,speed,info,begin=None,*,display=True,sound=True):
    audio=tv.work/(label+'.pcm') if sound else None
    video=None
    duration=20 if item==lab.item else 14.96
    limit=duration/speed+MEDIA_MARGIN_SECONDS
    info.update(speed=speed,fixtureDurationS=duration,wallCeilingS=limit,endSignal='fixture duration plus startup margin (no end signal)')
    with ExitStack() as s:
        if sound:
            mon=s.enter_context(audio.open('wb')); err=s.enter_context((tv.work/(label+'-audio.log')).open('wb'))
            proc=sp.Popen(tv.prefix+['parec','--device=fixture.monitor','--format=s16le','--rate=48000','--channels=2','--raw','--latency-msec=20'],env=tv.env,stdout=mon,stderr=err)
            def stop():
                proc.terminate()
                try: proc.wait(timeout=5)
                except sp.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            s.callback(stop)
            eventually(lambda:audio.stat().st_size>=38400,12,'Private monitor did not yield 200 ms of PCM')
        if display:
            video=capture_display(tv,s,label,limit,info)
            # An opened MP4 only certifies that the recorder created its file.
            # Allow composition/encoding before playing; trim by barcode later.
            time.sleep(DISPLAY_PREROLL_SECONDS)
            info['displayPrerollS']=DISPLAY_PREROLL_SECONDS
        if begin is None:
            tv.adb('logcat','-c')  # Only this private guest, before each playback.
        launch=time.monotonic(); pid=''; saw_session=False; saw_surface=False
        with take_deadline(limit) as expired:
            try:
                if begin is None:play(tv,lab,item)
                else:begin()  # Player already paused at the first witnessed frame.
                while True:
                    if tv.emu.poll() is not None:raise RuntimeError('Owned Android emulator exited during capture')
                    try:
                        if not pid:
                            pid=tv.adb('shell','pidof',APP,timeout=2).decode().strip()
                        if pid.isdecimal():
                            logs=tv.adb('logcat','-d','--pid='+pid,'-v','tag',timeout=2).decode(errors='replace')
                            app_runtime(logs,info)
                        if info.get('libmpvEof'):
                            info['endSignal']='libmpv EOF';break
                        sess=now(lab,item)
                        if sess:
                            saw_session=True
                            method=sess.get('PlayState',{}).get('PlayMethod')
                            if method:info['playMethod']=method
                            if not info.get('activeQemuAudioRoute'):
                                info['activeQemuAudioRoute']=audio_route(tv)
                        elif saw_session:
                            info['endSignal']='Jellyfin session no longer reports the item';break
                        if info.get('libmpvVO'):
                            active=player_surface(tv)
                            if saw_surface and not active:
                                info['endSignal']='Wholphin video surface left foreground';break
                            saw_surface|=active
                    except (sp.CalledProcessError,sp.TimeoutExpired):
                        pass  # A missing probe never substitutes for EOF.
                    time.sleep(1.25)
            except expired:
                info['captureDeadline']=timeout_label(label+' playback limit',tv.host_history)
        info['watchedS']=round(time.monotonic()-launch,2)
        # Keep each tap past the observed end; monitor wall time and PCM length
        # can diverge when the guest or sink is briefly starved.
        time.sleep(3 if sound else .75)
        if pid.isdecimal():
            try:
                logs=tv.adb('logcat','-d','--pid='+pid,'-v','threadtime',timeout=3)
                (tv.work/(label+'-logcat.log')).write_bytes(logs)
                app_runtime(logs.decode(errors='replace'),info)
            except (sp.CalledProcessError,sp.TimeoutExpired):pass
    if video is not None:info['videoBytes']=video.stat().st_size if video.is_file() else 0
    if audio is not None:
        info['audioBytes']=audio.stat().st_size
        info['audioDurationS']=round(info['audioBytes']/192000,3)
    info['runtimeVerified']=bool(info.get('libmpvAO') and info.get('libmpvVO') and info.get('playMethod')=='DirectPlay')
    print(f"{label}: {speed:g}x, {info['endSignal']}, watched {info['watchedS']:g}s; DirectPlay and libmpv AO/VO verified: {info['runtimeVerified']}",file=sys.stderr,flush=True)
    return video,audio


def handoff(lab):
    logs=run('docker','logs',lab.cid).decode(errors='replace')
    routes=[logs.count(t) for t in ('Fixture POST /Items/{pointer}/PlaybackInfo 200','Fixture GET /Cleanyfin/play/{S}/list.edl 200','Fixture GET /Cleanyfin/play/{S}/source -> /Videos/{original}/stream 206')]
    reasons=re.findall(r'Fixture projection refused: ([A-Za-z][A-Za-z0-9]{1,64})\b',logs)
    return routes,reasons


def configure_audio(tv):
    volume=['shell','cmd','media_session','volume','--stream','3']
    read=lambda: tv.adb(*volume,'--get').decode(errors='replace')
    vals=re.search(r'volume is (\d+) in range \[(\d+)\.\.(\d+)\]',read(),re.I)
    result={}
    if vals:
        result['androidMusicVolumeBefore'],_,maximum=map(int,vals.groups())
        tv.adb(*volume,'--set',str(maximum))
        after=re.search(r'volume is (\d+)',read(),re.I)
        result['androidMusicVolumeAfter']=int(after[1]) if after else None
        result['androidMusicVolumeMax']=maximum
        if result['androidMusicVolumeAfter'] != maximum:
            tv.adb('shell','input','keyevent',*(['KEYCODE_VOLUME_UP']*maximum),timeout=12)
            keys=re.search(r'volume is (\d+)',read(),re.I)
            result['androidMusicVolumeAfterKeys']=int(keys[1]) if keys else None
    else:
        result['androidMusicVolumeReadback']='unavailable; not assuming unity'
    tv.call('pactl','set-sink-volume','fixture','100%')
    tv.call('pactl','set-source-volume','fixture.monitor','100%')
    inputs=tv.call('pactl','list','short','sink-inputs').decode().splitlines()
    if len(inputs)==1:
        stream=inputs[0].split()[0]
        tv.call('pactl','set-sink-input-volume',stream,'100%')
        rows=json.loads(tv.call('pactl','-f','json','list','sink-inputs'))
        row=next((x for x in rows if str(x.get('index'))==stream),{})
        result['privateQemuStreamVolume']={k:v.get('value_percent') for k,v in row.get('volume',{}).items()}
    return result


def audio_route(tv):
    rows=json.loads(tv.call('pactl','-f','json','list','sink-inputs',timeout=2))
    sinks={r['index']:r['name'] for r in json.loads(tv.call('pactl','-f','json','list','sinks',timeout=2))}
    qemu=[r for r in rows if r.get('properties',{}).get('application.process.binary','').startswith('qemu-system-')]
    return [{'sink':sinks.get(r['sink']),'mute':r.get('mute'),'corked':r.get('corked')} for r in qemu]


def has_routes(info):
    return len(info.get('grantRouteDeltas',[]))==3 and min(info['grantRouteDeltas'])>0


def shown_frame(tv,scorer):
    data=tv.adb('exec-out','screencap','-p',timeout=8)
    raw=run('ffmpeg','-v','error','-f','image2pipe','-i','pipe:0','-vf','scale=160:90:flags=area,format=gray','-frames:v','1','-threads:v','1','-f','rawvideo','pipe:1',input=data,timeout=5)
    return scorer.read_barcode(raw,500) if len(raw)==14400 else None


def prepare_slow(tv,lab,ui,item,scorer,info):
    tv.adb('logcat','-c')
    play(tv,lab,item)
    eventually(lambda:now(lab,item),15,'Quarter-speed setup: no native playback session')
    def command(op,value='0'):
        if not ui.action(op,value):raise RuntimeError('Quarter-speed setup: stock Android MediaSession '+op+' unavailable')
    command('pause')
    eventually(ui.paused,8,'Quarter-speed setup: stock pause not acknowledged')
    info['speedSetting']=ui.slow_playback()
    if not ui.paused():raise RuntimeError('Quarter-speed setup: player left paused state')
    # The stock MPV player does not advertise MediaSession seeking. With
    # overlays dismissed, Wholphin's left key takes its own rewind path.
    if shown_frame(tv,scorer)!=0:
        ui.key('DPAD_LEFT')
        ui.key('DPAD_LEFT')  # Two default 10 s steps cover the whole fixture.
    eventually(lambda:shown_frame(tv,scorer)==0,10,'Quarter-speed setup: paused rewind did not present frame zero')
    return lambda:command('play')


def measure(tv,lab,ui,out,split):
    from tv_score import picture,audio,compare
    scorer=module('analyze-capture'); takes=out['recordings']={}
    out['audioLevels']=configure_audio(tv)
    print('Private audio setup:',json.dumps(out['audioLevels']),file=sys.stderr,flush=True)
    def score(key,scorer_fn,*args):
        try:out[key]=scorer_fn(*args)
        except (ValueError,OSError,sp.CalledProcessError,sp.TimeoutExpired) as exc:
            msg=timeout_label(key,tv.host_history) if isinstance(exc,sp.TimeoutExpired) else str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            out.setdefault('scoreIssues',{})[key]=msg
    def take(label,item,speed,*,sound=False,display=True,filtered=False):
        out['milestone']=label
        before=handoff(lab) if filtered else None
        offset=lab.wire_log.stat().st_size
        info=takes[label]={'speed':speed}
        try:
            # Keep Activity cold-start work out of the first barcode frames.
            if speed==1:tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity')
            begin=prepare_slow(tv,lab,ui,item,scorer,info) if speed!=1 else None
        except RuntimeError as exc:
            if not str(exc).startswith(('Quarter-speed setup:','Playback controls did not expose','Wholphin did not display','No focused')):raise
            info['setupLimit']=str(exc)
            tv.adb('shell','am','force-stop',APP)
            print(f'{label}: {exc}',file=sys.stderr,flush=True)
            return None
        v,a=record(tv,lab,item,label,speed,info,begin,display=display,sound=sound)
        with lab.wire_log.open() as log:
            log.seek(offset)
            info['observedPlaybackInfo']=[json.loads(line) for line in log if line.startswith('{') and line.endswith('\n')]
        if filtered:
            routes,reasons=handoff(lab)
            info['grantRouteDeltas']=[b-a for a,b in zip(before[0],routes)]
            info['serverLoggedRefusalCodes']=reasons[len(before[1]):]
            if not has_routes(info):
                print('Filtered request:',json.dumps(info['observedPlaybackInfo']),
                      '; logged refusal codes:',info['serverLoggedRefusalCodes'],file=sys.stderr,flush=True)
        # Stop player work before running host-side analysis; all captures are
        # already finalized, and each next playback starts independently.
        tv.adb('shell','am','force-stop',APP)
        if display:score(label+'Picture',picture,v,scorer)
        if sound:score(label+'Sound',audio,a,scorer)
        if 'captureDeadline' in info:
            if info['captureDeadline'].startswith('Host too busy'):raise RuntimeError(info['captureDeadline'])
            print(info['captureDeadline'],file=sys.stderr,flush=True)
        return a
    ca=take('control',lab.item,1,sound=True,display=not split)
    cb=take('repeatControl',lab.item,1,sound=True,display=not split)
    out['milestone']='PCM reference origin and repeatability'
    refs=None
    if all(label+'Sound' in out and takes[label].get('runtimeVerified') and not takes[label].get('captureDeadline') for label in ('control','repeatControl')):
        try:
            refs=scorer.media(lab.work/'fixture/source.mp4','ffmpeg','-i','/lab','-map','0:a:0','-f','s16le','-ac','2','-ar','48000','pipe:1',readonly=True)
        except (sp.CalledProcessError,sp.TimeoutExpired):
            out.setdefault('scoreIssues',{})['pcmReference']='Could not decode source onset witness'
        if refs is not None:
            noise={label:out[label+'Sound']['preRollPeakLSB'] for label in ('control','repeatControl')}
            score('waveform',compare,ca,cb,None,refs,noise)
            floor=out.get('waveform',{}).get('repeatability',{})
            if floor:
                def detail(k):
                    f=floor[k]; rms='/'.join(f'{v:g}' for v in f['errorRmsLSB'])
                    return f"{f['differentSampleFrames']}/{f['comparedSampleFrames']} changed frames, {rms} L/R LSB RMS"
                print('Uncut PCM repeatability: 20 ms before:',detail('joinBefore20ms'),
                      '; 20 ms after:',detail('joinAfter20ms'),file=sys.stderr,flush=True)
    fa=take('filtered',lab.pointer,1,sound=True,display=not split,filtered=True)
    out['milestone']='filtered PCM comparison'
    if refs is not None and 'filteredSound' in out and takes['filtered'].get('runtimeVerified') and has_routes(takes['filtered']):
        noise['filtered']=out['filteredSound']['preRollPeakLSB']
        score('waveform',compare,ca,cb,fa,refs,noise)
    control_names=('control','repeatControl')
    picture_cut='filtered'
    if split:
        control_names=('controlDisplay',)
        picture_cut='filteredDisplay'
        take('controlDisplay',lab.item,1)
        take(picture_cut,lab.pointer,1,filtered=True)
    real=any(picture_calibrated(out.get(label+'Picture',{}),takes[label]) for label in control_names)
    cut=out.get(picture_cut+'Picture',{})
    need_slow_cut=(has_routes(takes[picture_cut]) and not cut.get('marked') and
                   not cut.get('filteredComplete') and not picture_resolved(cut,1))
    if not real or need_slow_cut:
        take('slowControl',lab.item,PICTURE_SPEED)
        if takes['slowControl'].get('speedSetting'):
            out['verifiedSlowSettings']=takes['slowControl']['speedSetting']
        if has_routes(takes['filtered']) and picture_calibrated(out.get('slowControlPicture',{}),takes['slowControl']):
            take('slowFiltered',lab.pointer,PICTURE_SPEED,filtered=True)
    if has_routes(takes['filtered']):
        out['milestone']='seeks and persisted resume'
        controls(tv,lab,ui,out)


def controls(tv,lab,ui,out):
    """Exercise stock D-pad seeks and the player's visible Jellyfin Resume choice."""
    from uuid import UUID
    scorer=module('analyze-capture'); receipt=out['controlOperations']={}
    def witness(fn,limit,reason):
        try:return eventually(fn,limit,reason)
        except RuntimeError as exc:
            if str(exc)!=reason:raise
            receipt.setdefault('limits',[]).append(reason)
            return None
    receipt['transport']='Stock Wholphin D-pad seeks, Android MediaSession pause, TV Stop key'
    tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity')
    try:receipt['verifiedSkipForwardMs']=ui.skip_forward_five_seconds()
    except RuntimeError as exc:
        receipt['seekLimit']=str(exc)
        tv.adb('shell','am','force-stop',APP)
    def ticks():
        return lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
    receipt['bookmarkBeforeTicks']=ticks()
    tv.adb('logcat','-c')
    play(tv,lab,lab.pointer)
    if not witness(lambda:now(lab,lab.pointer),20,'Control playback did not start'):return
    def shown():return shown_frame(tv,scorer)
    if 'verifiedSkipForwardMs' in receipt:
        receipt['pauseRequest']=ui.input_result('pause')
        receipt['pauseAcknowledged']=bool(receipt['pauseRequest']=='applied' and witness(ui.paused,8,'Stock Android player did not acknowledge pause'))
        if receipt['pauseAcknowledged']:
            ui.key('DPAD_LEFT');ui.key('DPAD_LEFT')
            receipt['rewindAnchor']=bool(witness(lambda:shown()==0,5,'Stock rewind did not present frame zero'))
        if receipt.get('rewindAnchor'):
            for direction,ms in (('RIGHT',5000),('RIGHT',10000),('LEFT',0))*2:
                target=ms//40+(126 if ms>=5480 else 0)
                ui.key('DPAD_'+direction)
                observed=[]
                def landed():
                    n=shown()
                    if n is not None:observed.append(n)
                    return n==target
                exact=bool(witness(landed,5,f'No exact paused seek landing at {ms} ms'))
                receipt.setdefault('pausedSeeks',[]).append(dict(filteredMs=ms,expectedFrame=target,observed=observed,exact=exact,request=direction))
                if not exact:break  # Relative expectations cannot be chained from an unverified position.
        # Resume the same stock player and stop past the cut, away from EOF.
        if receipt['pauseAcknowledged']:ui.action('play')
    receipt['preStopReadings']=[]
    def beyond_cut():
        n=shown()
        if n is not None:receipt['preStopReadings'].append(n)
        return n if n is not None and 280<=n<=410 else None
    receipt['preStopFrame']=witness(beyond_cut,12,'No kept frame sufficiently far past the cut before Stop')
    session=now(lab,lab.pointer)
    if not session:
        receipt.setdefault('limits',[]).append('Player left before stock Stop could be sent');return
    receipt['preStopSessionTicks']=session.get('PlayState',{}).get('PositionTicks')
    ui.key('KEYCODE_MEDIA_STOP')
    receipt['stopAcknowledged']=bool(witness(lambda:not now(lab,lab.pointer),10,'No Jellyfin session clear after Stop'))
    try:
        pid=tv.adb('shell','pidof',APP,timeout=2).decode().strip()
        if pid.isdecimal():
            logs=tv.adb('logcat','-d','--pid='+pid,'-v','tag',timeout=3).decode(errors='replace')
            receipt['reportedStopPositions']=[pos for item,pos in re.findall(r'reportPlaybackStopped for ([a-fA-F0-9-]{32,36}) at ([-0-9. hms]{1,24})(?:\r?\n|$)',logs) if item.replace('-','').lower()==lab.pointer]
    except (sp.CalledProcessError,sp.TimeoutExpired):pass
    def new_ticks():
        value=ticks()
        return value if value>0 and value!=receipt['bookmarkBeforeTicks'] else None
    receipt['bookmarkTicks']=witness(new_ticks,10,'No new persisted pointer bookmark after Stop')
    if not receipt['bookmarkTicks']:return
    tv.adb('shell','am','force-stop',APP)
    tv.adb('shell','am','start','-n',APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.api('/System/Info/Public')['Id'])))
    matches=lambda:[n for n in ui.tree() if n.get('package')==APP and any('resume' in n.get(k,'').lower() for k in ('text','content-desc'))]
    nodes=witness(matches,15,'No actual Resume choice after stopping Wholphin')
    if not nodes:return
    n=nodes[0];label=n.get('content-desc') or n.get('text')
    before=handoff(lab)[0]
    tv.adb('logcat','-c')
    info=receipt['resumeTake']={}
    try:video,_=record(tv,lab,lab.pointer,'resume',1,info,lambda:ui.activate(label),sound=False)
    except RuntimeError as exc:
        if not str(exc).startswith('Focus did not reach'):raise
        receipt.setdefault('limits',[]).append('Resume choice visible but did not receive focus');return
    info['grantRouteDeltas']=[b-a for a,b in zip(before,handoff(lab)[0])]
    from tv_score import picture
    try:pic=picture(video,scorer)
    except (ValueError,OSError,sp.CalledProcessError,sp.TimeoutExpired):
        receipt.setdefault('limits',[]).append('Resume display capture unreadable');return
    tick=receipt['bookmarkTicks']
    receipt['expectedResumeFrame']=tick//400000+(126 if tick>=54800000 else 0)
    receipt['resumePicture']={key:pic.get(key) for key in ('first','last','maxCaptureGapMs','interiorUnreadable')}
    print('Resume observation:',json.dumps({k:receipt[k] for k in ('bookmarkTicks','expectedResumeFrame','resumePicture')}),file=sys.stderr,flush=True)


CLAIMS=('filtered handoff','picture control','marked picture','kept picture at join','real-speed join','sound control','marked sound','kept tones','kept PCM at join','seek forward/back','persisted resume')


def speed_witnessed(pic,info):
    expected=info['fixtureDurationS']/info['speed']
    return .85*expected<=pic.get('witnessSpanS',0)<=1.15*expected


def picture_calibrated(pic,info):
    return bool(info.get('runtimeVerified') and pic.get('controlComplete') and speed_witnessed(pic,info))


def picture_resolved(pic,speed):
    return (pic.get('maxCaptureGapMs',float('inf'))<40/speed and not pic.get('interiorUnreadable') and
            pic.get('first')==0 and pic.get('last')==499)


def live_pcm(take):
    return (take.get('runtimeVerified') and not take.get('captureDeadline') and
            take.get('activeQemuAudioRoute')==[{'sink':'fixture','mute':False,'corked':False}])


def compact_frames(frames):
    from hard_fixture_lab import ranges
    return ','.join(str(a) if a==b else f'{a}–{b}' for a,b in ranges(frames)) or 'none'


def verdicts(raw,reason):
    out={k:('not-measured',reason or 'No calibrated capture') for k in CLAIMS}
    takes=raw.get('recordings',{}); normal=takes.get('control',{}); sound_cut=takes.get('filtered',{})
    cut=takes.get('filteredDisplay',sound_cut); display_normal=takes.get('controlDisplay',normal)
    c=raw.get('controlDisplayPicture',raw.get('controlPicture',{}))
    f=raw.get('filteredDisplayPicture',raw.get('filteredPicture',{}))
    a=raw.get('controlSound',{});b=raw.get('filteredSound',{});w=raw.get('waveform',{})
    sc=raw.get('slowControlPicture',{});sf=raw.get('slowFilteredPicture',{}); slow=takes.get('slowControl',{}); slow_cut=takes.get('slowFiltered',{})
    if 'grantRouteDeltas' in sound_cut:
        out['filtered handoff']=('PASS' if has_routes(sound_cut) else 'FAIL',
            f"Pointer POST / list GET / original range GET: {sound_cut['grantRouteDeltas']}; 1x")
    rep=takes.get('repeatControl',{}); rc=raw.get('repeatControlPicture',{}); ar=raw.get('repeatControlSound',{})
    def coverage(pic,info):
        return (f"{pic['distinct']}/500, {pic['marked']}/126 marked at {info['speed']:g}x "
                f"({pic.get('witnessSpanS','unknown')} s span, {pic.get('maxCaptureGapMs','unknown')} ms max gap)")
    sizes=[coverage(pic,info) for pic,info in ((c,display_normal),(rc,rep),(sc,slow)) if pic]
    c,normal_pic=next(((pic,info) for pic,info in ((c,display_normal),(rc,rep)) if picture_calibrated(pic,info)),(c,display_normal))
    real_valid=picture_calibrated(c,normal_pic); slow_valid=picture_calibrated(sc,slow)
    if sizes:
        out['picture control']=('PASS' if real_valid or slow_valid else 'FAIL','; '.join(sizes))
    if real_valid or slow_valid:
        use_slow=slow_valid and (not real_valid or (not f.get('marked') and not f.get('filteredComplete') and not picture_resolved(f,1)))
        pic,info,source_info=(sf,slow_cut,slow) if use_slow else (f,cut,normal_pic)
        if pic and info.get('runtimeVerified') and has_routes(info) and source_info['displayTap']==info['displayTap'] and speed_witnessed(pic,info):
            gap=pic.get('maxCaptureGapMs',float('inf')); speed=info['speed']
            resolved=picture_resolved(pic,speed)
            limit=f"{speed:g}x display, {pic.get('witnessSpanS','unknown')} s span, max gap {gap:g} ms"
            out['marked picture']=('FAIL' if pic['marked'] else ('PASS' if resolved else 'not-measured'),
                                   f"{pic['marked']} distinct marked; {limit}")
            fail_frames=pic['missingKept']; note=f"; missing {compact_frames(fail_frames)}" if resolved and fail_frames else ''
            out['kept picture at join']=('PASS' if pic['filteredComplete'] else 'FAIL' if resolved else 'not-measured',
                                        f"{374-len(fail_frames)}/374 kept; 136→263 {pic['joinAdjacent']}; {limit}{note}")
        else:
            for k in ('marked picture','kept picture at join'):out[k]=('not-measured','No matching filtered tap, libmpv output or handoff receipts')
    elif sizes:
        for k in ('marked picture','kept picture at join'):out[k]=('not-measured','Uncut display did not witness all 500 in order on a verified tap')
    if f and cut.get('runtimeVerified') and has_routes(cut):
        hold=f.get('joinTiming',{}).get('136-263'); ref=c.get('joinTiming',{}).get('136-137')
        status='not-measured'; timing='join neighbours not fully bracketed'
        same_tap=normal_pic.get('runtimeVerified') and normal_pic['displayTap']==cut['displayTap']
        if hold and ref:
            low=hold['lowerMs']-ref['upperMs'];high=hold['upperMs']-ref['lowerMs']
            timing=f'{low:g}–{high:g} ms extra hold vs uncut; 40 ms stall threshold'
            if same_tap:
                status='FAIL' if low>=40 else 'PASS' if high<40 and real_valid and picture_resolved(f,1) else 'not-measured'
        if same_tap and f['marked']:status='FAIL'
        out['real-speed join']=(status,f"{timing}; sampled {374-len(f['missingKept'])}/374 kept, {f['marked']} marked at 1x")
    if a:
        ct=a['tones']; rt=ar.get('tones',[])
        both=bool(rt and live_pcm(normal) and live_pcm(rep))
        marked_ok=both and all(abs(t['880']-5040)<=5 for t in ct+rt)
        kept_ok=both and all(t['440']>=5475 and t['660']>=9475 for t in ct+rt)
        lr=lambda vals:'/'.join(f'{x:g}' for x in vals) or 'unavailable'
        matches=lambda tones,hz:lr([t[hz] for t in tones])
        peak=lr(a['capturePeakLSB']); noise=lr([a['preRollRmsLSB']]+([ar['preRollRmsLSB']] if ar else []))
        out['sound control']=('PASS' if marked_ok else 'FAIL',f"880 Hz L/R {matches(ct,'880')} and {matches(rt,'880')} ms (expected 5040); 48 kHz stereo, pre-roll {noise} LSB RMS, peak {peak} LSB; 5 ms at 0.10 FS")
        filtered=bool(b and live_pcm(sound_cut) and has_routes(sound_cut))
        if not marked_ok:
            out['marked sound']=('not-measured','Two uncut controls did not calibrate the known marked tone')
        elif not filtered:
            out['marked sound']=('not-measured','No verified filtered render')
        else:
            vals=b['tones']; leak=any(t['880'] for t in vals)
            out['marked sound']=('FAIL' if leak else 'PASS' if w.get('valid') and all(t['660'] for t in vals) else 'not-measured',f"880 Hz L/R {matches(vals,'880')} ms at >=0.10 FS in 5 ms windows")
        if not kept_ok:
            out['kept tones']=('not-measured',f"Uncut 440/660 Hz L/R {matches(ct,'440')}, {matches(ct,'660')} ms; repeat {matches(rt,'440')}, {matches(rt,'660')} ms (expected 5480, 9480)")
        elif not filtered:
            out['kept tones']=('not-measured','No verified filtered render')
        else:
            def loss(hz):
                delta=[x[hz]-y[hz] for x,y in zip(ct,b['tones'])]
                floor=[max(5,abs(x[hz]-r[hz])) for x,r in zip(ct,rt)]
                return any(d>f for d,f in zip(delta,floor)),f'{hz} Hz loss L/R {lr(delta)} ms (floor {lr(floor)})'
            lose440,msg440=loss('440');lose660,msg660=loss('660')
            out['kept tones']=('FAIL' if lose440 or lose660 else 'PASS',f'{msg440}; {msg660}; 5 ms windows')
        if w.get('valid') and filtered:
            obs=w['controlVsFiltered'];base=w['repeatability'];before,after='joinBefore20ms','joinAfter20ms'
            def changed(d,k):return d[k]['differentSampleFrames']
            def region(k,offset):
                x=obs[k]; first=x['firstDifferentFrame']
                where='' if first is None else f" [samples {first+offset}…{x['lastDifferentFrame']+offset}]"
                return (f"{changed(obs,k)}/960 ({x['differentDurationMs']:g} ms){where}, "
                        f"{lr(x['errorRmsLSB'])} LSB RMS vs floor {lr(base[k]['errorRmsLSB'])}")
            join=[obs[before],obs[after]]
            definite=any(not x['withinFloor'] and x['floorBelowSignal'] for x in join)
            bounded=all(x['withinFloor'] and x['floorBelowSignal'] for x in join)
            status='FAIL' if definite else 'PASS' if bounded else 'not-measured'
            out['kept PCM at join']=(status,f"Changed stereo frames: before {region(before,-960)}; after {region(after,0)}; prefix/suffix {changed(obs,'prefix')}/{changed(obs,'suffix')} (repeat {changed(base,'prefix')}/{changed(base,'suffix')}); frozen onset")
        elif w:
            out['kept PCM at join']=('not-measured',w.get('reason','Filtered audio did not complete on the calibrated tap'))
        else:
            out['kept PCM at join']=('not-measured','No second verified uncut PCM render for repeatability')
    ops=raw.get('controlOperations',{});seeks=ops.get('pausedSeeks',[])
    if seeks:
        n=sum(x['exact'] for x in seeks); obs=f"{n}/6 exact paused landings ({len(seeks)} attempted); samples, not travel"
        out['seek forward/back']=('PASS' if n==6 else 'FAIL' if (real_valid or slow_valid) else 'not-measured',obs)
    elif ops.get('seekLimit') or ops.get('limits'):
        out['seek forward/back']=('not-measured',ops.get('seekLimit') or ops['limits'][0])
    if 'resumePicture' in ops:
        r=ops['resumePicture']; info=ops['resumeTake']; want=ops['expectedResumeFrame']; got=r['first']
        ok=(ops['stopAcknowledged'] and info.get('runtimeVerified') and has_routes(info) and
            (real_valid or slow_valid) and got==want and not r['interiorUnreadable'])
        out['persisted resume']=('PASS' if ok else 'not-measured',f"Stored {ops['bookmarkTicks']} filtered ticks; first readable Resume frame {got}, expected {want}; capture cannot exclude earlier unreadable frames")
    elif ops.get('limits'):out['persisted resume']=('not-measured',ops['limits'][-1])
    return out


def allocated(root):
    total=0
    for path,dirs,files in os.walk(root):
        for n in files:
            try:total+=os.lstat(os.path.join(path,n)).st_blocks*512
            except FileNotFoundError:pass
    return total


def main():
    import resource,threading
    from datetime import datetime,timezone
    from contextlib import redirect_stdout
    import secrets
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep',action='store_true',help='retain private synthetic data and credentials (never running services)')
    parser.add_argument('--json',action='store_true',help='print a single JSON object on stdout')
    parser.add_argument('--gpu',choices=('swangle','host'),default='swangle',help='declared emulator graphics mode')
    parser.add_argument('--llvmpipe-threads',type=int,choices=(2,4),help='bound software raster workers in the owned emulator only')
    parser.add_argument('--ram-mib',type=int,choices=(1536,1792,2048),default=2048,help='guest RAM; part of the measured device mode')
    parser.add_argument('--split-capture',action='store_true',help='diagnostic: use separate 1x audio and display takes, without changing player outputs')
    opts=parser.parse_args()
    if not __debug__:parser.error('Python assertions are required')
    if opts.llvmpipe_threads is not None and opts.gpu!='host':parser.error('--llvmpipe-threads requires --gpu host')
    start=time.monotonic(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); uid=secrets.token_hex(5)
    work=Path(tempfile.mkdtemp(prefix='cleanyfin-tv-'));sdk=Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk').resolve()
    output=HERE.parents[1]/'delivery/evidence/runs'/('tv-'+stamp+'-'+uid)
    summary={'startedUtc':stamp,'recording':'Synthetic fixture only; Android compositor display (host recorder fallback), isolated PipeWire sink monitor s16le stereo 48000 Hz'}
    from collections import deque
    raw=summary['observations']={};row=None;error=None;peak=0;over=threading.Event();done=threading.Event()
    history=deque(maxlen=5);worst_io=host_health()
    worst_scoped={k:worst_io[k] for k in ('scopeMemoryFull10sPct','scopeIOFull10sPct')}
    def budget():
        nonlocal peak,worst_io
        while not done.is_set():
            health=host_health();history.append(health)
            if (health['ioFull10sPct'] or 0)>(worst_io['ioFull10sPct'] or 0):worst_io=health
            for key in ('scopeMemoryFull10sPct','scopeIOFull10sPct'):
                if (health[key] or 0)>(worst_scoped[key] or 0):worst_scoped[key]=health[key]
            peak=max(peak,allocated(work))
            if peak>6_000_000_000:
                over.set();os.kill(os.getpid(),signal.SIGTERM);return
            done.wait(1.5)
    def interrupted(sig,frame):
        raise RuntimeError('Scratch budget exceeded' if over.is_set() else
                           timeout_label(raw.get('milestone','setup'),list(history)) if sig==signal.SIGALRM else 'Run interrupted')
    for sig in (signal.SIGTERM,signal.SIGALRM,signal.SIGINT):signal.signal(sig,interrupted)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));watcher=threading.Thread(target=budget,daemon=True);watcher.start()
    try:
        summary['hostBeforeRun']=wait_for_host()
        signal.alarm(1200)
        with ExitStack() as stack, redirect_stdout(sys.stderr):
            # Every Docker child, including fixture-generator subprocesses, gets
            # an ownership label so an interrupted image build is collected too.
            docker=shutil.which('docker')
            if not docker:raise RuntimeError('Docker unavailable')
            wrappers=work/'bin';wrappers.mkdir();wrapper=wrappers/'docker';label='org.cleanyfin.tv-run='+uid
            wrapper.write_text('#!/bin/sh\ncase "$1" in run|create) verb=$1;shift;exec '+shlex.quote(docker)+' "$verb" --ulimit core=0 --label '+shlex.quote(label)+' "$@";; esac\nexec '+shlex.quote(docker)+' "$@"\n');wrapper.chmod(0o700)
            oldpath=os.environ['PATH'];os.environ['PATH']=str(wrappers)+':'+oldpath
            def sweep():
                os.environ['PATH']=oldpath
                ids=run(docker,'ps','-aq','--filter','label='+label).decode().split()
                if ids:run(docker,'rm','-fv',*ids)
            stack.callback(sweep)
            if not (sdk/'emulator/emulator').is_file():raise RuntimeError('Android emulator SDK unavailable')
            check=run(sdk/'emulator/emulator','-accel-check').decode()
            if 'KVM' not in check or 'usable' not in check:raise RuntimeError('KVM not usable')
            raw['milestone']='official Wholphin release verification'
            apk=release(work);summary['apkSha256']=DIGEST
            summary['emulatorVersion']=(sdk/'emulator/source.properties').read_text().split('Pkg.Revision=')[-1].splitlines()[0].strip()
            def host_audio():
                return (run('pactl','get-default-sink').decode().strip(),sorted(line.split('\t')[1] for line in run('pactl','list','short','sinks').decode().splitlines()))
            before=host_audio()
            raw['milestone']='private emulator and audio boot'
            tv=TV(work,sdk,stack,opts.gpu,opts.ram_mib,opts.llvmpipe_threads)
            summary.update(declaredGpu=opts.gpu,llvmpipeThreadsRequested=opts.llvmpipe_threads,guestRamMiB=opts.ram_mib);tv.host_history=history
            tv.boot();summary['graphicsAdapter']=tv.graphicsAdapter
            print('Emulator graphics:',tv.graphicsAdapter or opts.gpu+' requested; renderer not reported',file=sys.stderr,flush=True)
            tv.adb('install','--no-streaming',apk,timeout=75)
            summary['androidFingerprint']=tv.adb('shell','getprop','ro.build.fingerprint').decode().strip();summary['audioInputFormat']=tv.call('pactl','list','short','sink-inputs').decode().strip().split('\t')[-1]
            mem=re.search(rb'^MemTotal:\s+(\d+) kB',tv.adb('shell','cat','/proc/meminfo'),re.M)
            if mem:summary['guestKernelMemTotalKiB']=int(mem[1])
            raw['milestone']='disposable Jellyfin build and setup'
            lab=setup_lab(work,tv,stack);summary['pluginBuild']=lab.plugin_build;summary['fixtureSha256']=hashlib.sha256(lab.original).hexdigest()
            row=lab.stub_register()['rows'][0];row['controls']=[];row['status']='experimental'
            raw['milestone']='Wholphin login, enrollment and MPV settings'
            ui=Screen(tv);ui.setup(lab);raw['verifiedSettings']=ui.verified;raw['sessionIdentity']=lab.tv_identity
            print('Disposable TV enrollment:',json.dumps(lab.tv_identity),file=sys.stderr,flush=True)
            raw['milestone']='separate stock transport test input install'
            install_input(tv)
            summary['splitCapture']=opts.split_capture
            measure(tv,lab,ui,raw,opts.split_capture)
    except (Exception,KeyboardInterrupt) as exc:
        # Never serialize child command lines, queries, login codes or stderr.
        if isinstance(exc,sp.TimeoutExpired):error=timeout_label(raw.get('milestone','setup'),list(history))
        else:error=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__
    finally:
        signal.alarm(0);done.set();watcher.join(timeout=3);peak=max(peak,allocated(work));summary['scratchPeakBytesObserved']=peak
        summary['hostWorstObservedIo']=worst_io;summary['hostWorstScopedFullPsi']=worst_scoped;summary['hostAfterRun']=host_health()
        if 'before' in locals():
            after=host_audio();summary['defaultSinkUnchanged']=before[0]==after[0];summary['hostSinkListUnchanged']=before[1]==after[1]
        if 'tv' in locals():summary['privateNamespaceExited']=tv.ns.poll() is not None
        if 'tv' in locals() and hasattr(tv,'emu'):summary['emulatorExited']=tv.emu.poll() is not None
        if 'label' in locals():summary['ownedContainersRemaining']=len(run(docker,'ps','-aq','--filter','label='+label).split())
        if opts.keep:summary['privateScratch']=str(work)
        else:shutil.rmtree(work)
        summary['scratchRemoved']=not work.exists()
    summary.update(elapsedSeconds=round(time.monotonic()-start,1),blocker=error);claims=verdicts(raw,error)
    summary['claims']=[status+' '+name+' — '+limit for name,(status,limit) in claims.items()]
    # A speed-altered picture observation is not evidence about the 1x tuple.
    # Keep the complete mixed-speed observations in the summary only.
    if opts.split_capture or 'verifiedSlowSettings' in raw or not has_routes(raw.get('recordings',{}).get('filtered',{})):row=None
    output.mkdir(parents=True,exist_ok=False);summary['evidenceDirectory']=str(output.relative_to(HERE.parents[1]));summary['candidateWritten']=row is not None
    if row is not None:
        row.update(date=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8],evidenceRef=summary['evidenceDirectory']+'/summary.json',observation='Synthetic emulator only. See control calibration, route receipts and limits. Never grants production eligibility.')
        if claims['filtered handoff'][0]=='PASS': row['controls'].append('straight-play')
        if claims['seek forward/back'][0]=='PASS':row['controls']+=['seek-forward','seek-back']
        if claims['persisted resume'][0]=='PASS':row['controls'].append('resume')
        if 'verifiedSettings' not in raw:row['observation']='Attempted emulator tuple only: neither the backend nor its options were verified; no playback claim or eligibility.'
        for key,name in [('markedPicture','marked picture'),('markedSound','marked sound'),('keptPictureAtJoin','kept picture at join'),('keptSoundAtJoin','kept PCM at join')]:
            status,limit=claims[name];row['results'][key]={'outcome':status.lower(),'limit':limit}
        (output/'candidate.json').write_text(json.dumps(row,indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if opts.json:print(json.dumps(summary))
    else:
        if 'verifiedSettings' not in raw:print('Effective backend and mpv options: not verified; no playback evidence')
        print('\n'.join(summary['claims']));print('Run',summary['evidenceDirectory'],f"({summary['elapsedSeconds']} s, {peak} scratch bytes observed)")
        if 'before' in locals():print('Cleanup:',{k:summary.get(k) for k in ('emulatorExited','ownedContainersRemaining','privateNamespaceExited','hostSinkListUnchanged','defaultSinkUnchanged','scratchRemoved')})
        if error:print('Stopped:',error)
        if opts.keep:print('Private retained data:',work)
    return 0 if all(v[0]=='PASS' for v in claims.values()) and not error else 1

if __name__=='__main__':sys.exit(main())



