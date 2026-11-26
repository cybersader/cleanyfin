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


def release(work, abi='x86_64', offline=False):
    from tv_device import ASSETS
    name, digest, size, lib = ASSETS[abi]
    folder = work/('release-'+abi)
    folder.mkdir()
    file = folder/name
    if offline:
        staged=Path(os.environ.get('CLEANYFIN_APK_FEED') or Path.home()/'.cache/cleanyfin-scratch/apks')/name
        if not staged.is_file() or staged.stat().st_size!=size:raise RuntimeError(f'Stage the pinned {name} in the offline Wholphin APK feed first')
        shutil.copyfile(staged,file)
    else:
        row, = [a for a in json.loads(run('gh', 'release', 'view', TAG, '-R', REPO,
                                       '--json', 'assets'))['assets'] if a['name'] == name]
        if row['digest'] != 'sha256:'+digest or row['size'] != size:
            raise RuntimeError(f'Official release metadata differs from pinned {abi} build')
        run('gh', 'release', 'download', TAG, '-R', REPO, '-p', name, '-D', folder, timeout=120)
    if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
        raise RuntimeError('APK differs from published SHA-256')
    with zipfile.ZipFile(file) as z:
        z.getinfo(lib)
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
    def __init__(self, work, sdk, stack, gpu='swangle', ram_mib=2048, llvmpipe_threads=None, r24=False):
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
        self.ns = self.spawn(['unshare','-Urn','sh','-ec','ip link set lo up; exec sleep '+('14700' if r24 else '1800')], 'namespace')
        eventually(lambda: os.readlink(f'/proc/{self.ns.pid}/ns/net') != os.readlink('/proc/self/ns/net'),
                   5, 'Private network namespace unavailable')
        self.prefix = ['nsenter','--preserve-credentials','-t',str(self.ns.pid),'-U','-n','--']
        eventually(lambda: b'127.0.0.1' in self.call('ip','-4','address','show','lo'), 5, 'Private loopback unavailable')
        if self.call('ip','route','show','default').strip():
            raise RuntimeError('TV namespace has external network route')

    def spawn(self, cmd, label):
        return self.stack.enter_context(owned(cmd, self.env, self.work/(label+'.log')))

    public_origin_host = 'http://10.0.2.2:18096'
    app_digest = DIGEST
    ui_scale = (1, 1)
    no_test_packages = False
    expected_route = [{'sink':'fixture','mute':False,'corked':False}]

    def alive(self):
        return self.emu.poll() is None

    def clear_log(self):
        self.adb('logcat','-c')  # Only this private guest, before each playback.

    def device_mode(self):
        return f'Google TV API 36 x86_64 emulator 1280x720, {self.ram_mib} MiB, {self.graphicsClass}, software video vo-gpu Android GLES, stereo PCM'

    def audio_route(self):
        return audio_route(self)

    def configure_audio(self):
        return configure_audio(self)

    def begin_capture(self, s, label, limit, info, *, sound, display):
        audio=self.work/(label+'.pcm') if sound else None
        video=None
        if sound:
            mon=s.enter_context(audio.open('wb')); err=s.enter_context((self.work/(label+'-audio.log')).open('wb'))
            proc=sp.Popen(self.prefix+['parec','--device=fixture.monitor','--format=s16le','--rate=48000','--channels=2','--raw','--latency-msec=20'],env=self.env,stdout=mon,stderr=err)
            def stop():
                proc.terminate()
                try: proc.wait(timeout=5)
                except sp.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            s.callback(stop)
            eventually(lambda:audio.stat().st_size>=38400,12,'Private monitor did not yield 200 ms of PCM')
        if display:
            video=capture_display(self,s,label,limit,info)
        return video,audio

    def audio_route_ok(self, info):
        return info.get('activeQemuAudioRoute')==self.expected_route

    def call(self, *args, **kw):
        return run(*self.prefix, *args, env=self.env, **kw)

    def adb_args(self, *args):
        return [*self.prefix, str(self.sdk/'platform-tools/adb'), '-P', '5037', '-s', 'emulator-5580', *map(str,args)]

    def adb(self, *args, **kw):
        if hasattr(self,"transport"):return self.transport.run(*args,**kw)
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
        if hasattr(self,'transport'):self.transport.boot_guest(self)
        else:  # Only verify-adb-label.py's independent, neutral bootstrap-key witness.
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
        if hasattr(self,'transport'):self.transport.attach_guest(self)
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


def setup_lab(work,tv,stack,line=None,layout=None):
    kit=module('check-plugin')
    if layout:
        import tv_r24
        matrix=module('format-matrix')
        lab=tv_r24.make_lab(matrix,work,line,layout,tv,tv.public_origin_host+kit.BASE)
    else:
        lab=kit.PointerLab(work/'server',line or kit.DEFAULT_JELLYFIN); lab.work.mkdir()
    def cleanup():
        if lab.cid:
            run('docker','rm','-f',lab.cid,timeout=30)
    stack.callback(cleanup)
    lab.setup()
    lab.wholphin['tuple'].update(appBuild=tv.app_digest,deviceMode=tv.device_mode())
    lab.wholphin['status']='experimental'
    lab.public_origin=tv.public_origin_host+kit.BASE
    if layout: tv_r24.prepare(lab,matrix,tv,eventually)
    else: lab.setup_pointer()
    cfg=lab.api('/System/Configuration',user=lab.admin)
    resume_settings=dict(MinResumeDurationSeconds=1,MinResumePct=1,MaxResumePct=95)
    cfg.update(resume_settings)
    lab.api('/System/Configuration','POST',cfg,user=lab.admin)
    readback=lab.api('/System/Configuration',user=lab.admin)
    lab.resume_settings={k:readback.get(k) for k in resume_settings}
    if lab.resume_settings!=resume_settings:raise RuntimeError('Disposable Jellyfin resume settings did not persist')
    run('docker','exec','-d',lab.cid,'perl','-e',RELAY)
    sock=lab.work/'config/tv.sock'
    eventually(sock.is_socket,8,'Jellyfin Unix relay did not start')
    lab.wire_log=tv.work/'relay.log'
    prefix=tv.transport.bridge(LAB_PORT,sock) if isinstance(tv,DeviceTV) else tv.prefix
    tv.spawn(prefix+[sys.executable,'-I',HERE/'tv-wire.py',str(sock),lab.item,lab.pointer],'relay')
    eventually(lambda: lab.server_version.encode() in run(*prefix,'curl','--noproxy','*','-fsS','--max-time','2','http://127.0.0.1:18096'+kit.BASE+'/System/Info/Public'),10,'TV lab origin unavailable')
    return lab


def lossless_control(lab):
    """Index a *normal* MP4, apart from the AAC-only plugin projection."""
    from hard_fixture_lab import HZ, write_identifiable_movie
    fixture=lab.work/'fixture'
    raw=write_identifiable_movie(fixture,20*HZ)
    shutil.copyfile(fixture/'identifiable.mp4',lab.work/'other/identifiable.mp4')
    other=lab.folders['Other']
    policy=lab.api('/Users/'+lab.viewer,user=lab.admin)['Policy']
    policy['EnabledFolders']=[*policy['EnabledFolders'],other]
    lab.api('/Users/'+lab.viewer+'/Policy','POST',policy,user=lab.admin)
    # A metadata refresh of the empty Other folder did not discover the newly
    # added file. Run the native library scan on this disposable server only.
    lab.api('/Library/Refresh','POST',user=lab.admin)
    query='/Items?Recursive=true&IncludeItemTypes=Video'
    def indexed():
        rows=lab.api(query+'&Fields=Path',user=lab.admin)['Items']
        item=next((row for row in rows if row.get('Path')=='/other/identifiable.mp4'),None)
        if item and any(row['Id']==item['Id'] for row in lab.api(query,user=lab.one)['Items']):return item
    item=eventually(indexed,30,'Identifiable FLAC MP4 was not visible to the viewer after the disposable library scan')
    return item['Id'].replace('-',''),raw,hashlib.sha256((fixture/'identifiable.mp4').read_bytes()).hexdigest()


APP='com.github.damontecres.wholphin'
INPUT='org.cleanyfin.fixture'

def install_input(tv):
    if tv.no_test_packages:
        raise RuntimeError('Wholphin did not expose Save for the verified mpv.conf text; device mode installs no helper package')
    root=tv.work/'input'; root.mkdir(exist_ok=True)
    apk=root/'input.apk'; android=tv.sdk/'platforms/android-36/android.jar'; tools=tv.sdk/'build-tools/36.0.0'
    if not apk.exists():
        manifest=root/'AndroidManifest.xml'
        sources=[HERE/'TvInput.java']
        if getattr(tv,'projection_control',False):
            shutil.copyfile(HERE/'ProjectionManifest.xml',manifest)
            sources.extend([HERE/'ProjectionConsent.java',HERE/'ProjectionRecorder.java'])
        else:
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.cleanyfin.fixture"><uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35"/><application android:label="Disposable TV fixture input"/><instrumentation android:name=".TvInput" android:targetPackage="org.cleanyfin.fixture"/></manifest>')
        run('java','-m','jdk.compiler/com.sun.tools.javac.Main','-source','11','-target','11','-classpath',android,'-d',root,*sources)
        run(tools/'d8','--lib',android,'--min-api','34' if getattr(tv,'projection_control',False) else '26','--output',root,*sorted((root/'org/cleanyfin/fixture').glob('*.class')))
        plain=root/'plain.apk'; run(tools/'aapt2','link','--manifest',manifest,'-I',android,'-o',plain)
        with zipfile.ZipFile(plain,'a') as z:z.write(root/'classes.dex','classes.dex')
        key=root/'test.jks'; run('keytool','-genkeypair','-keystore',key,'-alias','fixture','-keyalg','RSA','-keysize','2048','-validity','2','-storepass','disposable','-keypass','disposable','-dname','CN=Disposable Fixture')
        run(tools/'apksigner','sign','--ks',key,'--ks-pass','pass:disposable','--out',apk,plain)
    if hasattr(tv,'transport') and tv.transport.guest:tv.transport.local_inputs.add(apk)
    tv.adb('install','-r','--no-streaming',apk,timeout=60)

class Screen:
    def __init__(self,tv):
        self.tv=tv
        self.sx,self.sy=tv.ui_scale

    def tree(self):
        import xml.etree.ElementTree as ET
        # A new remote filename for each snapshot; do not overwrite another session's UI
        # dump or leave profile/settings text behind when a parse fails. Never log the XML.
        path=self.tv.transport.ui_path() if hasattr(self.tv,'transport') else '/data/local/tmp/cleanyfin-ui-'+os.urandom(12).hex()+'.xml'
        try:
            self.tv.adb('shell','uiautomator','dump',path,timeout=12)
            nodes=list(ET.fromstring(self.tv.adb('exec-out','cat',path)).iter('node'))
        finally:self.tv.adb('shell','rm','-f',path,timeout=4)
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

    def focus_backend(self,cap=42):
        # Compose does not materialize off-screen rows. Walk the menu without
        # selecting anything, and read the exact value in the focused subtree.
        for i in range(cap+1):
            nodes=self.tree();focus=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            values=({c.get(k) for c in focus.iter() for k in ('text','content-desc')} & {'ExoPlayer','MPV'}) if focus is not None else set()
            if len(values)==1:return values.pop()
            if values or i==cap:raise RuntimeError('focused playback backend value is ambiguous or unavailable')
            self.key('DPAD_DOWN')

    def home_drawer(self,cap=4):
        for i in range(cap+1):
            if self.get('Home') is not None:return True
            if i<cap:self.key('DPAD_LEFT')  # Focus only; never activate a profile or media card.
        return False

    def wait_label(self,label):
        eventually(lambda:self.get(label) is not None,18,'Wholphin did not display '+label)

    def key(self,key): self.tv.adb('shell','input','keyevent',key)
    def text(self,text):
        if not re.fullmatch(r'[a-zA-Z0-9:/._=-]+',text): raise ValueError('Unexpected lab text')
        self.tv.adb('shell','input','text',text)

    def connect_lab(self,lab):
        if not getattr(self.tv,'family',None):self.tv.adb('shell','am','start','-n',APP+'/.MainActivity')
        self.activate('Add Server');self.activate('Enter server address')
        eventually(lambda:self.get(edit=0) is not None,20,'Server URL field unavailable')
        self.text(lab.public_origin);self.key('ENTER');self.activate('Add User')
        def code():
            return next((n.get('text') for n in self.tree() if n.get('package')==APP and re.fullmatch(r'\d{6}',n.get('text',''))),None)
        qc=eventually(code,30,'No Wholphin Quick Connect code')
        lab.api('/QuickConnect/Authorize?code='+qc,'POST',user=lab.one)
        def session():
            return next((s for s in lab.api('/Sessions',user=lab.admin) if s.get('UserId','').replace('-','')==lab.viewer and s['Client']==lab.client and s['ApplicationVersion']==lab.version and s['DeviceId']!=lab.one[0] and s['DeviceId']!='unenrolled'),None)
        return eventually(session,30,'No signed-in Wholphin session of the required version')

    def setup(self,lab):
        actual=self.connect_lab(lab)
        lab.tv_device=actual['DeviceId']; lab.policy(lab.viewer,lab.tv_device,['violence','gore']); lab.api('/Cleanyfin/lab/reconcile','POST',user=lab.admin)
        lab.tv_identity={'client':actual['Client'],'version':actual['ApplicationVersion'],
                         'deviceIdSha256':hashlib.sha256(lab.tv_device.encode()).hexdigest(),
                         'exactDeviceEnrollment':(lab.data/'profiles'/lab.viewer/'device.txt').read_text()==lab.tv_device,
                         'labTuple':lab.stub_register()['rows'][0]['tuple']}
        self.enter_settings()
        if not hasattr(lab,'r24_layout'):
            try:self.seek_settings={'verifiedSkipForwardMs':self.skip_forward_five_seconds()}
            except RuntimeError as exc:self.seek_settings={'seekLimit':str(exc)}
        self.dpad('Advanced Settings');self.wait_label('Show Clock')
        if self.focus_backend()=='ExoPlayer':
            self.key('DPAD_CENTER');self.wait_label('Prefer MPV');self.dpad('MPV',cap=5)
        self.wait_label('Edit mpv.conf');self.key('BACK');self.dpad('Advanced Settings')
        options=MPV_OPTIONS + ("\nad-lavc-o=cons_noisegen=1" if hasattr(lab, "r24_layout") else "")
        if getattr(self.tv,'internal',False):
            cap=self.tv.capture;cap.choose_pcm_directory(self.tv)
            for path in (cap.paths.wav,cap.paths.video):cap.verify_missing(self.tv,path)
            self.tv.internal_state['pathsVerifiedAbsentBeforeOptions']=True
            options+='\n'+cap.paths.options
        self.save_mpv(options)

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
        def bounds(n):return tuple(map(int,re.findall(r'\d+',n.get('bounds',''))))
        def read_focus():
            label=self.get('Skip forward')
            focus=next((n for n in self.nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if label is None or focus is None:return None
            l,t,r,b=bounds(label); x,y,w,h=bounds(focus)
            if not (r>l and x>=l-20 and w>r and b<=y<h<=b+70):return None
            for n in self.nodes:
                if n.get('package')!=APP or not re.fullmatch(r'\d+ seconds?',n.get('text','')):continue
                a,u,c,v=bounds(n)
                if a>=w and u<h and v>y:return int(n.get('text').split()[0])
            return None
        value=None
        for _ in range(17):
            value=read_focus()
            if value is not None:break
            self.key('DPAD_DOWN')
        if value not in (5,30):raise RuntimeError('Native Skip forward slider focus/value unavailable')
        for step in range(value-5,4,-5):
            self.key('DPAD_LEFT')
            eventually(lambda:read_focus()==step,6,'Native Skip forward slider did not verify the new value')
        print('Verified native Skip forward: 5 seconds',file=sys.stderr,flush=True)
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
                if 0<=l<160*self.sx and 520*self.sy<t<b<=720*self.sy and 0<r-l<180*self.sx and not (node.get('text') or node.get('content-desc')):
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
            if l<=fx<=r and t<=fy<=b and w-x<180*self.sx:
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
        print('Verified MPV: saved options read back',file=sys.stderr)


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


def capture_display(tv,stack,label,limit,info,*,device_path=None,android_only=False):
    # Android's virtual display records the presented composition, not decoded
    # frames. MP4 preserves the observed per-sample timestamps. The legacy host
    # VP9 tap remains diagnostic fallback only when Android recording won't start.
    device_path=device_path or f'/data/local/tmp/cleanyfin-{label}.mp4'
    video=tv.work/(label+'.mp4');video.unlink(missing_ok=True)
    log=tv.work/(label+'-display.log')
    args=['screenrecord','--verbose','--size',DISPLAY_CAPTURE_SIZE,'--bit-rate','4000000',
          '--time-limit',str(math.ceil(limit+8)),device_path]
    port=getattr(tv,'transport',None)
    if port and not android_only and port.guest:port.capture.add(device_path)
    shell='printf "%s\\n" "$$"; exec '+shlex.join(args)
    def console(*args):
        result=tv.adb('emu','screenrecord',*args,timeout=5)
        return b'OK' in result and b'KO:' not in result
    with ExitStack() as trial:
        argv=port.recorder(device_path,math.ceil(limit+8)) if port else tv.adb_args('shell','sh','-c',shlex.quote(shell))
        proc=trial.enter_context(owned(argv,tv.env,log))
        def identify():
            lines=log.read_bytes().splitlines()
            return lines[0].decode() if lines and lines[0].isdigit() else None
        def finish():
            pid=identify()
            if pid and proc.poll() is None:
                try:
                    if port:port.stop_recorder(device_path,pid)
                    else:tv.adb('shell','kill','-INT',pid,timeout=3)
                except (RuntimeError,sp.CalledProcessError,sp.TimeoutExpired):pass
            try:proc.wait(timeout=6)
            except sp.TimeoutExpired:info['displayStop']='Android recorder did not acknowledge SIGINT'
            try:tv.adb('pull',device_path,video,timeout=12)
            except (sp.CalledProcessError,sp.TimeoutExpired):pass
            finally:
                if android_only:
                    tv.capture.remove(tv,device_path);info['videoRemoved']=True
                else:
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
    if android_only:
        info['displayTap']='SurfaceFlinger timing only; Android screenrecord unavailable, no frame identities'
        return None
    if port:raise RuntimeError('Android screenrecord failed on the qualified playback transport; no host substitute')
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
    a=re.findall(r'\bcplayer,\s+AO:\s+\[([a-z0-9_-]+)\]\s+([^\r\n]+)',logs)
    v=re.findall(r'\bcplayer,\s+VO:\s+\[(gpu)\]\s+(\d+x\d+ [\w-]+)',logs)
    if a:
        driver,fmt=a[-1]
        info['observedAudioDriver']=driver
        expected=info.get('expectedAudioDriver','audiotrack')
        if driver==expected and re.match(r'48000Hz stereo 2ch s16(?:le)?\b' if expected=='pcm' else r'\d+Hz stereo 2ch [\w-]+',fmt):
            info['libmpvAO']=driver+' '+fmt.strip()
        else:info.pop('libmpvAO',None)
    if v:info['libmpvVO']=' '.join(v[-1])
    decoders=re.findall(r'\bad,\s+(?:Selected codec:|Opening decoder)\s+([a-z0-9_-]+)',logs,re.I)
    tracks=re.findall(r'\bcplayer,\s+(?:\(\+\)|●) Audio[^\r\n]*?\((aac|flac|ac3|eac3)\b',logs,re.I)
    if decoders or tracks: info['libmpvAudioCodecs']=sorted(set(decoders+tracks))
    info['audioUnderruns']=logs.count('cplayer, Audio device underrun detected')
    info['libmpvEof']=bool(re.search(r'\bcplayer,\s+EOF code:\s*1\b|\bMPV_EVENT_END_FILE:\s+0\s+0\b',logs))


def player_layers(tv):
    layers=tv.adb('shell','dumpsys','SurfaceFlinger','--list',timeout=2).decode(errors='replace')
    return [line.strip() for line in layers.splitlines() if APP in line and 'SurfaceView' in line]


def player_surface(tv):
    return bool(player_layers(tv))


def observe_presentations(tv, stop, receipt):
    """Independent Android layer timing. Fences identify presents, not barcodes."""
    from tv_score import parse_latency
    layers={}
    failed=0
    while not stop.is_set():
        try:
            for name in player_layers(tv):
                payload=tv.adb('shell','dumpsys','SurfaceFlinger','--latency',shlex.quote(name),timeout=2)
                period,rows=parse_latency(payload)
                entry=layers.setdefault(name, {'periodNs':period,'timestamps':set(),'polls':0,'possibleLostRing':False})
                seen=entry['timestamps']
                if seen and len(rows)>=128 and not seen.intersection(rows):entry['possibleLostRing']=True
                seen.update(rows);entry['polls']+=1
        except (ValueError,sp.CalledProcessError,sp.TimeoutExpired):failed+=1
        stop.wait(1.2)
    receipt.update(probe='SurfaceFlinger --latency; actual-present fences, no picture identities',failedPolls=failed,layers=[])
    for n, e in layers.items():
        rows=sorted(e.pop('timestamps'))
        intervals=[(b-a,a,b) for a,b in zip(rows,rows[1:])]
        receipt['layers'].append(dict(e,layer=n,presentCount=len(rows),
            largestGapsNs=sorted(intervals,reverse=True)[:5],actualPresentNs=rows))


def record(tv,lab,item,label,speed,info,begin=None,*,display=True,sound=True):
    duration=getattr(lab,'r24_duration',14.96) if item==lab.pointer else 20
    limit=duration/speed+MEDIA_MARGIN_SECONDS
    info.update(speed=speed,fixtureDurationS=duration,wallCeilingS=limit,endSignal='fixture duration plus startup margin (no end signal)')
    with ExitStack() as s:
        video,audio=tv.begin_capture(s,label,limit,info,sound=sound,display=display)
        if display:
            # An opened MP4 only certifies that the recorder created its file.
            # Allow composition/encoding before playing; trim by barcode later.
            time.sleep(DISPLAY_PREROLL_SECONDS)
            info['displayPrerollS']=DISPLAY_PREROLL_SECONDS
            from threading import Event,Thread
            stop_event=Event();timeline=info['presentationTiming']={}
            observer=Thread(target=observe_presentations,args=(tv,stop_event,timeline),daemon=True)
            observer.start()
            def end_observation():
                stop_event.set()
                observer.join(timeout=9)
                if observer.is_alive():timeline['incomplete']='SurfaceFlinger timing read did not finish'
            s.callback(end_observation)
        if begin is None:
            tv.clear_log()
        launch=time.monotonic(); pid=''; saw_session=False; saw_surface=False
        with take_deadline(limit) as expired:
            try:
                if begin is None:play(tv,lab,item)
                else:begin()  # Player already paused at the first witnessed frame.
                while True:
                    if not tv.alive():raise RuntimeError('TV device left during capture')
                    try:
                        if not pid:
                            pid=tv.adb('shell','pidof',APP,timeout=2).decode().strip()
                        if pid.isdecimal():
                            logs=tv.adb('logcat','-d','--pid='+pid,'-v','tag',timeout=2).decode(errors='replace')
                            app_runtime(logs,info)
                        if getattr(tv,'projection_control',False) and pid.isdecimal() and not info.get('activeTrack'):
                            from tv_projection import active_track
                            info['activeTrack']=active_track(tv,info['captureUid'],int(pid))
                        if info.get('libmpvEof'):
                            info['endSignal']='libmpv EOF';break
                        sess=now(lab,item)
                        if sess:
                            saw_session=True
                            method=sess.get('PlayState',{}).get('PlayMethod')
                            if method:info['playMethod']=method
                            if not info.get('activeQemuAudioRoute'):
                                info['activeQemuAudioRoute']=tv.audio_route()
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
                if not getattr(tv,'family',None):
                    (tv.work/(label+'-logcat.log')).write_bytes(logs)
                app_runtime(logs.decode(errors='replace'),info)
            except (sp.CalledProcessError,sp.TimeoutExpired):pass
    if video is not None:info['videoBytes']=video.stat().st_size if video.is_file() else 0
    if audio is not None:
        info['audioBytes']=audio.stat().st_size if audio.is_file() else 0
        info['audioDurationS']=round(info['audioBytes']/192000,3)
    info['audioRouteVerified']=bool(sound and tv.audio_route_ok(info))
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


def prepare_at_zero(tv,lab,ui,item,scorer,info,speed):
    tv.clear_log()
    play(tv,lab,item)
    eventually(lambda:now(lab,item),15,'Paused start: no native playback session')
    def command(op):
        if not ui.action(op):raise RuntimeError('Paused start: stock Android MediaSession '+op+' unavailable')
    command('pause')
    eventually(ui.paused,8,'Paused start: stock pause not acknowledged')
    if speed!=1:info['speedSetting']=ui.slow_playback()
    if not ui.paused():raise RuntimeError('Paused start: player left paused state')
    # The stock MPV player does not advertise MediaSession seeking. With
    # overlays dismissed, Wholphin's left key takes its own rewind path.
    if shown_frame(tv,scorer)!=0:
        ui.key('DPAD_LEFT')
        ui.key('DPAD_LEFT')  # Two default 10 s steps cover the whole fixture.
    eventually(lambda:shown_frame(tv,scorer)==0,10,'Paused start: rewind did not present frame zero')
    info['pausedFrameZeroVerified']=True
    return lambda:command('play')


def measure(tv,lab,ui,out,split):
    from tv_score import picture,audio
    from tv_pcm import measure as pcm_measure
    from exact_audio import plan_frames
    from hard_fixture_lab import IDENTIFIABLE_CUT, HZ, FPS
    scorer=module('analyze-capture'); takes=out['recordings']={}
    out['audioLevels']=tv.configure_audio()
    print('Private audio setup:',json.dumps(out['audioLevels']),file=sys.stderr,flush=True)
    def score(key,scorer_fn,*args):
        try:out[key]=scorer_fn(*args)
        except (ValueError,OSError,sp.CalledProcessError,sp.TimeoutExpired) as exc:
            msg=timeout_label(key,tv.host_history) if isinstance(exc,sp.TimeoutExpired) else str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            out.setdefault('scoreIssues',{})[key]=msg
    def take(label,item,speed,*,sound=False,display=True,filtered=False,paused_start=False):
        out['milestone']=label
        before=handoff(lab) if filtered else None
        offset=lab.wire_log.stat().st_size
        info=takes[label]={'speed':speed}
        try:
            # Keep Activity cold-start work out of the first barcode frames.
            tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity')
            begin=prepare_at_zero(tv,lab,ui,item,scorer,info,speed) if paused_start or speed!=1 else None
        except RuntimeError as exc:
            if not str(exc).startswith(('Paused start:','Playback controls did not expose','Wholphin did not display','No focused')):raise
            info['setupLimit']=str(exc)
            tv.adb('shell','am','force-stop',APP)
            print(f'{label}: {exc}',file=sys.stderr,flush=True)
            return None
        v,a=record(tv,lab,item,label,speed,info,begin,display=display,sound=sound)
        info['observedPlaybackInfo']=[r for r in wire_rows(lab,offset) if r['route']!='presentation-event']
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
        if display:
            score(label+'Picture',picture,v,scorer)
            pic=out.get(label+'Picture',{}); gaps=pic.get('largestCaptureGaps',[])
            sf=info.get('presentationTiming',{}).get('layers',[])
            if gaps:
                g=gaps[0]
                print(f"{label} picture: {pic['distinct']} distinct; largest recorder gap {g['ms']} ms "
                      f"(frame {g['before']}→{g['after']}); join window max {pic.get('joinMaxCaptureGapMs')} ms; "
                      f"SurfaceFlinger present fences {[r['presentCount'] for r in sf]}",file=sys.stderr,flush=True)
        if sound and not label.startswith('lossless'):score(label+'Sound',audio,a,scorer)
        if 'captureDeadline' in info:
            if info['captureDeadline'].startswith('Host too busy'):raise RuntimeError(info['captureDeadline'])
            print(info['captureDeadline'],file=sys.stderr,flush=True)
        return a
    ca=take('control',lab.item,1,sound=True,display=not split)
    cb=take('repeatControl',lab.item,1,sound=True,display=not split)
    out['milestone']='AAC PCM reference origin and repeatability'
    refs=None
    def lr(vals): return '/'.join(f'{v:.4g}' for v in vals)
    def print_pcm(codec,result):
        for label,r in result.get('renders',{}).items():
            clock=r['probes']; offsets={k:p.get('observedOffsetSamples') for k,p in clock.items()}
            print(f"{codec} {label}: origin {r['frozenOriginSample']} samples into capture; span at 512 LSB "
                  f"{r['signalSpanAt512LSBFrames']}/{r['expectedFrames']}; clock probe offsets {offsets}; "
                  f"early gain L/R {lr(clock.get('early',{}).get('gainLR',[]))}",file=sys.stderr,flush=True)
        floor=result.get('repeatability',{})
        if floor:
            def detail(k):
                f=floor[k];return f"{f['differentSampleFrames']}/{f['comparedSampleFrames']} frames, {lr(f['errorRmsLSB'])} L/R LSB RMS"
            print(f"{codec} uncut repeatability: 20 ms before: {detail('joinBefore20ms')}; "
                  f"20 ms after: {detail('joinAfter20ms')}",file=sys.stderr,flush=True)
    cut_samples=plan_frames(json.loads((lab.work/'fixture/plan.json').read_text()))
    if all(live_pcm(takes[label]) for label in ('control','repeatControl')):
        try:
            refs=scorer.media(lab.work/'fixture/source.mp4','ffmpeg','-i','/lab','-map','0:a:0','-f','s16le','-ac','2','-ar','48000','pipe:1',readonly=True)
        except (sp.CalledProcessError,sp.TimeoutExpired):
            out.setdefault('scoreIssues',{})['pcmReference']='Could not decode source alignment witness'
        if refs is not None:
            score('waveform',pcm_measure,refs,{'control':ca,'repeatControl':cb},*cut_samples)
            print_pcm('AAC',out.get('waveform',{}))
    fa=take('filtered',lab.pointer,1,sound=True,display=not split,filtered=True)
    out['milestone']='AAC filtered PCM comparison'
    if refs is not None and live_pcm(takes['filtered']) and has_routes(takes['filtered']):
        score('waveform',pcm_measure,refs,{'control':ca,'repeatControl':cb,'filtered':fa},*cut_samples)
        print_pcm('AAC',{'renders':{k:r for k,r in out.get('waveform',{}).get('renders',{}).items() if k=='filtered'}})
    out['milestone']='Lossless render calibration'
    try:
        item,source,digest=lossless_control(lab)
        lossless={'fixtureSha256':digest,'scope':'ordinary Jellyfin source only; filtered plugin admission requires AAC'}
        out['lossless']=lossless
        x=take('losslessControl',item,1,sound=True,display=False)
        y=take('losslessRepeat',item,1,sound=True,display=False)
        if all(live_pcm(takes[label]) and 'flac' in takes[label].get('libmpvAudioCodecs',[]) for label in ('losslessControl','losslessRepeat')):
            start,end=(i*HZ//FPS for i in IDENTIFIABLE_CUT)
            score('losslessWaveform',pcm_measure,source,{'control':x,'repeatControl':y},start,end,20*HZ)
            print_pcm('FLAC',out.get('losslessWaveform',{}))
        else:lossless['limit']='Two verified direct FLAC renders were not observed'
    except RuntimeError as exc:
        if str(exc).startswith('Host too busy'):raise
        out.setdefault('lossless',{})['limit']=str(exc)
    except (sp.CalledProcessError,sp.TimeoutExpired) as exc:
        out.setdefault('lossless',{})['limit']=timeout_label('lossless calibration',tv.host_history) if isinstance(exc,sp.TimeoutExpired) else 'Lossless calibration setup command failed'
    control_names=('control','repeatControl')
    picture_cut='filtered'
    if split:
        control_names=('controlDisplay',)
        picture_cut='filteredDisplay'
        take('controlDisplay',lab.item,1)
        take(picture_cut,lab.pointer,1,filtered=True)
    real=any(picture_calibrated(out.get(label+'Picture',{}),takes[label]) for label in control_names)
    cut=out.get(picture_cut+'Picture',{})
    if not real or not cut.get('filteredComplete'):
        take('warmControl',lab.item,1,paused_start=True)
        if picture_calibrated(out.get('warmControlPicture',{}),takes['warmControl']):
            take('warmFiltered',lab.pointer,1,filtered=True,paused_start=True)
            warm=out.get('warmFilteredPicture',{})
            if takes['warmFiltered'].get('runtimeVerified') and has_routes(takes['warmFiltered']) and warm.get('filteredComplete'):
                picture_cut='warmFiltered';cut=warm;real=True
    # Do not spend the remaining run window on a slower-clock diagnostic
    # before attempting real-speed transport and bookmark evidence.
    if has_routes(takes['filtered']):
        out['milestone']='seeks and persisted resume'
        controls(tv,lab,ui,out)
    need_slow_cut=(has_routes(takes[picture_cut]) and not cut.get('marked') and not picture_resolved(cut,1))
    if not real or need_slow_cut:
        take('slowControl',lab.item,PICTURE_SPEED)
        if takes['slowControl'].get('speedSetting'):
            out['verifiedSlowSettings']=takes['slowControl']['speedSetting']
        if has_routes(takes['filtered']) and picture_calibrated(out.get('slowControlPicture',{}),takes['slowControl']):
            take('slowFiltered',lab.pointer,PICTURE_SPEED,filtered=True)


def wire_rows(lab,offset):
    with lab.wire_log.open() as log:
        log.seek(offset)
        return [json.loads(line) for line in log if line.startswith('{') and line.endswith('\n')]


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
    receipt['transport']='Stock Wholphin D-pad seeks, Android MediaSession pause and receiver Stop key'
    receipt.update(ui.seek_settings)
    receipt['serverResumeSettings']=lab.resume_settings
    tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity')
    offset=lab.wire_log.stat().st_size
    routes=handoff(lab)[0]
    def ticks():
        return lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
    receipt['bookmarkBeforeTicks']=ticks()
    tv.clear_log()
    play(tv,lab,lab.pointer)
    if not witness(lambda:now(lab,lab.pointer),20,'Control playback did not start'):return
    def shown():return shown_frame(tv,scorer)
    if 'verifiedSkipForwardMs' in receipt:
        receipt['pauseRequest']=ui.input_result('pause')
        receipt['pauseAcknowledged']=bool(receipt['pauseRequest']=='applied' and witness(ui.paused,8,'Stock Android player did not acknowledge pause'))
        def focus_mark():
            focused=next((n for n in ui.tree() if n.get('package')==APP and n.get('focused')=='true'),None)
            return {k:focused.get(k) for k in ('class','bounds','clickable')} if focused is not None else None
        if receipt['pauseAcknowledged']:
            ui.key('DPAD_LEFT');ui.key('DPAD_LEFT')
            receipt['rewindAnchor']=bool(witness(lambda:shown()==0,5,'Stock rewind did not present frame zero'))
        if receipt.get('rewindAnchor'):
            for direction,ms in (('RIGHT',5000),('RIGHT',10000),('LEFT',0))*2:
                target=ms//40+(126 if ms>=5480 else 0)
                focus=focus_mark()
                ui.key('DPAD_'+direction)
                observed=[]
                def landed():
                    n=shown()
                    if n is not None:observed.append(n)
                    return n==target
                exact=bool(witness(landed,5,f'No exact paused seek landing at {ms} ms'))
                receipt.setdefault('pausedSeeks',[]).append(dict(filteredMs=ms,expectedFrame=target,observed=observed,exact=exact,request=direction,focus=focus))
                if not exact:break  # Relative expectations cannot be chained from an unverified position.
        receipt['seekGrantRouteDeltas']=[b-a for a,b in zip(routes,handoff(lab)[0])]
        # Resume the same stock player and stop past the cut, away from EOF.
        if receipt['pauseAcknowledged']:ui.action('play')
    receipt['preStopReadings']=[]
    def beyond_cut():
        n=shown()
        if n is not None:receipt['preStopReadings'].append(n)
        return n if n is not None and 280<=n<=410 else None
    receipt['preStopFrame']=witness(beyond_cut,10,'No kept frame sufficiently far past the cut before Stop')
    session=now(lab,lab.pointer)
    if not session:
        receipt.setdefault('limits',[]).append('Player left before stock Stop could be sent');return
    receipt['preStopSessionTicks']=session.get('PlayState',{}).get('PositionTicks')
    receipt['bookmarkImmediatelyBeforeStopTicks']=ticks()
    ui.key('KEYCODE_MEDIA_STOP')
    receipt['stopAcknowledged']=bool(witness(lambda:not now(lab,lab.pointer),8,'No Jellyfin session clear after Stop'))
    receipt['wireEventsThroughStop']=[r for r in wire_rows(lab,offset) if r.get('item')=='pointer']
    try:
        pid=tv.adb('shell','pidof',APP,timeout=2).decode().strip()
        if pid.isdecimal():
            logs=tv.adb('logcat','-d','--pid='+pid,'-v','tag',timeout=3).decode(errors='replace')
            receipt['reportedStopPositions']=[pos for item,pos in re.findall(r'reportPlaybackStopped for ([a-fA-F0-9-]{32,36}) at ([-0-9. hms]{1,24})(?:\r?\n|$)',logs) if item.replace('-','').lower()==lab.pointer]
    except (sp.CalledProcessError,sp.TimeoutExpired):pass
    def new_ticks():
        value=ticks()
        return value if value>0 and value!=receipt['bookmarkBeforeTicks'] else None
    receipt['bookmarkTicks']=witness(new_ticks,5,'No new persisted pointer bookmark after Stop')
    if not receipt['bookmarkTicks']:
        # Back follows the normal activity release path without first resetting
        # the player. Treat it independently; never turn a Stop failure into a pass.
        offset=lab.wire_log.stat().st_size
        play(tv,lab,lab.pointer)
        if not witness(lambda:now(lab,lab.pointer),15,'Back control could not start a fresh player'):return
        before_back=time.monotonic()
        def halfway():
            s=now(lab,lab.pointer)
            t=s.get('PlayState',{}).get('PositionTicks') if s else None
            return t if type(t) is int and 58_000_000<=t<=110_000_000 else None
        receipt['preBackSessionTicks']=witness(halfway,10,'No mid-playback position for Back control')
        if not receipt['preBackSessionTicks']:return
        receipt['preBackElapsedS']=round(time.monotonic()-before_back,2)
        ui.key('BACK')
        receipt['backAcknowledged']=bool(witness(lambda:not now(lab,lab.pointer),8,'Player did not close after Back'))
        receipt['bookmarkTicks']=witness(new_ticks,5,'No new persisted pointer bookmark after Back')
        receipt['wireEventsThroughBack']=[r for r in wire_rows(lab,offset) if r.get('item')=='pointer']
        if not receipt['bookmarkTicks']:return
        receipt['resumeVia']='Back (separate from Stop)'
    else:receipt['resumeVia']='Stop'
    tv.adb('shell','am','force-stop',APP)
    tv.adb('shell','am','start','-n',APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.api('/System/Info/Public')['Id'])))
    matches=lambda:[n for n in ui.tree() if n.get('package')==APP and any('resume' in n.get(k,'').lower() for k in ('text','content-desc'))]
    nodes=witness(matches,15,'No actual Resume choice after stopping Wholphin')
    if not nodes:return
    n=nodes[0];label=n.get('content-desc') or n.get('text')
    before=handoff(lab)[0]
    tv.clear_log()
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


# ---------------------------------------------------------------------------
# Device mode: the owner's Google TV box over wireless debugging, its HDMI
# output captured through a USB adapter on this PC. The dry run drives the
# harness's own emulator over adb TCP and feeds recorded files as capture.

STORE_SIZE = '320x180'
STORE_FILTER = f"scale={STORE_SIZE.replace('x',':')}:flags=area,format=gray"
LAB_PORT = 18096


def pcm_arrival(audio, info, fmt, fmt_ok):
    """Verify the format actually arriving, not the one requested."""
    from tv_device import iec61937
    from array import array
    raw=audio.read_bytes() if audio.is_file() else b''
    vals=array('h',raw[:len(raw)//2*2]) if raw else array('h')
    if sys.byteorder!='little':vals.byteswap()
    peak=max(map(abs,vals),default=0)
    bitstream=iec61937(raw)
    info['arrivingAudio']=dict(format=fmt,formatOk=fmt_ok,bytes=len(raw),peakLSB=peak,iec61937Bitstream=bitstream,
                               ok=bool(fmt_ok and raw and not len(raw)%4 and peak>0 and not bitstream))
    if bitstream:print('Captured audio is an undecoded bitstream; the box must output decoded PCM',file=sys.stderr,flush=True)


class AdapterCapture:
    """Video from the adapter's UVC node through the pinned FFmpeg container (only that node
    passed in); audio from the adapter's own ALSA capture device at 48 kHz stereo, opened as
    `hw:` so nothing resamples it. The desktop's default sink and source are never touched."""
    def __init__(self,work,adapter,mode,reason):
        from tv_device import FFMPEG_PIXFMT
        self.work,self.adapter,self.mode,self.reason=work,adapter,mode,reason
        self.pixfmt=FFMPEG_PIXFMT.get(mode['fourcc'],mode['fourcc'])
        self.label=f"{adapter['model']} {mode['fourcc']} 1920x1080@60"

    def begin(self,s,label,limit,info,*,sound,display):
        from hard_fixture_lab import IMAGE
        video=audio=None
        node=self.adapter['video']
        if display:
            video=self.work/(label+'.mkv'); name='cleanyfin-hdmi-'+os.urandom(4).hex()
            cmd=['docker','run','--rm','--name',name,'--network','none','--user',f'{os.getuid()}:{os.getgid()}',
                 '--group-add',str(os.stat(node).st_gid),'--device',f'{node}:{node}','-v',f'{self.work}:/lab:Z',
                 '--entrypoint','/usr/lib/jellyfin-ffmpeg/ffmpeg',IMAGE,'-hide_banner','-loglevel','error','-nostdin',
                 '-f','v4l2','-input_format',self.pixfmt,'-video_size','1920x1080','-framerate','60','-i',node,
                 '-t',str(math.ceil(limit+8)),'-map','0:v:0','-vf',STORE_FILTER,'-fps_mode','passthrough',
                 '-c:v','ffv1','/lab/'+video.name]
            proc=s.enter_context(owned(cmd,os.environ,self.work/(label+'-hdmi-video.log')))
            def stop_video():
                try:run('docker','stop','-t','6',name,timeout=15)
                except (sp.CalledProcessError,sp.TimeoutExpired):pass
            s.callback(stop_video)
            def started():
                if proc.poll() is not None:raise RuntimeError('HDMI video capture exited (adapter busy or mode refused)')
                return video.is_file() and video.stat().st_size>0
            eventually(started,15,'HDMI video capture did not start')
            info['displayTap']=f'HDMI adapter {self.label} via pinned FFmpeg v4l2 -> stored {STORE_SIZE} gray FFV1'
        if sound:
            audio=self.work/(label+'.pcm')
            s.callback(lambda:pcm_arrival(audio,info,'s16le 48000 Hz 2 ch from adapter hw: device (no resampling)',True))
            cmd=['arecord','-q','-D',f"hw:CARD={self.adapter['cardId']},DEV=0",'-f','S16_LE','-r','48000','-c','2','-t','raw',str(audio)]
            proc=s.enter_context(owned(cmd,os.environ,self.work/(label+'-hdmi-audio.log')))
            def started():
                if proc.poll() is not None:raise RuntimeError('Adapter audio capture refused 48 kHz stereo 16-bit or is busy')
                return audio.is_file() and audio.stat().st_size>=38400
            eventually(started,10,'Adapter audio capture did not yield 200 ms')
        return video,audio


class FileCapture:
    """Dry run: recorded video/audio files stand in for the adapter, through the same
    normalisation, arriving-format check and scoring."""
    def __init__(self,work,mapping):
        self.work,self.mapping=work,mapping
        self.label='recorded capture files (dry run)'
        (work/'feed').mkdir(exist_ok=True)

    def begin(self,s,label,limit,info,*,sound,display):
        from tv_device import pick_capture, arriving_audio
        from hard_fixture_lab import media
        src=pick_capture(self.mapping,label)
        if src is None:raise RuntimeError(f'No --capture-file for take {label}')
        feed=self.work/'feed'/(label+src.suffix)
        if not feed.exists():shutil.copyfile(src,feed)
        video=self.work/(label+'.mkv') if display else None
        audio=self.work/(label+'.pcm') if sound else None
        def finish():
            if display:
                media(self.work,'ffmpeg','-i','/lab/feed/'+feed.name,'-map','0:v:0','-vf',STORE_FILTER,'-fps_mode','passthrough','-c:v','ffv1','-y','/lab/'+video.name)
            if sound:
                streams=json.loads(media(self.work,'ffprobe','-select_streams','a:0','-show_entries','stream=codec_name,sample_rate,channels','-of','json','/lab/feed/'+feed.name,readonly=True)).get('streams',[])
                ok,fmt=arriving_audio(streams[0] if streams else {})
                if ok:media(self.work,'ffmpeg','-i','/lab/feed/'+feed.name,'-map','0:a:0','-c:a','copy','-f','s16le','-y','/lab/'+audio.name)
                else:audio.write_bytes(b'')
                pcm_arrival(audio,info,fmt,ok)
        s.callback(finish)
        if display:info['displayTap']=f'recorded capture file (dry run) -> stored {STORE_SIZE} gray FFV1'
        info['captureFile']=src.name
        return video,audio


class DeviceTV:
    """The box (or the dry-run emulator) reached only through adb at the given address."""
    public_origin_host=f'http://127.0.0.1:{LAB_PORT}'  # device loopback, carried by adb reverse
    no_test_packages=True
    app_digest=DIGEST

    def __init__(self,work,sdk,stack,*,prefix,env,port,capture,emulator=None):
        self.work,self.sdk,self.stack,self.prefix,self.env,self.port=work,sdk,stack,list(prefix),env,port
        self.capture,self.emulator,self.serial,self.found=capture,emulator,None,{}
        from tv_internal import InternalCapture
        self.internal=isinstance(capture,InternalCapture)
        self.ui_scale=(1,1); self.misses=0

    def spawn(self,cmd,label):
        return self.stack.enter_context(owned(cmd,self.env,self.work/(label+'.log')))

    def call(self,*args,**kw):
        return run(*self.prefix,*args,env=self.env,**kw)

    def adb(self,*args,**kw):
        return self.transport.run(*args,**kw)

    def initial_read(self,*command):
        # sys.boot_completed can precede the package/window services on a fresh clone.
        # Only retry these four read-only startup queries, never install or app input.
        if tuple(command) not in {('wm','size'),('getprop',),('pm','list','features'),('pm','list','packages')}:
            raise ValueError('not a startup query')
        end=time.monotonic()+(15 if self.emulator is not None else 0)
        while True:
            try:return self.adb('shell',*command,timeout=8)
            except (sp.CalledProcessError,sp.TimeoutExpired):
                if time.monotonic()>=end:raise
                time.sleep(.5)

    def bind(self,transport):
        self.transport=transport
        if self.internal:transport.bind_capture(self.capture.paths)
        size=re.findall(rb'(\d+)x(\d+)',self.initial_read('wm','size'))
        if size:
            w,h=map(int,size[-1]);self.ui_scale=(w/1280,h/720);self.found['uiSize']=f'{w}x{h}'

    def alive(self):
        if self.emulator is not None and self.emulator.emu.poll() is not None:return False
        try:ok=self.adb('get-state',timeout=4).strip()==b'device'
        except (sp.CalledProcessError,sp.TimeoutExpired):ok=False
        self.misses=0 if ok else self.misses+1
        return self.misses<3  # Tolerate a brief Wi-Fi hiccup, not a lost box.

    def clear_log(self):
        pass  # Never clear the family box's logs; each take reads only its fresh Wholphin pid.

    def device_mode(self):
        from tv_device import register_label
        f=self.found
        if self.internal:
            who=('dry-run emulator' if self.emulator is not None else 'physical box')
            # Put the tap first. Do not silently truncate device identity to fit the register.
            fingerprint=hashlib.sha256(f['build'].encode()).hexdigest()
            return register_label(f'{self.capture.label}, {who} {f["model"]}, build sha256 {fingerprint}, vo-gpu GLES')
        if self.emulator is not None:
            return register_label(f"DRY RUN Google TV API 36 x86_64 emulator over adb TCP, capture {self.capture.label}")
        return register_label(f"{f.get('manufacturer')} {f.get('model')}, Android {f.get('android')} build {f.get('build')}, "
                              f"HDMI capture {self.capture.label}, software video vo-gpu Android GLES, decoded stereo PCM")

    def audio_route(self):
        return [{'capture':self.capture.label}]

    def audio_route_ok(self,info):
        return bool(info.get('arrivingAudio',{}).get('ok') and (not self.internal or
                    (info.get('observedAudioDriver')=='pcm' and info.get('pcmRemoved') and info.get('videoRemoved'))))

    def configure_audio(self):
        if self.internal:return {'level':'mpv decoded PCM file; Android volume unchanged, not output gain'}
        from tv_device import parse_volume,MUSIC_STREAM
        cmd=['shell','cmd','media_session','volume','--stream',str(MUSIC_STREAM)]
        before=parse_volume(self.adb(*cmd,'--get').decode(errors='replace'))
        result={'androidMusicVolumeBefore':before and before['value']}
        if before:
            self.adb(*cmd,'--set',str(before['max']))
            after=parse_volume(self.adb(*cmd,'--get').decode(errors='replace'))
            result.update(androidMusicVolumeAfter=after and after['value'],androidMusicVolumeMax=before['max'])
            if not after or after['value']!=before['max']:
                # No volume keys: on a real box they can reach a TV over HDMI-CEC.
                # HDMI output is often fixed-volume; the capture's gain is measured instead.
                result['volumeLimit']='box kept its volume; output gain is measured from the capture, not assumed'
        else:result['androidMusicVolumeReadback']='unavailable; not assuming unity'
        return result

    def begin_capture(self,s,label,limit,info,*,sound,display):
        if self.internal:return self.capture.begin(sys.modules[__name__],self,s,label,limit,info,sound=sound,display=display)
        return self.capture.begin(s,label,limit,info,sound=sound,display=display)


class DeviceScreen(Screen):
    """Pause/play through Android's own media-session command; no helper package on the box."""
    def input_result(self,op,value='0'):
        from tv_device import media_state
        if op in ('pause','play'):
            try:self.tv.adb('shell','cmd','media_session','dispatch',op,timeout=6)
            except (sp.CalledProcessError,sp.TimeoutExpired):return 'unavailable'
            return 'applied'
        if op=='state':
            try:state=media_state(self.tv.adb('shell','dumpsys','media_session',timeout=6).decode(errors='replace'))
            except (sp.CalledProcessError,sp.TimeoutExpired):return 'no-result'
            return f'{state}:' if state is not None else 'no-result'
        return 'unsupported'


def adapter_capture(work,opts,summary):
    import tv_device as dev
    src=run('pactl','get-default-source').decode().strip()
    card=dev.default_source_card(src,run('pactl','-f','json','list','sources').decode())
    found=dev.discover(extra_ids=opts.capture_usb or (),default_source_card=card)
    summary['captureDiscovery']=found
    for r in found['refused']:print('Not using',r['device'],'-',r['reason'],file=sys.stderr,flush=True)
    if len(found['adapters'])!=1:
        raise RuntimeError(f"Need exactly one HDMI capture adapter; found {len(found['adapters'])}")
    adapter=found['adapters'][0]
    modes=list_modes(adapter['video'])
    mode,reason=dev.choose_mode(modes)
    summary['captureMode']=dict(adapter=adapter['device'],chosen=mode,reason=reason,listed=len(modes))
    print('Capture adapter:',adapter['device'],'| video',adapter['video'],'| audio card',adapter['cardId'],'|',reason,file=sys.stderr,flush=True)
    if mode is None:raise RuntimeError('Capture adapter lists no 1920x1080 mode')
    return AdapterCapture(work,adapter,mode,reason)


def list_modes(node):
    """Formats from v4l2-ctl if installed, else from the pinned FFmpeg with only that node."""
    import tv_device as dev
    if shutil.which('v4l2-ctl'):
        return dev.parse_v4l2_ctl(run('v4l2-ctl','-d',node,'--list-formats-ext',timeout=10).decode(errors='replace'))
    from hard_fixture_lab import IMAGE
    p=sp.run(['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}','--group-add',str(os.stat(node).st_gid),
              '--device',f'{node}:{node}','--entrypoint','/usr/lib/jellyfin-ffmpeg/ffmpeg',IMAGE,'-hide_banner',
              '-f','v4l2','-list_formats','all','-i',node],stdout=sp.PIPE,stderr=sp.STDOUT,timeout=30)
    return dev.parse_ffmpeg_formats(p.stdout.decode(errors='replace'))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def stock_labels(screen,apk):
    # Literal release strings on a private clone only. Never serialize unmatched
    # text such as a Quick Connect code, user, server address or access token.
    if screen.tv.emulator is None or not screen.tv.found.get('emulator'):return {}
    pool=run(screen.tv.sdk/'build-tools/36.0.0/aapt2','dump','strings',apk).decode(errors='replace')
    known={v for v in re.findall(r'(?m)^String #\d+ : ([^\n]+)$',pool) if re.fullmatch(r"[A-Za-z][A-Za-z ,!?'’-]{0,58}",v)}
    app=[n for n in screen.tree() if n.get('package')==APP]
    labels={n.get(k) for n in app for k in ('text','content-desc') if n.get(k)}
    return {'appVisible':bool(app),'appNodeCount':len(app),'labelCount':len(labels),
            'hasProgressIndicator':any(n.get('class','').endswith('ProgressBar') for n in app),
            'errorTextPresent':any(re.search(r'error|exception|failed|not found',v,re.I) for v in labels),
            'stockLabels':sorted(labels & known)[:30],
            'visibleCategories':sorted(set(re.findall(r'[a-z]+',' '.join(labels).lower())) &
                set('profile account user select original home favorite favorites loading connect connection login code authorize server setup offline search settings library libraries next continue username password finish retry welcome privacy playback error about version update changelog release crash sign skip button device remember permission one synthetic household'.split()))}


def seed_update_rehearsal(tv,work,stack,receipt):
    # The extra signed-in account exists only in the fresh AVD + a separate network-none
    # Jellyfin. Never install the old version or seed a user on a physical device.
    if tv.emulator is None or not tv.found.get('emulator'):raise RuntimeError('synthetic baseline needs the verified private AVD')
    from tv_update import OLD_SHA
    feed=Path(os.environ.get('CLEANYFIN_APK_FEED') or Path.home()/'.cache/cleanyfin-scratch/apks')
    old=feed/'v1.0.9/Wholphin-x86_64.apk'
    if not old.is_file() or hashlib.sha256(old.read_bytes()).hexdigest()!=OLD_SHA:raise RuntimeError('stage verified official 1.0.9 x86_64 APK before update rehearsal')
    if b'Success' not in tv.adb('install','--no-streaming',old,timeout=120):raise RuntimeError('baseline APK install not verified')
    kit=module('check-plugin');lab=kit.Lab(work/'synthetic-profile','10.11');lab.work.mkdir()
    stack.callback(lambda:run('docker','rm','-f',lab.cid,timeout=30) if lab.cid else None)
    lab.setup();lab.users_library();lab.client='Wholphin';lab.version='1.0.9-0-g2d87d526'
    cfg=lab.api('/System/Configuration',user=lab.admin);cfg['ServerName']='Synthetic Household';lab.api('/System/Configuration','POST',cfg,user=lab.admin)
    port=LAB_PORT+1;lab.public_origin=f'http://127.0.0.1:{port}'+kit.BASE
    run('docker','exec','-d',lab.cid,'perl','-e',RELAY);sock=lab.work/'config/tv.sock';eventually(sock.is_socket,8,'synthetic baseline relay unavailable')
    tv.spawn(tv.transport.bridge(port,sock)+[sys.executable,'-I',HERE/'tv-wire.py',str(sock),lab.item,'unused',str(port)],'baseline-relay')
    tv.adb('reverse','--no-rebind',f'tcp:{port}',f'tcp:{port}',timeout=8)
    stack.callback(lambda:tv.adb('reverse','--remove',f'tcp:{port}',timeout=8))
    ui=DeviceScreen(tv);ui.connect_lab(lab);receipt['authenticatedSyntheticSession']=True
    # Quick Connect can authorize the server session before local user selection.
    drawer_moves=0
    def signed_home():
        nonlocal drawer_moves
        nodes=ui.tree();labels={(n.get(k) or '').casefold() for n in nodes if n.get('package')==APP for k in ('text','content-desc')}
        if 'home' in labels:return True
        if 'select user' in labels and 'one' in labels:ui.activate('one')
        elif drawer_moves<4:ui.key('DPAD_LEFT');drawer_moves+=1
        return False
    try:eventually(signed_home,30,'signed-in 1.0.9 did not reach Home')
    except RuntimeError:
        receipt['screenAfterLogin']=stock_labels(ui,old)
        # The seed is not established until the same local profile survives an
        # app restart. Do not reauthenticate or clear app data to pass this check.
        tv.adb('shell','am','force-stop',APP);tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity',timeout=20)
        drawer_moves=0
        try:eventually(signed_home,30,'signed-in 1.0.9 did not reach Home after restart')
        except RuntimeError:
            receipt['screenAfterRestart']=stock_labels(ui,old);raise
    ui.enter_advanced()
    if ui.focus_backend()=='ExoPlayer':
        ui.key('DPAD_CENTER');ui.wait_label('Prefer MPV');ui.dpad('MPV',cap=5)
    ui.wait_label('Edit mpv.conf')
    ui.save_mpv('hwdec=no')  # Nonempty synthetic baseline must survive upgrade and test.


def device_session(opts,work,sdk,stack,summary,history):
    """Connect, verify identity, record the manifest, register the restore, then make the
    budgeted changes. Returns the DeviceTV ready for setup_lab."""
    import getpass
    import tv_device as dev
    if opts.no_capture:
        from tv_internal import InternalCapture,Paths
        capture=InternalCapture(Paths(os.urandom(12).hex()))
    elif opts.capture_file:capture=FileCapture(work,dev.capture_files(opts.capture_file))
    else:capture=adapter_capture(work,opts,summary)
    from adb_lab import AdbLab
    from adb_lab_playback import Playback
    if opts.dry_run_emulator:
        emu=None
        def observe_guest_exit():
            if emu is not None:
                summary['privateNamespaceExited']=emu.ns.poll() is not None
                if hasattr(emu,'emu'):summary['emulatorExited']=emu.emu.poll() is not None
        stack.callback(observe_guest_exit)
        emu=TV(work,sdk,stack,opts.gpu,opts.ram_mib,opts.llvmpipe_threads,opts.r24);emu.host_history=history
        emu.transport=Playback.guest_session(stack,work,sdk,family=bool(opts.existing_wholphin),receipt=summary)
        emu.boot();summary['graphicsAdapter']=emu.graphicsAdapter
        tv=DeviceTV(work,sdk,stack,prefix=emu.prefix,env=emu.env,port=None,capture=capture,emulator=emu)
        tv.bind(emu.transport)
    else:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            pair,code,address=dev.connection({},input,getpass.getpass)
        client=stack.enter_context(AdbLab());client.pair(pair,code);del code;client.connect(address)
        tv=DeviceTV(work,sdk,stack,prefix=(),env=dev.env_without_secrets(os.environ),port=None,capture=capture)
        tv.bind(Playback(client,work,guest=False,family=bool(opts.existing_wholphin)))
    tv.host_history=history
    text=lambda *a:tv.adb(*a,timeout=30).decode(errors='replace')
    initial=lambda *a:tv.initial_read(*a).decode(errors='replace')
    props=dev.parse_getprop(initial('getprop'))
    ok,found,why=dev.identity(props,initial('pm','list','features'),dev.parse_packages(initial('pm','list','packages')),
                              emulator=opts.dry_run_emulator,expect_model=opts.expect_model)
    tv.found.update(found);summary['device']={k:v for k,v in tv.found.items()}
    print('Connected device:',json.dumps(summary['device']),file=sys.stderr,flush=True)
    if not ok:raise RuntimeError('Refusing device: '+why)
    tv.transport.qualify(found['abi'])
    if opts.existing_wholphin:
        from tv_update import check_update_target
        check_update_target(found,rehearsal=opts.dry_run_emulator)
    if opts.dry_run_emulator and opts.existing_wholphin:
        receipt=summary['familyUpdate']={}
        seed_update_rehearsal(tv,work,stack,receipt);receipt['syntheticBaselineSeeded']=True
    before=dev.manifest(text)
    summary['manifestBefore']=manifest_digest(before)
    updating=opts.existing_wholphin=='update'
    if updating and dev.APP not in before['packages']:raise RuntimeError('update requires the verified existing family app; no fresh install')
    if dev.APP in before['packages'] and not updating:
        # Its server list and settings live in app-private storage that cannot be
        # backed up or restored without root; never edit the family's copy.
        raise RuntimeError(f"Wholphin {before['wholphinVersion']} is already on the box; refusing to change the family's copy")
    state={'installed':False}
    if tv.internal:
        tv.internal_state=summary['internalRestore']={'wholphinAbsentBefore':not updating,'pathsVerifiedAbsentBeforeOptions':False}
        if not opts.dry_run_emulator:
            # Restore first. Turning off wireless debugging closes the live connection.
            def closed():
                from tv_internal import closed_endpoint
                import select
                print('Turn Wireless debugging OFF on the TV now, then press Enter (2 minute limit).',file=sys.stderr,flush=True)
                status='unverified'
                if sys.stdin.isatty() and select.select([sys.stdin],[],[],120)[0] and sys.stdin.readline():status=closed_endpoint(address)
                summary['debugEndpointAfterOff']=status
                if status!='refused':summary['restored']=False
            stack.callback(closed)
    if updating:
        # Entirely separate cleanup. An existing app never enters the disposable clear/uninstall path.
        from tv_family_ui import FamilyUI
        from tv_update import Update
        apk=release(work,found['abi'],offline=True);tv.app_digest=dev.ASSETS[found['abi']][1]
        tv.family=Update(tv,FamilyUI(DeviceScreen(tv)),work,sdk/'build-tools/36.0.0',run,summary.setdefault('familyUpdate',{}))
        stack.callback(lambda:finish_family(tv,before,state,summary,text))
        tv.family.begin(apk,before,authorized=True)
        tv.adb('reverse','--no-rebind',f'tcp:{LAB_PORT}',f'tcp:{LAB_PORT}',timeout=10);state['reverse']=True
        summary['androidFingerprint']=found['build']
        return tv
    stack.callback(lambda:finish_device(tv,before,state,opts,summary,text))
    print('Change budget: install Wholphin 1.1.2 (absent now)'
          +(', temporary app PCM and screen recording; no system sound change' if tv.internal else ', HDMI audio = PCM, music volume = max')
          +('' if opts.frame_rate_match=='leave' else ', match content frame rate = '+opts.frame_rate_match)
          +', a lab server inside Wholphin only. Everything is put back at the end.',file=sys.stderr,flush=True)
    if not tv.internal:tv.adb('shell','settings','put','global','encoded_surround_output','1')  # Never: decoded PCM only
    if opts.frame_rate_match!='leave':
        tv.adb('shell','settings','put','secure','match_content_frame_rate',{'never':'0','seamless':'1','always':'2'}[opts.frame_rate_match])
    apk=release(work,found['abi'],offline=opts.r24);tv.app_digest=dev.ASSETS[found['abi']][1]
    if b'Success' not in tv.adb('install','--no-streaming',apk,timeout=240):raise RuntimeError('Installation of disposable Wholphin not verified')
    state['installed']=True
    tv.adb('reverse','--no-rebind',f'tcp:{LAB_PORT}',f'tcp:{LAB_PORT}',timeout=10);state['reverse']=True
    summary['androidFingerprint']=found['build']
    return tv


def manifest_digest(m):
    # The family's app list stays in private scratch; the summary keeps only a digest.
    names='\n'.join(f'{k} {v}' for k,v in sorted(m['packages'].items())).encode()
    return dict(packageCount=len(m['packages']),packageListSha256=hashlib.sha256(names).hexdigest(),
                legacyAdb5555=m.get('legacyAdb5555','unknown'),settings=m['settings'],musicVolume=m['musicVolume'],wholphinVersion=m['wholphinVersion'])


def restore_family_ui(tv):
    # Called while the disposable test server and port forward are still alive.
    tx=tv.family
    tx.receipt['restoreAttempted']=True
    paths_owned=tv.internal_state.get('pathsVerifiedAbsentBeforeOptions') is True
    # Removing one capture must never suppress restoration of the family profile
    # or removal of the other capture. Recheck both after the app is restored.
    if paths_owned:
        try:tv.adb('shell','am','force-stop',APP)
        except (OSError,sp.SubprocessError):pass
    try:tx.finish()
    except Exception:
        tx.receipt['visibleStateRestored']=False
        print('Family app restore not verified. Do not clear, uninstall or downgrade Wholphin.',file=sys.stderr)
    finally:
        if paths_owned:
            for key,path in [('pcmRemoved',tv.capture.paths.wav),('screenrecordRemoved',tv.capture.paths.video)]:
                try:tv.capture.remove(tv,path);tv.internal_state[key]=True
                except Exception:tv.internal_state[key]=False



def finish_family(tv,before,state,summary,text):
    from tv_device import APP as package,manifest,diff
    tx=tv.family
    if not tx.receipt.get('restoreAttempted'):restore_family_ui(tv)
    if state.get('reverse'):
        try:tv.adb('reverse','--remove',f'tcp:{LAB_PORT}',timeout=8)
        except sp.SubprocessError:summary['restored']=False;return
    after=manifest(text);summary['manifestAfter']=manifest_digest(after);summary['manifestDiff']=diff(before,after)
    # Exactly the approved versionCode/versionName may differ. Compare all original settings,
    # volume, package names, versions, and scratch membership independently of the UI proof.
    expected=dict(before,packages=dict(before['packages']),wholphinVersion=tx.target.version if tx.target else None)
    if tx.target:expected['packages'][package]=str(tx.target.code)
    summary['unexpectedResidue']=diff(expected,after)
    summary['restored']=bool(tx.receipt.get('visibleStateRestored') and not summary['unexpectedResidue'] and
                             tv.internal_state.get('pcmRemoved') and tv.internal_state.get('screenrecordRemoved'))
    print('Family sign-in, server list, player settings and device restore verified:',summary['restored'],file=sys.stderr)


def finish_device(tv,before,state,opts,summary,text):
    import tv_device as dev
    if dev.APP in before['packages']:raise RuntimeError('disposable cleanup forbidden for a preexisting app')
    log=summary['restoreSteps']=[]
    def step(label,*args,timeout=30):
        try:tv.adb(*args,timeout=timeout);log.append(label)
        except (sp.CalledProcessError,sp.TimeoutExpired):log.append('FAILED: '+label)
    if state.get('reverse'):step('removed adb reverse port','reverse','--remove',f'tcp:{LAB_PORT}')
    if state['installed']:
        step('stopped Wholphin','shell','am','force-stop',dev.APP)
        if tv.internal and tv.internal_state['pathsVerifiedAbsentBeforeOptions']:
            for key,path in [('pcmRemoved',tv.capture.paths.wav),('screenrecordRemoved',tv.capture.paths.video)]:
                try:tv.capture.remove(tv,path);tv.internal_state[key]=True
                except (RuntimeError,sp.CalledProcessError,sp.TimeoutExpired):log.append('FAILED: remove and verify '+key)
        # The harness installed it: its data holds only the lab server entry,
        # the lab viewer sign-in, the MPV backend and the test mpv.conf.
        try:
            if tv.adb('shell','pm','clear',dev.APP,timeout=30).strip()!=b'Success':raise ValueError
            log.append('cleared Wholphin data (lab server entry, backend, mpv.conf)')
        except (ValueError,sp.CalledProcessError,sp.TimeoutExpired):log.append('FAILED: clear lab app data')
    try:now=dev.manifest(text)
    except (sp.CalledProcessError,sp.TimeoutExpired):
        summary['restored']=False;print(LOST_BOX,file=sys.stderr,flush=True);return
    for cmd in dev.restore_plan(before,now):
        if tv.internal and cmd[:2]==['shell','rm']:continue  # only capture.remove owns recording files
        step('restored: '+' '.join(cmd[1:]),*cmd)
    keep=True
    if state['installed'] and dev.APP in now['packages']:
        choice=opts.after
        if choice is None and sys.stdin.isatty():
            answer=input('Keep Wholphin on the box for the family to use? [K]eep / [u]ninstall: ').strip().lower()
            choice='uninstall' if answer.startswith('u') else 'keep'
        choice=choice or 'keep'
        if choice=='uninstall':step('uninstalled Wholphin','uninstall',dev.APP,timeout=60);keep=False
        else:log.append('kept Wholphin (data cleared) at the family\'s choice')
    after=dev.manifest(text)
    if tv.internal:
        tv.internal_state['labAppAndMpvOptionsRemoved']=dev.APP not in after['packages']
        if not tv.internal_state['labAppAndMpvOptionsRemoved']:log.append('FAILED: remove lab app and its mpv options')
    summary['manifestAfter']=manifest_digest(after)
    summary['manifestDiff']=dev.diff(before,after)
    summary['unexpectedResidue']=dev.accepted_residue(before,after,kept_wholphin=state['installed'] and keep)
    summary['restored']=not summary['unexpectedResidue'] and not any(x.startswith('FAILED') for x in log)
    print('Box before/after:',('no differences' if not summary['manifestDiff'] else '; '.join(summary['manifestDiff'])),
          '| put back:',summary['restored'],file=sys.stderr,flush=True)
    if not opts.dry_run_emulator:print(REMINDER,file=sys.stderr,flush=True)


REMINDER=('Now on the TV box: Settings > System > Developer options > turn Wireless debugging OFF '
          '(and Developer options off if you like). Then plug the box back into the living-room TV.')
LOST_BOX=('Lost the connection to the box before everything was put back. On the box: Settings > Apps > Wholphin > '
          'Uninstall (or Clear data to keep it), and Settings > Display & Sound > Audio > Audio format back to Automatic.')


def preflight(opts):
    """Read-only: list candidate capture devices and already-connected adb devices."""
    import tv_device as dev
    src=run('pactl','get-default-source').decode().strip();sink=run('pactl','get-default-sink').decode().strip()
    card=dev.default_source_card(src,run('pactl','-f','json','list','sources').decode())
    found=dev.discover(extra_ids=opts.capture_usb or (),default_source_card=card)
    print('Desktop default sink:',sink);print('Desktop default source:',src,'(never used)')
    for a in found['adapters']:
        print('Capture adapter:',a['device'],'video',a['video'],'audio card',a['cardId'])
        try:
            mode,reason=dev.choose_mode(list_modes(a['video']));print('  would capture:',mode and f"{mode['fourcc']} {mode['width']}x{mode['height']} {mode['fps'] or 'rates unlisted'}",'-',reason)
        except (sp.CalledProcessError,sp.TimeoutExpired,OSError) as exc:print('  formats unavailable:',type(exc).__name__)
    if not found['adapters']:print('Capture adapter: none found')
    for r in found['refused']:print('Not a capture adapter:',r['device'],'-',r['reason'])
    print('No adb server queried or started; capture discovery only.')
    print('Preflight changed nothing.')
    return 0


CLAIMS=('filtered handoff','picture control','marked picture','kept picture at join','real-speed join','sound control','marked sound','kept tones','kept PCM at join','lossless control','lossless filtered PCM','seek forward/back','persisted resume')


def speed_witnessed(pic,info):
    expected=info['fixtureDurationS']/info['speed']
    return .85*expected<=pic.get('witnessSpanS',0)<=1.15*expected


def picture_calibrated(pic,info):
    return bool(info.get('runtimeVerified') and pic.get('controlComplete') and speed_witnessed(pic,info))


def picture_resolved(pic,speed):
    return (pic.get('maxCaptureGapMs',float('inf'))<40/speed and not pic.get('interiorUnreadable') and
            pic.get('first')==0 and pic.get('last')==499)


def live_pcm(take):
    # The emulator checks its private QEMU sink route; the device mode checks
    # the adapter's arriving format (48 kHz stereo, decoded, not bitstream).
    route=(take['audioRouteVerified'] if 'audioRouteVerified' in take else
           take.get('activeQemuAudioRoute')==TV.expected_route)
    return bool(take.get('runtimeVerified') and not take.get('captureDeadline') and route)


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
    wc=raw.get('warmControlPicture',{}); wf=raw.get('warmFilteredPicture',{})
    warm=takes.get('warmControl',{}); warm_cut=takes.get('warmFiltered',{})
    def coverage(pic,info):
        start=' paused start' if info.get('pausedFrameZeroVerified') else ''
        return (f"{pic['distinct']}/500, {pic['marked']}/126 marked at {info['speed']:g}x{start} "
                f"({pic.get('maxCaptureGapMs','unknown')} ms max gap)"
                + ('' if pic['distinct']==500 else '; unseen '+compact_frames(pic['missingKept']+pic['missingMarked'])))
    sizes=[coverage(pic,info) for pic,info in ((c,display_normal),(rc,rep),(wc,warm),(sc,slow)) if pic]
    c,normal_pic=next(((pic,info) for pic,info in ((c,display_normal),(rc,rep)) if picture_calibrated(pic,info)),(c,display_normal))
    if picture_calibrated(wc,warm) and wf.get('filteredComplete') and has_routes(warm_cut) and warm_cut.get('runtimeVerified'):
        c,normal_pic,f,cut=wc,warm,wf,warm_cut
    real_valid=picture_calibrated(c,normal_pic); slow_valid=picture_calibrated(sc,slow)
    if sizes:
        out['picture control']=('PASS' if real_valid or slow_valid else 'FAIL','; '.join(sizes))
    if real_valid or slow_valid:
        use_slow=slow_valid and (not real_valid or (not f.get('marked') and not f.get('filteredComplete') and not picture_resolved(f,1)))
        pic,info,source_info=(sf,slow_cut,slow) if use_slow else (f,cut,normal_pic)
        if pic and info.get('runtimeVerified') and has_routes(info) and source_info['displayTap']==info['displayTap'] and speed_witnessed(pic,info):
            gap=pic.get('maxCaptureGapMs',float('inf')); speed=info['speed']
            resolved=picture_resolved(pic,speed)
            join_gap=pic.get('joinMaxCaptureGapMs')
            limit=f"{speed:g}x display; max gap {gap:g} ms"+(f", join window {join_gap:g} ms" if join_gap is not None else '')
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
        kept_ok=marked_ok and all(t['440']>=5475 and t['660']>=9475 for t in ct+rt)
        lr=lambda vals:'/'.join(f'{x:g}' for x in vals) or 'unavailable'
        matches=lambda tones,hz:lr([t[hz] for t in tones])
        peak=lr(a['capturePeakLSB']); noise=lr([a['preRollRmsLSB']]+([ar['preRollRmsLSB']] if ar else []))
        out['sound control']=('PASS' if marked_ok else 'FAIL',f"880 Hz L/R {matches(ct,'880')} and {matches(rt,'880')} ms (expected 5040); 48 kHz stereo, pre-roll {noise} LSB RMS, peak {peak} LSB; 5 ms at 0.10 FS")
        filtered=bool(b and live_pcm(sound_cut) and has_routes(sound_cut))
        if not marked_ok:
            observed=f"; filtered detector {matches(b['tones'],'880')} ms L/R, not a leak count" if filtered else ''
            out['marked sound']=('not-measured','Two uncut controls did not calibrate the known marked tone'+observed)
        elif not filtered:
            out['marked sound']=('not-measured','No verified filtered render')
        else:
            vals=b['tones']; leak=any(t['880'] for t in vals)
            out['marked sound']=('FAIL' if leak else 'not-measured',f"880 Hz detector L/R {matches(vals,'880')} ms at >=0.10 FS in 5 ms windows; not sample identity")
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
        if w.get('controlVsFiltered') and filtered:
            obs=w['controlVsFiltered'];base=w['repeatability'];before,after='joinBefore20ms','joinAfter20ms'
            def region(k):
                x=obs[k]
                return (f"{x['differentSampleFrames']}/{x['comparedSampleFrames']}, "
                        f"{lr(x['errorRmsLSB'])} vs {lr(base[k]['errorRmsLSB'])} LSB RMS floor")
            rendered=w['renders']['filtered']; probes=[p for r in w['renders'].values() for p in r['probes'].values()]
            stable=bool(probes) and all(p.get('observedOffsetSamples')==0 for p in probes)
            exact=stable and w['uncutBitExact'] and rendered['againstDecodedAtNonzeroSpan']['exact']
            found=stable and any(not obs[k]['withinFloor'] and obs[k]['floorBelowSignal'] for k in (before,after))
            status='PASS' if exact else 'FAIL' if found else 'not-measured'
            out['kept PCM at join']=(status,f"Fixed origin; before {region(before)}; after {region(after)}; "
                                    f"span at 512 LSB {rendered['signalSpanAt512LSBFrames']}/{rendered['expectedFrames']} samples"
                                    + ('' if stable else '; constant delay not established'))
        else:
            out['kept PCM at join']=('not-measured','No complete matched render comparison after calibrating two uncut controls')
    flac=raw.get('losslessWaveform',{})
    lossless=raw.get('lossless',{})
    if flac.get('repeatability'):
        n=flac['renders']['control']['expectedFrames']
        same=flac['uncutBitExact']
        def control_brief(name):
            r=flac['renders'][name];d=r['againstDecodedAtNonzeroSpan']
            return f"{r['nonzeroSpanFrames']} span, {d['unequalFrames']} differing"
        out['lossless control']=('PASS' if same else 'FAIL',
            f"FLAC verified in libmpv; expected {n} samples; {control_brief('control')}; repeat {control_brief('repeatControl')}; bit-exact {same}")
    elif lossless:
        out['lossless control']=('not-measured',lossless.get('limit',raw.get('scoreIssues',{}).get('losslessWaveform','No calibrated FLAC output')))
    out['lossless filtered PCM']=('not-measured','Pinned plugin accepts AAC only; FLAC control was not a filtered projection')
    ops=raw.get('controlOperations',{});seeks=ops.get('pausedSeeks',[])
    if seeks:
        n=sum(x['exact'] for x in seeks); landed=[x['observed'][-1] if x['observed'] else None for x in seeks]
        handoff_ok=has_routes({'grantRouteDeltas':ops.get('seekGrantRouteDeltas',[])})
        obs=f"{n}/6 exact paused landings {landed}; barcode snapshots only, no intervening PCM"
        out['seek forward/back']=('PASS' if n==6 and handoff_ok else 'FAIL' if handoff_ok and (real_valid or slow_valid) else 'not-measured',obs)
    elif ops.get('seekLimit') or ops.get('limits'):
        out['seek forward/back']=('not-measured',ops.get('seekLimit') or ops['limits'][0])
    if 'resumePicture' in ops:
        r=ops['resumePicture']; info=ops['resumeTake']; want=ops['expectedResumeFrame']; got=r['first']
        via=ops['resumeVia']; ack=ops.get('backAcknowledged') if via.startswith('Back') else ops['stopAcknowledged']
        ok=(ack and info.get('runtimeVerified') and has_routes(info) and
            (real_valid or slow_valid) and got==want and not r['interiorUnreadable'])
        out['persisted resume']=('PASS' if ok else 'not-measured',f"{via}: stored {ops['bookmarkTicks']} filtered ticks; first readable Resume frame {got}, expected {want}; earlier unreadable frames not excluded")
    elif ops.get('limits'):out['persisted resume']=('not-measured',ops['limits'][-1])
    return out


def calibration(raw):
    """Is the capture path exact? Reported before any filtered comparison is trusted."""
    takes=raw.get('recordings',{})
    def exact(key):
        w=raw.get(key,{})
        if not w.get('renders'):return None
        probes=[p for r in w['renders'].values() for p in r.get('probes',{}).values()]
        return dict(bitExact=w.get('uncutBitExact'),constantDelay=bool(probes) and all(p.get('observedOffsetSamples')==0 for p in probes))
    seen=[raw.get(k+'Picture',{}) for k in ('control','repeatControl','controlDisplay','warmControl')]
    return dict(pictureAll500InOrder=any(p.get('controlComplete') for p in seen),
                pictureMarkedSeen=max((p.get('marked',0) for p in seen),default=0),
                arrivingAudio={k:v['arrivingAudio'] for k,v in takes.items() if 'arrivingAudio' in v},
                aac=exact('waveform'),flac=exact('losslessWaveform'))


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
    parser.add_argument('--projection-control',action='store_true',help='emulator-only uncut Dolby audio measurement, never admission')
    parser.add_argument('--r24',action='store_true',help='measure the Dolby test-tier gate; never installs evidence')
    parser.add_argument('--layout',choices=('mp4-h264-eac351','mkv-h264-ac351','mkv-h264-eac351'))
    parser.add_argument('--jellyfin',choices=('12.4','10.11'),help='R24 default: 12.4; legacy AAC default: 10.11')
    box=parser.add_argument_group('real TV box (HDMI capture adapter on this PC)')
    box.add_argument('--device',action='store_true',help='measure the Google TV box over wireless debugging; pairing details are prompted or read from CLEANYFIN_TV_PAIR, CLEANYFIN_TV_CODE, CLEANYFIN_TV_CONNECT for this run only')
    box.add_argument('--existing-wholphin',choices=('update',),help='explicit signed 1.0.9 to 1.1.2 in-place update; requires separate preservation rehearsal')
    box.add_argument('--no-capture',action='store_true',help='synthetic Android screenrecord + decoded mpv PCM file, not HDMI output')
    box.add_argument('--preflight',action='store_true',help='list local capture adapters; no adb or device connection')
    box.add_argument('--capture-usb',action='append',metavar='VID:PID',help='accept this USB id as the HDMI capture adapter')
    box.add_argument('--capture-file',action='append',metavar='[TAKE=]PATH',help='dry run: score recorded files instead of the adapter (no candidate row)')
    box.add_argument('--dry-run-emulator',action='store_true',help='dry run: drive the harness emulator through the device path over adb TCP')
    box.add_argument('--expect-model',help='refuse unless ro.product.model equals this')
    box.add_argument('--frame-rate-match',choices=('leave','never','seamless','always'),default='never',help='box "match content frame rate" for the run (default never: a constant 60 Hz signal for the adapter; restored afterwards)')
    box.add_argument('--after',choices=('keep','uninstall'),help='Wholphin at the end, if the harness installed it (default: ask; keep)')
    opts=parser.parse_args()
    if opts.projection_control and (not opts.r24 or opts.device or opts.preflight or opts.keep or opts.capture_usb or opts.split_capture):parser.error('projection control needs only the fresh private R24 emulator')
    if opts.layout and not opts.r24:parser.error('--layout needs --r24')
    if opts.existing_wholphin and (not opts.no_capture or opts.after or opts.frame_rate_match!='leave'):
        parser.error('update requires --no-capture --frame-rate-match leave; never use --after on a family app')
    if opts.no_capture:
        if not (opts.device and opts.r24) or opts.capture_file or opts.capture_usb or opts.preflight or opts.after=='keep':
            parser.error('--no-capture needs --device --r24 and lab-app removal; no adapter or file substitute')
        if not opts.existing_wholphin:opts.after='uninstall'
    if opts.r24 and (opts.keep or opts.split_capture or opts.capture_file or (opts.dry_run_emulator and not opts.no_capture)):
        parser.error('R24 requires simultaneous live taps, disposable synthetic data only')
    if opts.r24:
        opts.layout=opts.layout or 'mp4-h264-eac351';opts.jellyfin=opts.jellyfin or '12.4'
        feed=Path(os.environ.get('CLEANYFIN_NUGET_FEED') or Path.home()/'.cache/cleanyfin-scratch/nuget-feed')
        if not feed.is_dir():parser.error('R24 requires a pre-staged offline NuGet feed')
        os.environ['CLEANYFIN_NUGET_FEED']=str(feed.resolve())
    if opts.preflight:return preflight(opts)
    if opts.device and not opts.dry_run_emulator:
        parser.error('Physical playback remains closed pending verified emulator capture and update rehearsals; no device contacted')
    if (opts.capture_file or opts.dry_run_emulator) and not opts.device:parser.error('--capture-file and --dry-run-emulator need --device')
    if opts.dry_run_emulator and not (opts.capture_file or opts.no_capture):parser.error('--dry-run-emulator has no HDMI adapter; give --capture-file')
    try:import numpy  # Fail before starting services if PCM analysis cannot run.
    except ImportError:parser.error('NumPy is required for rendered PCM clock and gain analysis')
    if not __debug__:parser.error('Python assertions are required')
    if opts.llvmpipe_threads is not None and opts.gpu!='host':parser.error('--llvmpipe-threads requires --gpu host')
    proof=None
    if opts.no_capture:
        from tv_internal import harness_digest,prior_dry_run
        digest=harness_digest(HERE)
        if not opts.dry_run_emulator:
            runs=HERE.parents[1]/'delivery/evidence/runs'
            proof={'capture':prior_dry_run(runs,digest)}
            if opts.existing_wholphin:proof['familyPreservation']=prior_dry_run(runs,digest,update=True)
            if not all(proof.values()):parser.error('Required isolated emulator capture/preservation rehearsal missing for this harness; the physical box was not contacted')
    if opts.r24:
        from tv_device import ASSETS
        try:import tv_r24
        except ImportError as exc:parser.error(f'R24 requires NumPy and SciPy before any device contact ({exc.name} missing)')
        for abi in (('armeabi-v7a','arm64-v8a') if opts.device and not opts.dry_run_emulator else ('x86_64',)):
            name,sha,size,_=ASSETS[abi]
            apkpath=Path(os.environ.get('CLEANYFIN_APK_FEED') or Path.home()/'.cache/cleanyfin-scratch/apks')/name
            if not apkpath.is_file() or apkpath.stat().st_size!=size or hashlib.sha256(apkpath.read_bytes()).hexdigest()!=sha:
                parser.error('Offline Wholphin release missing or mismatched: '+name+'; no device contacted')
    start=time.monotonic(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); uid=secrets.token_hex(5)
    work=Path(tempfile.mkdtemp(prefix='cleanyfin-tv-'));sdk=Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk').resolve()
    output=HERE.parents[1]/'delivery/evidence/runs'/('tv-'+stamp+'-'+uid)
    summary={'startedUtc':stamp,'recording':'Synthetic fixture only; Android compositor display (host recorder fallback), isolated PipeWire sink monitor s16le stereo 48000 Hz'}
    if opts.projection_control:summary.update(mode='projection-control',recording='AudioPlaybackCapture; uncut Wholphin UID/USAGE_MEDIA only; no picture, microphone or HDMI')
    if opts.device:
        summary.update(mode='device-internal' if opts.no_capture else 'device',dryRun=bool(opts.capture_file or opts.dry_run_emulator),
                       recording=('Synthetic Android screenrecord with per-frame PTS and libmpv ao=pcm decoded WAV; no AudioTrack, HDMI or passthrough witness' if opts.no_capture else 'DRY RUN: recorded files through the device capture path' if opts.capture_file else
                                  'Synthetic fixture only; box HDMI output via USB capture adapter: UVC video stored 320x180 gray FFV1, UAC s16le stereo 48000 Hz'))
    if opts.no_capture:summary.update(captureHarnessSha256=digest,emulatorProof=proof)
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
        if not opts.device:signal.alarm(13500 if opts.r24 else 1200)
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
            emulated=not opts.device or opts.dry_run_emulator
            if emulated and not (sdk/'emulator/emulator').is_file():raise RuntimeError('Android emulator SDK unavailable')
            if emulated:
                check=run(sdk/'emulator/emulator','-accel-check').decode()
                if 'KVM' not in check or 'usable' not in check:raise RuntimeError('KVM not usable')
                summary['emulatorVersion']=(sdk/'emulator/source.properties').read_text().split('Pkg.Revision=')[-1].splitlines()[0].strip()
            def host_audio():
                listed=lambda kind:sorted(line.split('\t')[1] for line in run('pactl','list','short',kind).decode().splitlines())
                return (run('pactl','get-default-sink').decode().strip(),listed('sinks'),run('pactl','get-default-source').decode().strip(),listed('sources'))
            before=host_audio()
            if opts.device:
                raw['milestone']='box connection, identity and change manifest'
                tv=device_session(opts,work,sdk,stack,summary,history)
                stack.callback(signal.alarm,0)  # never interrupt the restore
                signal.alarm(13500 if opts.r24 else 1200)
                summary['apkSha256']=tv.app_digest
            else:
                raw['milestone']='official Wholphin release verification'
                apk=release(work,offline=opts.r24);summary['apkSha256']=DIGEST
                raw['milestone']='private emulator and audio boot'
                tv=TV(work,sdk,stack,opts.gpu,opts.ram_mib,opts.llvmpipe_threads,opts.r24)
                summary.update(declaredGpu=opts.gpu,llvmpipeThreadsRequested=opts.llvmpipe_threads,guestRamMiB=opts.ram_mib);tv.host_history=history
                from adb_lab_playback import Playback
                tv.transport=Playback.guest_session(stack,work,sdk,receipt=summary)
                tv.projection_control=opts.projection_control
                tv.boot();summary['graphicsAdapter']=tv.graphicsAdapter
                print('Emulator graphics:',tv.graphicsAdapter or opts.gpu+' requested; renderer not reported',file=sys.stderr,flush=True)
                tv.adb('install','--no-streaming',apk,timeout=75)
                summary['androidFingerprint']=tv.adb('shell','getprop','ro.build.fingerprint').decode().strip();summary['audioInputFormat']=tv.call('pactl','list','short','sink-inputs').decode().strip().split('\t')[-1]
                mem=re.search(rb'^MemTotal:\s+(\d+) kB',tv.adb('shell','cat','/proc/meminfo'),re.M)
                if mem:summary['guestKernelMemTotalKiB']=int(mem[1])
            raw['milestone']='disposable Jellyfin build and setup'
            lab=setup_lab(work,tv,stack,opts.jellyfin,opts.layout);summary['pluginBuild']=lab.plugin_build;summary['fixtureSha256']=lab.r24_fixture['sha256'] if opts.r24 else hashlib.sha256(lab.original).hexdigest()
            row=lab.stub_register()['rows'][0];row['controls']=[];row['status']='experimental'
            raw['milestone']='Wholphin login, enrollment and MPV settings'
            if getattr(tv,'family',None):
                name='Cleanyfin Lab '+uid
                cfg=lab.api('/System/Configuration',user=lab.admin);cfg['ServerName']=name;lab.api('/System/Configuration','POST',cfg,user=lab.admin)
                if lab.api('/System/Info/Public').get('ServerName')!=name:raise RuntimeError('unique lab server identity not verified')
                tv.family.ui.prepare_lab(name,tv.family.original)
                tv.family.lab_added=True;stack.callback(restore_family_ui,tv)
            ui=(DeviceScreen if opts.device else Screen)(tv);ui.setup(lab);raw['verifiedSettings']=ui.verified;raw['sessionIdentity']=lab.tv_identity
            print('Disposable TV enrollment:',json.dumps(lab.tv_identity),file=sys.stderr,flush=True)
            if not opts.device:
                raw['milestone']='separate stock transport test input install'
                install_input(tv)
            summary['splitCapture']=opts.split_capture
            if opts.projection_control:
                import tv_projection
                row=None;raw['milestone']='AudioPlaybackCapture uncut control'
                tv_projection.measure(sys.modules[__name__],tv,lab,raw,module('format-matrix'))
            elif opts.r24:
                import tv_r24
                row=None
                tv_r24.measure(sys.modules[__name__],tv,lab,ui,raw,module('format-matrix'))
            else: measure(tv,lab,ui,raw,opts.split_capture)
    except (Exception,KeyboardInterrupt) as exc:
        # Never serialize child command lines, queries, login codes, file paths or stderr.
        import traceback
        sites=[f'{Path(f.filename).name}:{f.name}:{f.lineno}' for f in traceback.extract_tb(exc.__traceback__)
               if Path(f.filename).parent==HERE]
        raw['blockerSites']=sites[-7:]
        if isinstance(exc,sp.CalledProcessError):
            raw['childExit']=exc.returncode
            if hasattr(exc,'playback_read'):raw['failedStockRead']=exc.playback_read
        if isinstance(exc,sp.TimeoutExpired):error=timeout_label(raw.get('milestone','setup'),list(history))
        else:error=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__
    finally:
        signal.alarm(0);done.set();watcher.join(timeout=3);peak=max(peak,allocated(work));summary['scratchPeakBytesObserved']=peak
        summary['hostWorstObservedIo']=worst_io;summary['hostWorstScopedFullPsi']=worst_scoped;summary['hostAfterRun']=host_health()
        if 'before' in locals():
            after=host_audio();summary['defaultSinkUnchanged']=before[0]==after[0];summary['hostSinkListUnchanged']=before[1]==after[1]
            summary['defaultSourceUnchanged']=before[2]==after[2];summary['hostSourceListUnchanged']=before[3]==after[3]
        emu=tv.emulator if 'tv' in locals() and isinstance(tv,DeviceTV) else tv if 'tv' in locals() else None
        if emu is not None:summary['privateNamespaceExited']=emu.ns.poll() is not None
        if emu is not None and hasattr(emu,'emu'):summary['emulatorExited']=emu.emu.poll() is not None
        if 'label' in locals():summary['ownedContainersRemaining']=len(run(docker,'ps','-aq','--filter','label='+label).split())
        if opts.keep:summary['privateScratch']=str(work)
        else:shutil.rmtree(work)
        summary['scratchRemoved']=not work.exists()
    summary.update(elapsedSeconds=round(time.monotonic()-start,1),blocker=error)
    if opts.projection_control:
        row=None
        result=raw.get('projection',{}).get('comparison',{}).get('outcome','unmeasured')
        claims={'projection uncut control':('PASS' if result=='bit-exact' and not error else 'not-measured' if result=='unmeasured' or error else 'deviation',error or result)}
    elif opts.r24:
        # No v1 candidate, even on interruption partway through TV setup. Receipts are reviewed separately.
        from tv_r24 import verdict
        if opts.no_capture and summary.get('restored') is not True:error=summary['blocker']=error or 'internal recording restore unverified'
        row=None;claims={'R24 '+opts.layout:verdict(raw,error)}
    else:
        claims=verdicts(raw,error)
        if opts.device:summary['calibration']=calibration(raw)
    summary['claims']=[status+' '+name+' — '+limit for name,(status,limit) in claims.items()]
    # Never turn the supplemental quarter-speed or FLAC observation into a
    # verdict about the candidate's 1x AAC tuple.
    if 'verifiedSettings' not in raw or not has_routes(raw.get('recordings',{}).get('filtered',{})):row=None
    if summary.get('dryRun'):row=None  # Recorded files are not the box's output: never candidate evidence.
    output.mkdir(parents=True,exist_ok=False);summary['evidenceDirectory']=str(output.relative_to(HERE.parents[1]));summary['candidateWritten']=row is not None
    if row is not None:
        normal_claims=verdicts({k:v for k,v in raw.items() if k not in ('slowControlPicture','slowFilteredPicture')},error)
        row.update(date=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8],evidenceRef=summary['evidenceDirectory']+'/summary.json',observation=(
            'Physical box HDMI output through a USB capture adapter; verified MPV settings and 1x AAC takes only. FLAC and quarter-speed takes are not candidate evidence. Never grants eligibility.'
            if opts.device else 'Synthetic emulator, verified MPV settings and 1x AAC takes only. Supplemental quarter-speed/FLAC takes are not candidate evidence. Split taps still play both tracks. Never grants eligibility.'))
        if normal_claims['filtered handoff'][0]=='PASS':row['controls'].append('straight-play')
        if normal_claims['seek forward/back'][0]=='PASS':row['controls']+=['seek-forward','seek-back']
        if normal_claims['persisted resume'][0]=='PASS':row['controls'].append('resume')
        for key,name in [('markedPicture','marked picture'),('markedSound','marked sound'),('keptPictureAtJoin','kept picture at join'),('keptSoundAtJoin','kept PCM at join')]:
            status,limit=normal_claims[name];row['results'][key]={'outcome':status.lower(),'limit':limit}
        (output/'candidate.json').write_text(json.dumps(row,indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if opts.json:print(json.dumps(summary))
    else:
        if 'verifiedSettings' not in raw:print('Effective backend and mpv options: not verified; no playback evidence')
        print('\n'.join(summary['claims']));print('Run',summary['evidenceDirectory'],f"({summary['elapsedSeconds']} s, {peak} scratch bytes observed)")
        if 'before' in locals():print('Cleanup:',{k:summary.get(k) for k in ('emulatorExited','ownedContainersRemaining','privateNamespaceExited','hostSinkListUnchanged','defaultSinkUnchanged','hostSourceListUnchanged','defaultSourceUnchanged','scratchRemoved') if k in summary})
        if opts.device:
            print('Box put back:',summary.get('restored'),'| before/after differences:',summary.get('manifestDiff'))
            if summary.get('calibration'):print('HDMI path calibration:',json.dumps(summary['calibration']))
        if error:print('Stopped:',error)
        if opts.keep:print('Private retained data:',work)
    return 0 if all(v[0]=='PASS' for v in claims.values()) and not error else 1

if __name__=='__main__':sys.exit(main())



