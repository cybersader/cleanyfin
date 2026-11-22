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
APK = 'Wholphin-x86_64.apk'
DIGEST = '60305215500d033ffd55333adc0066a8ab15dc146f2262735cbb6e7e60ce692d'
REPO = 'damontecres/Wholphin'
TAG = 'v1.1.2'
MPV_OPTIONS = 'hwdec=no\nvo=gpu\ngpu-context=android\nopengl-es=yes\nmsg-level=all=v'
PICTURE_SPEED = .25
DISPLAY_CAPTURE_SIZE = '320x180'
MEDIA_MARGIN_SECONDS = 14


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
    def __init__(self, work, sdk, stack, gpu='swangle'):
        self.work, self.sdk, self.stack, self.gpu = work, sdk, stack, gpu
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
        self.emu = self.spawn(self.prefix+[em/'qemu/linux-x86_64/qemu-system-x86_64','-avd','lab','-accel','on','-no-window',
                              '-no-snapshot','-no-boot-anim','-port','5580','-no-metrics','-gpu',self.gpu,'-feature','-Vulkan',
                              '-audio','pa','-crash-report-mode','disabled','-show-kernel','-memory','2048','-cores','4'], 'emulator')
        def ready():
            if self.emu.poll() is not None:
                raise RuntimeError('Emulator exited before Android boot completed')
            return self.adb('shell','getprop','sys.boot_completed', timeout=3).strip() == b'1'
        eventually(ready, 240, 'Android did not complete boot in 240 s')
        log=(self.work/'emulator.log').read_text(errors='replace')
        self.graphicsAdapter=next((m[1].strip() for m in re.finditer(r'Graphics Adapter (?!Vendor\b)([^\r\n]{1,120})',log)),None)
        self.graphicsClass='llvmpipe' if self.graphicsAdapter and 'llvmpipe' in self.graphicsAdapter.lower() else self.gpu


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
    lab.wholphin['tuple'].update(appBuild=DIGEST,deviceMode=f'Google TV API 36 x86_64 emulator 1280x720, {tv.graphicsClass}, software video vo-gpu Android GLES, stereo PCM')
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

    def action(self,op,value):
        import base64
        val=base64.b64encode(value.encode()).decode()
        response=self.tv.adb('shell','am','instrument','-w','-e','op',op,'-e','value',val,INPUT+'/.TvInput',timeout=12)
        return b'result=applied' in response

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

    def enter_advanced(self):
        self.wait_label('Home')
        def sidebar():
            nodes=self.tree();f=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if f is not None and any((n.get('text') or n.get('content-desc')) in ('Home','Search','Favorites','Settings','Original') for n in f.iter()) and any(n.get('text')=='Settings' for n in nodes):return True
            self.key('DPAD_LEFT');return False
        eventually(sidebar,40,'D-pad did not enter the navigation drawer');self.dpad('Settings',cap=14)
        self.wait_label('Remember selected tabs');self.dpad('Advanced Settings')
        self.wait_label('Show Clock')

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
        l,t,r,b=eventually(gear,8,'Playback controls did not expose the stock settings button')
        self.tv.adb('shell','input','tap',(l+r)//2,(t+b)//2)
        self.wait_label('Playback Speed')
        self.dpad('Playback Speed',cap=4)
        self.wait_label('1.0')
        self.dpad('.25',step='DPAD_UP',cap=5)
        self.wait_label('Playback Speed')
        self.wait_label('0.25')
        self.key('BACK')  # Return from stock settings; controls auto-hide.
        time.sleep(4)
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


def record(tv,lab,item,label,speed,info,begin=None):
    audio=tv.work/(label+'.pcm')
    duration=20 if item==lab.item else 14.96
    limit=duration/speed+MEDIA_MARGIN_SECONDS
    info.update(speed=speed,fixtureDurationS=duration,wallCeilingS=limit,endSignal='fixture duration plus startup margin (no end signal)')
    with ExitStack() as s:
        mon=s.enter_context(audio.open('wb')); err=s.enter_context((tv.work/(label+'-audio.log')).open('wb'))
        proc=sp.Popen(tv.prefix+['parec','--device=fixture.monitor','--format=s16le','--rate=48000','--channels=2','--raw','--latency-msec=20'],env=tv.env,stdout=mon,stderr=err)
        def stop():
            proc.terminate()
            try: proc.wait(timeout=5)
            except sp.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        s.callback(stop)
        eventually(lambda:audio.stat().st_size>=38400,12,'Private monitor did not yield 200 ms of PCM')
        video=capture_display(tv,s,label,limit,info)
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
                    time.sleep(.75)
                time.sleep(.75)  # Keep the renderer/monitor tail past the end signal.
            except expired:
                pass
        info['watchedS']=round(time.monotonic()-launch,2)
        if pid.isdecimal():
            try:
                logs=tv.adb('logcat','-d','--pid='+pid,'-v','threadtime',timeout=3)
                (tv.work/(label+'-logcat.log')).write_bytes(logs)
                app_runtime(logs.decode(errors='replace'),info)
            except (sp.CalledProcessError,sp.TimeoutExpired):pass
    info['videoBytes']=video.stat().st_size if video.is_file() else 0
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


def session_command(lab, session, name, at=None):
    path='/Sessions/'+session['Id']+'/Playing/'+name
    if at is not None:path+='?seekPositionTicks='+str(at*10000)
    lab.api(path,'POST',user=lab.one)


def shown_frame(tv,scorer):
    data=tv.adb('exec-out','screencap','-p',timeout=8)
    raw=run('ffmpeg','-v','error','-f','image2pipe','-i','pipe:0','-vf','scale=160:90:flags=area,format=gray','-frames:v','1','-threads:v','1','-f','rawvideo','pipe:1',input=data,timeout=5)
    return scorer.read_barcode(raw,500) if len(raw)==14400 else None


def prepare_slow(tv,lab,ui,item,scorer,info):
    tv.adb('logcat','-c')
    play(tv,lab,item)
    session=eventually(lambda:now(lab,item),15,'Quarter-speed setup: no native playback session')
    cmd=lambda name,at=None:session_command(lab,session,name,at)
    paused=lambda:(now(lab,item) or {}).get('PlayState',{}).get('IsPaused') is True
    cmd('Pause')
    eventually(paused,8,'Quarter-speed setup: Pause not acknowledged')
    info['speedSetting']=ui.slow_playback()
    if not paused():raise RuntimeError('Quarter-speed setup: player left paused state')
    cmd('Seek',0)
    eventually(lambda:shown_frame(tv,scorer)==0,8,'Quarter-speed setup: paused seek did not present frame zero')
    return lambda:cmd('Unpause')


def measure(tv,lab,ui,out):
    from tv_score import picture,audio,compare
    scorer=module('analyze-capture'); takes=out['recordings']={}
    out['audioLevels']=configure_audio(tv)
    print('Private audio setup:',json.dumps(out['audioLevels']),file=sys.stderr,flush=True)
    def score(key,scorer_fn,*args):
        try:out[key]=scorer_fn(*args)
        except (ValueError,OSError,sp.CalledProcessError) as exc:
            out.setdefault('scoreIssues',{})[key]=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    def take(label,item,speed,*,sound=False,filtered=False):
        out['milestone']=label
        before=handoff(lab) if filtered else None
        offset=lab.wire_log.stat().st_size
        info=takes[label]={'speed':speed}
        try:
            begin=prepare_slow(tv,lab,ui,item,scorer,info) if speed!=1 else None
        except RuntimeError as exc:
            if not str(exc).startswith(('Quarter-speed setup:','Playback controls did not expose','Wholphin did not display','No focused')):raise
            info['setupLimit']=str(exc)
            tv.adb('shell','am','force-stop',APP)
            print(f'{label}: {exc}',file=sys.stderr,flush=True)
            return None
        v,a=record(tv,lab,item,label,speed,info,begin)
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
        # A broken instrument, missing stop report or invalid calibration on
        # one channel cannot discard the other channel's observations.
        score(label+'Picture',picture,v,scorer)
        if sound:score(label+'Sound',audio,a,scorer)
        # Force-stop only after capture was finalized; each intent starts from
        # zero independently, even when a session was left stale at the limit.
        tv.adb('shell','am','force-stop',APP)
        return a
    ca=take('control',lab.item,1,sound=True)
    fa=take('filtered',lab.pointer,1,sound=True,filtered=True)
    if 'controlSound' in out:
        ref=scorer.media(lab.work/'fixture/source.mp4','ffmpeg','-i','/lab','-map','0:a:0','-f','s16le','-ac','2','-ar','48000','pipe:1',readonly=True)
        matched=has_routes(takes['filtered']) and takes['filtered'].get('runtimeVerified') and 'filteredSound' in out
        score('waveform',compare,ca,fa if matched else None,ref,out['controlSound']['preRollPeakLSB'],
              out['filteredSound']['preRollPeakLSB'] if matched else None)
    if has_routes(takes['filtered']):
        controls(tv,lab,ui,out)
    if not out.get('controlPicture',{}).get('controlComplete'):
        take('slowControl',lab.item,PICTURE_SPEED)
        if takes['slowControl'].get('speedSetting'):
            out['verifiedSlowSettings']=takes['slowControl']['speedSetting']
        if has_routes(takes['filtered']) and picture_calibrated(out.get('slowControlPicture',{}),takes['slowControl']):
            take('slowFiltered',lab.pointer,PICTURE_SPEED,filtered=True)


def controls(tv,lab,ui,out):
    """Exercise the player's own Jellyfin remote commands and visible resume choice."""
    from uuid import UUID
    scorer=module('analyze-capture'); receipt=out['controlOperations']={}
    def witness(fn,limit,reason):
        try:return eventually(fn,limit,reason)
        except RuntimeError as exc:
            if str(exc)!=reason:raise
            receipt.setdefault('limits',[]).append(reason)
            return None
    play(tv,lab,lab.pointer)
    s=witness(lambda:now(lab,lab.pointer),20,'Control playback did not start')
    if not s:return
    def command(name,at=None):return session_command(lab,s,name,at)
    def shown():return shown_frame(tv,scorer)
    command('Pause')
    receipt['pauseAcknowledged']=bool(witness(lambda:(now(lab,lab.pointer) or {}).get('PlayState',{}).get('IsPaused'),10,'Player did not acknowledge pause'))
    # Paused seek destinations on both sides and exactly at the cut. Never use
    # server playhead ticks alone as evidence of what was actually presented.
    if receipt['pauseAcknowledged']:
        for ms in (7600,2400,8000,3600,5480,7600):
            target=ms//40+(126 if ms>=5480 else 0);command('Seek',ms)
            observed=[]
            def landed():
                n=shown()
                if n is not None:observed.append(n)
                return n==target
            exact=bool(witness(landed,5,f'No exact paused seek landing at {ms} ms'))
            receipt.setdefault('pausedSeeks',[]).append(dict(filteredMs=ms,expectedFrame=target,observed=observed,exact=exact))
    if now(lab,lab.pointer):command('Stop')
    receipt['stopAcknowledged']=bool(witness(lambda:not now(lab,lab.pointer),10,'No Jellyfin session clear after Stop'))
    def ticks():
        return lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
    receipt['bookmarkTicks']=witness(ticks,10,'No persisted pointer bookmark')
    if not receipt['bookmarkTicks']:return
    tv.adb('shell','am','force-stop',APP)
    tv.adb('shell','am','start','-n',APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.api('/System/Info/Public')['Id'])))
    matches=lambda:[n for n in ui.tree() if n.get('package')==APP and any('resume' in n.get(k,'').lower() for k in ('text','content-desc'))]
    nodes=witness(matches,15,'No actual Resume choice after stopping Wholphin')
    if not nodes:return
    n=nodes[0];label=n.get('content-desc') or n.get('text')
    try:ui.activate(label)
    except RuntimeError as exc:
        if not str(exc).startswith('Focus did not reach'):raise
        receipt.setdefault('limits',[]).append('Resume choice visible but did not receive focus');return
    def sample():
        frame=shown()
        return (frame,) if frame is not None else None
    seen=witness(sample,10,'Resume picture unreadable')
    if seen:
        receipt['resumedFrame']=seen[0]
        receipt['resumeObservation']='One sample after actual Resume; not the first presented frame'


CLAIMS=('filtered handoff','picture control','marked picture','kept picture at join','real-speed join','sound control','marked sound','kept tones','kept PCM at join','seek forward/back','persisted resume')


def picture_calibrated(pic,info):
    return bool(info.get('runtimeVerified') and pic.get('controlComplete') and
                (info['speed']==1 or pic['witnessSpanS']>=.85*info['fixtureDurationS']/info['speed']))


def compact_frames(frames):
    from hard_fixture_lab import ranges
    return ','.join(str(a) if a==b else f'{a}–{b}' for a,b in ranges(frames)) or 'none'


def verdicts(raw,reason):
    out={k:('not-measured',reason or 'No calibrated capture') for k in CLAIMS}
    takes=raw.get('recordings',{}); normal=takes.get('control',{}); cut=takes.get('filtered',{})
    c=raw.get('controlPicture',{});f=raw.get('filteredPicture',{});a=raw.get('controlSound',{});b=raw.get('filteredSound',{});w=raw.get('waveform',{})
    sc=raw.get('slowControlPicture',{});sf=raw.get('slowFilteredPicture',{}); slow=takes.get('slowControl',{}); slow_cut=takes.get('slowFiltered',{})
    if 'grantRouteDeltas' in cut:
        out['filtered handoff']=('PASS' if has_routes(cut) else 'FAIL',
            f"Pointer POST / list GET / original range GET: {cut['grantRouteDeltas']}; 1x")
    real_valid=picture_calibrated(c,normal); slow_valid=picture_calibrated(sc,slow)
    def coverage(pic,info):
        return f"{pic['distinct']}/500, {pic['marked']}/126 marked at {info['speed']:g}x (max gap {pic.get('maxCaptureGapMs','unknown')} ms)"
    sizes=[coverage(pic,info) for pic,info in ((c,normal),(sc,slow)) if pic]
    if sizes:
        out['picture control']=('PASS' if real_valid or slow_valid else 'FAIL','; '.join(sizes))
    if real_valid or slow_valid:
        pic,info,source_info=(f,cut,normal) if real_valid else (sf,slow_cut,slow)
        if pic and info.get('runtimeVerified') and has_routes(info) and source_info['displayTap']==info['displayTap']:
            gap=pic.get('maxCaptureGapMs',float('inf')); speed=info['speed']
            resolved=(gap<40/speed and not pic['interiorUnreadable'] and pic['first']==0 and pic['last']==499)
            limit=f"{speed:g}x display, max gap {gap:g} ms"
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
        if hold and ref:
            low=hold['lowerMs']-ref['upperMs'];high=hold['upperMs']-ref['lowerMs']
            timing=f'{low:g}–{high:g} ms extra hold vs uncut; 40 ms stall threshold'
            if real_valid and normal['displayTap']==cut['displayTap']:
                status='FAIL' if low>=40 or f['marked'] else 'PASS' if high<40 and out['marked picture'][0]=='PASS' else 'not-measured'
        out['real-speed join']=(status,f"{timing}; sampled {f['distinct']}/374 distinct, {f['marked']} marked at 1x")
    if a:
        ct=a['tones']
        runtime=bool(normal.get('runtimeVerified'))
        marked_ok=runtime and all(abs(t['880']-5040)<=5 for t in ct)
        kept_ok=runtime and all(abs(t['440']-5480)<=5 and abs(t['660']-9480)<=5 for t in ct)
        matches=lambda tones,hz: '/'.join(f'{t[hz]:g}' for t in tones)
        peak='/'.join(map(str,a['capturePeakLSB']))
        out['sound control']=('PASS' if marked_ok else 'FAIL',f"880 Hz L/R {matches(ct,'880')} ms (expected 5040); 48 kHz stereo, floor {a['preRollRmsLSB']:g} LSB RMS, peak {peak} LSB; 5 ms at 0.10 FS")
        filtered=bool(b and cut.get('runtimeVerified') and has_routes(cut))
        if not marked_ok:
            out['marked sound']=('not-measured','Uncut rendered control failed marked-tone calibration')
        elif not filtered:
            out['marked sound']=('not-measured','No verified filtered render')
        else:
            vals=b['tones']; leak=any(t['880'] for t in vals); ended=not cut['endSignal'].startswith('fixture duration')
            out['marked sound']=('FAIL' if leak else 'PASS' if ended and all(t['660'] for t in vals) else 'not-measured',f"880 Hz L/R {matches(vals,'880')} ms at >=0.10 FS in 5 ms windows")
        if not kept_ok:
            out['kept tones']=('not-measured',f"Uncut 440/660 Hz L/R {matches(ct,'440')}, {matches(ct,'660')} ms (expected 5480, 9480)")
        elif not filtered:
            out['kept tones']=('not-measured','No verified filtered render')
        else:
            vals=b['tones']
            out['kept tones']=('PASS' if all(abs(t['440']-5480)<=5 and abs(t['660']-9480)<=5 for t in vals) else 'FAIL',f"440 Hz L/R {matches(vals,'440')} ms; 660 Hz {matches(vals,'660')} ms; 5 ms windows")
        if w and w['valid'] and filtered:
            pre=w['prefix']['differentSampleFrames'];post=w['suffix']['differentSampleFrames'];join=w['join20msEachSide']
            first=join['firstDifferentFrame']; where='' if first is None else f" at join samples {first-960}…{join['lastDifferentFrame']-960}"
            out['kept PCM at join']=('FAIL' if pre or post else 'PASS',f"{join['differentSampleFrames']}/1920 changed stereo frames{where}; {pre} prefix, {post} suffix; fixed offset, 1 LSB")
        elif w and not w['valid']:
            base=w.get('controlVsDecoded',{}); detail=f" ({base['differentSampleFrames']}/{base['comparedSampleFrames']} uncut stereo frames differ)" if base else ''
            out['kept PCM at join']=('not-measured',w['reason']+detail)
        else:
            out['kept PCM at join']=('not-measured','No verified filtered render or fixed-offset PCM comparison')
    ops=raw.get('controlOperations',{});seeks=ops.get('pausedSeeks',[])
    if seeks:
        n=sum(x['exact'] for x in seeks); obs=f"{n}/6 exact paused landings; samples, not travel"
        out['seek forward/back']=(('PASS' if n==6 else 'FAIL') if (real_valid or slow_valid) else 'not-measured',obs)
    elif ops.get('limits'):out['seek forward/back']=('not-measured',ops['limits'][0])
    if 'resumedFrame' in ops:out['persisted resume']=('not-measured',f"Stored {ops['bookmarkTicks']} filtered ticks; Resume sampled source frame {ops['resumedFrame']}, first frame unresolved")
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
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--keep',action='store_true',help='retain private synthetic data and credentials (never running services)');parser.add_argument('--json',action='store_true',help='print a single JSON object on stdout');parser.add_argument('--gpu',choices=('swangle','host'),default='swangle',help='declared emulator graphics mode');opts=parser.parse_args()
    if not __debug__:parser.error('Python assertions are required')
    start=time.monotonic(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); uid=secrets.token_hex(5)
    work=Path(tempfile.mkdtemp(prefix='cleanyfin-tv-'));sdk=Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk').resolve()
    output=HERE.parents[1]/'delivery/evidence/runs'/('tv-'+stamp+'-'+uid)
    summary={'startedUtc':stamp,'recording':'Synthetic fixture only; Android compositor display (host recorder fallback), isolated PipeWire sink monitor s16le stereo 48000 Hz'}
    raw=summary['observations']={};row=None;error=None;peak=0;over=threading.Event();done=threading.Event()
    def budget():
        nonlocal peak
        while not done.is_set():
            peak=max(peak,allocated(work))
            if peak>6_000_000_000:
                over.set();os.kill(os.getpid(),signal.SIGTERM);return
            done.wait(.75)
    def interrupt(sig,frame): raise RuntimeError('Scratch budget exceeded' if over.is_set() else 'Wall timeout' if sig==signal.SIGALRM else 'Run interrupted')
    for sig in (signal.SIGTERM,signal.SIGALRM,signal.SIGINT):signal.signal(sig,interrupt)
    signal.alarm(1200);resource.setrlimit(resource.RLIMIT_CORE,(0,0));watcher=threading.Thread(target=budget,daemon=True);watcher.start()
    try:
        with ExitStack() as stack, redirect_stdout(sys.stderr):
            # Every Docker child, including fixture-generator subprocesses, gets
            # an ownership label so an interrupted image build is collected too.
            docker=shutil.which('docker')
            if not docker:raise RuntimeError('Docker unavailable')
            wrappers=work/'bin';wrappers.mkdir();wrapper=wrappers/'docker';label='org.cleanyfin.tv-run='+uid
            wrapper.write_text('#!/bin/sh\ncase "$1" in run|create) verb=$1;shift;exec '+shlex.quote(docker)+' "$verb" --label '+shlex.quote(label)+' "$@";; esac\nexec '+shlex.quote(docker)+' "$@"\n');wrapper.chmod(0o700)
            oldpath=os.environ['PATH'];os.environ['PATH']=str(wrappers)+':'+oldpath
            def sweep():
                os.environ['PATH']=oldpath
                ids=run(docker,'ps','-aq','--filter','label='+label).decode().split()
                if ids:run(docker,'rm','-fv',*ids)
            stack.callback(sweep)
            if not (sdk/'emulator/emulator').is_file():raise RuntimeError('Android emulator SDK unavailable')
            check=run(sdk/'emulator/emulator','-accel-check').decode()
            if 'KVM' not in check or 'usable' not in check:raise RuntimeError('KVM not usable')
            apk=release(work);summary['apkSha256']=DIGEST
            summary['emulatorVersion']=(sdk/'emulator/source.properties').read_text().split('Pkg.Revision=')[-1].splitlines()[0].strip()
            def host_audio():
                return (run('pactl','get-default-sink').decode().strip(),sorted(line.split('\t')[1] for line in run('pactl','list','short','sinks').decode().splitlines()))
            before=host_audio()
            tv=TV(work,sdk,stack,opts.gpu);summary['declaredGpu']=opts.gpu
            tv.boot();summary['graphicsAdapter']=tv.graphicsAdapter
            print('Emulator graphics:',tv.graphicsAdapter or opts.gpu+' requested; renderer not reported',file=sys.stderr,flush=True)
            tv.adb('install','--no-streaming',apk,timeout=75)
            summary['androidFingerprint']=tv.adb('shell','getprop','ro.build.fingerprint').decode().strip();summary['audioInputFormat']=tv.call('pactl','list','short','sink-inputs').decode().strip().split('\t')[-1]
            lab=setup_lab(work,tv,stack);summary['pluginBuild']=lab.plugin_build;summary['fixtureSha256']=hashlib.sha256(lab.original).hexdigest()
            row=lab.stub_register()['rows'][0];row['controls']=[];row['status']='experimental'
            ui=Screen(tv);ui.setup(lab);raw['verifiedSettings']=ui.verified;raw['sessionIdentity']=lab.tv_identity
            print('Disposable TV enrollment:',json.dumps(lab.tv_identity),file=sys.stderr,flush=True)
            measure(tv,lab,ui,raw)
    except (Exception,KeyboardInterrupt) as exc:
        # Never serialize child command lines, queries, login codes or stderr.
        error=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__
    finally:
        signal.alarm(0);done.set();watcher.join(timeout=3);peak=max(peak,allocated(work));summary['scratchPeakBytesObserved']=peak
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
    if 'verifiedSlowSettings' in raw or not has_routes(raw.get('recordings',{}).get('filtered',{})):row=None
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



