#!/usr/bin/env python3
"""Synthetic TV lab; only a fresh cloned AVD in a loopback-only network namespace."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
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
    def __init__(self, work, sdk, stack):
        self.work, self.sdk, self.stack = work, sdk, stack
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
                    'AvdId':'lab', 'avd.ini.displayname':'lab'}.items():
            ini = re.sub(rf'(?m)^{re.escape(k)}=.*$', k+'='+v, ini)
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

    def adb(self, *args, **kw):
        return self.call(self.sdk/'platform-tools/adb','-P','5037','-s','emulator-5580',*args, **kw)

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
                        QT_QPA_PLATFORM_PLUGIN_PATH=str(libs/'qt/plugins/platforms'),
                        LD_LIBRARY_PATH=':'.join(map(str,[libs/'vulkan',libs/'gles_angle',libs,libs/'qt/lib'])))
        self.emu = self.spawn(self.prefix+[em/'qemu/linux-x86_64/qemu-system-x86_64','-avd','lab','-accel','on','-no-window',
                              '-no-snapshot','-no-boot-anim','-port','5580','-no-metrics','-gpu','swangle','-feature','-Vulkan',
                              '-audio','pa','-show-kernel','-memory','2048','-cores','4'], 'emulator')
        def ready():
            if self.emu.poll() is not None:
                raise RuntimeError('Emulator exited before Android boot completed')
            return self.adb('shell','getprop','sys.boot_completed', timeout=3).strip() == b'1'
        eventually(ready, 240, 'Android did not complete boot in 240 s')


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
    lab.wholphin['tuple'].update(appBuild=DIGEST,deviceMode='Google TV API 36 x86_64 emulator, swangle, software video vo-gpu Android GLES, stereo PCM')
    lab.wholphin['status']='experimental'
    lab.public_origin='http://10.0.2.2:18096'+kit.BASE
    lab.setup_pointer()
    cfg=lab.api('/System/Configuration',user=lab.admin)
    cfg.update(MinResumeDurationSeconds=1,MinResumePct=1,MaxResumePct=95)
    lab.api('/System/Configuration','POST',cfg,user=lab.admin)
    run('docker','exec','-d',lab.cid,'perl','-e',RELAY)
    sock=lab.work/'config/tv.sock'
    eventually(sock.is_socket,8,'Jellyfin Unix relay did not start')
    tv.spawn(tv.prefix+['socat','TCP4-LISTEN:18096,bind=127.0.0.1,reuseaddr,fork','UNIX-CONNECT:'+str(sock)],'relay')
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
        self.wait_label('Home')
        def sidebar():
            nodes=self.tree();f=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if f is not None and any((n.get('text') or n.get('content-desc')) in ('Home','Search','Favorites','Settings','Original') for n in f.iter()) and any(n.get('text')=='Settings' for n in nodes):return True
            self.key('DPAD_LEFT');return False
        eventually(sidebar,40,'D-pad did not enter the navigation drawer');self.dpad('Settings',cap=14)
        self.wait_label('Remember selected tabs');self.dpad('Advanced Settings')
        self.wait_label('Show Clock');self.dpad('ExoPlayer');self.wait_label('Prefer MPV');self.dpad('MPV',cap=5)
        self.wait_label('Edit mpv.conf'); self.key('BACK');self.dpad('Advanced Settings')
        # The choice dialog initially focuses option zero, regardless of selection.
        # Re-enter the page and verify the persisted summary and MPV-only editor.
        self.dpad('Edit mpv.conf');self.wait_label('Save')
        opts='hwdec=no\nvo=gpu\ngpu-context=android\nopengl-es=yes\nmsg-level=all=v'
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


def record(tv,lab,item,label):
    from contextlib import ExitStack
    path=tv.work/(label+'.webm'); audio=tv.work/(label+'.pcm')
    def console(*args):
        answer=tv.adb('emu','screenrecord',*args)
        if b'OK' not in answer or b'KO:' in answer:raise RuntimeError('Emulator display recorder refused '+args[0])
    # Earliest possible presentation is after the app intent; capture both
    # display and monitor first, including a noise-floor measurement.
    with ExitStack() as s:
        wav=s.enter_context(audio.open('wb')); err=s.enter_context((tv.work/(label+'-audio.log')).open('wb'))
        proc=sp.Popen(tv.prefix+['parec','--device=fixture.monitor','--format=s16le','--rate=48000','--channels=2','--raw','--latency-msec=20'],env=tv.env,stdout=wav,stderr=err)
        def stop():
            proc.terminate()
            try: proc.wait(timeout=5)
            except sp.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        s.callback(stop)
        eventually(lambda:audio.stat().st_size>=38400,12,'Private monitor did not yield 200 ms of PCM')
        console('start','--size','640x360','--time-limit','60','--fps','60',str(path));s.callback(console,'stop')
        tv.adb('logcat','-c')  # Only this private guest, before each playback.
        play(tv,lab,item)
        sess=eventually(lambda:now(lab,item),24,'Wholphin did not report playback of the selected item')
        if sess['PlayState'].get('PlayMethod')!='DirectPlay':raise RuntimeError('Wholphin did not report DirectPlay')
        pid=tv.adb('shell','pidof',APP).decode().strip()
        if not pid.isdecimal():raise RuntimeError('Wholphin playback PID unavailable')
        eventually(lambda:not now(lab,item),40,'Wholphin did not report end of playback')
        logs=tv.adb('logcat','-d','--pid='+pid,'-v','tag').decode(errors='replace')
        output=re.findall(r'\bcplayer,\s+(AO|VO):\s+\[([a-z0-9_-]+)\]',logs,re.I)
        if not ({('AO','audiotrack'),('VO','gpu')}<=set(output)):raise RuntimeError('No libmpv cplayer AO audiotrack and VO gpu pair in Wholphin logcat')
        print(f'{label}: Wholphin libmpv cplayer AO [audiotrack], VO [gpu]',file=sys.stderr)
    return path,audio


def handoff(lab):
    logs=run('docker','logs',lab.cid).decode(errors='replace')
    return [logs.count(t) for t in ('Fixture POST /Items/{pointer}/PlaybackInfo 200','Fixture GET /Cleanyfin/play/{S}/list.edl 200','Fixture GET /Cleanyfin/play/{S}/source -> /Videos/{original}/stream 206')]


def measure(tv,lab,ui,out):
    from tv_score import picture,audio,compare
    scorer=module('analyze-capture')
    out['milestone']='uncut real-speed playback and capture'
    tv.adb('shell','cmd','media_session','volume','--stream','3','--set','15')
    cv,ca=record(tv,lab,lab.item,'control');out['controlRuntime']={'libmpvAO':'audiotrack','libmpvVO':'gpu','playMethod':'DirectPlay'}
    out['milestone']='picture control';out['controlPicture']=picture(cv,scorer)
    if not out['controlPicture']['controlComplete']:raise RuntimeError('Uncut recording did not resolve all 500 frames in source order')
    out['milestone']='sound control';out['controlSound']=audio(ca,scorer)
    if verdicts(out,None)['sound control'][0]!='PASS':raise RuntimeError('Uncut monitor did not reproduce the three expected tone durations in stereo')
    out['milestone']='filtered real-speed playback and receipts';before=handoff(lab);fv,fa=record(tv,lab,lab.pointer,'filtered');out['grantRouteDeltas']=[b-a for a,b in zip(before,handoff(lab))]
    if min(out['grantRouteDeltas'])<1:raise RuntimeError('Missing real pointer POST, list GET or original range receipt')
    out['filteredPicture']=picture(fv,scorer);out['filteredSound']=audio(fa,scorer)
    ref=scorer.media(lab.work/'fixture/source.mp4','ffmpeg','-i','/lab','-map','0:a:0','-f','s16le','-ac','2','-ar','48000','pipe:1',readonly=True)
    out['waveform']=compare(ca,fa,ref,max(out[k]['preRollPeakLSB'] for k in ('controlSound','filteredSound')))
    controls(tv,lab,ui,out)


def controls(tv,lab,ui,out):
    """Exercise the player's own Jellyfin remote commands and visible resume choice."""
    from uuid import UUID
    scorer=module('analyze-capture'); receipt=out['controlOperations']={}
    play(tv,lab,lab.pointer); s=eventually(lambda:now(lab,lab.pointer),20,'Control playback did not start')
    def command(name,at=None):
        path='/Sessions/'+s['Id']+'/Playing/'+name
        if at is not None:path+='?seekPositionTicks='+str(at*10000)
        lab.api(path,'POST',user=lab.one)
    def shown():
        data=tv.adb('exec-out','screencap','-p',timeout=8)
        raw=run('ffmpeg','-v','error','-f','image2pipe','-i','pipe:0','-vf','scale=160:90:flags=area,format=gray','-frames:v','1','-threads:v','1','-f','rawvideo','pipe:1',input=data,timeout=5)
        return scorer.read_barcode(raw,500) if len(raw)==14400 else None
    command('Pause'); eventually(lambda:(now(lab,lab.pointer) or {}).get('PlayState',{}).get('IsPaused'),12,'Player did not acknowledge pause')
    # Paused seek destinations on both sides and exactly at the cut. Never use
    # server playhead ticks alone as evidence of what was actually presented.
    for ms in (7600,2400,8000,3600,5480,7600):
        target=ms//40+(126 if ms>=5480 else 0);command('Seek',ms)
        observed=[]
        def landed():
            n=shown()
            if n is not None:observed.append(n)
            return n==target
        try: eventually(landed,8,'No exact paused seek landing')
        finally:receipt.setdefault('pausedSeeks',[]).append(dict(filteredMs=ms,expectedFrame=target,observed=observed))
    command('Stop');eventually(lambda:not now(lab,lab.pointer),15,'Wholphin did not stop')
    def ticks():
        return lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
    receipt['bookmarkTicks']=eventually(ticks,15,'No persisted pointer bookmark')
    tv.adb('shell','am','force-stop',APP)
    tv.adb('shell','am','start','-n',APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.api('/System/Info/Public')['Id'])))
    matches=lambda:[n for n in ui.tree() if n.get('package')==APP and any('resume' in n.get(k,'').lower() for k in ('text','content-desc'))]
    n=eventually(matches,20,'No actual Resume choice after stopping Wholphin')[0]
    ui.activate(n.get('content-desc') or n.get('text')); receipt['resumedFrame']=eventually(shown,12,'Resume picture unreadable');receipt['resumeObservation']='One sample after actual Resume; not the first presented frame'


CLAIMS=('picture control','marked picture','kept picture at join','real-speed join','sound control','marked sound','kept tones','kept PCM at join','seek forward/back','persisted resume')

def verdicts(raw,reason):
    out={k:('not-measured',reason or 'No calibrated capture') for k in CLAIMS}
    c=raw.get('controlPicture',{});f=raw.get('filteredPicture',{});a=raw.get('controlSound',{});b=raw.get('filteredSound',{});w=raw.get('waveform',{})
    if c:
        ok=c['controlComplete'];out['picture control']=('PASS' if ok else 'FAIL',f"{c['distinct']}/500 distinct, {c['marked']}/126 marked; 25 fps source, at 1x")
        if not ok:
            for k in ('marked picture','kept picture at join','real-speed join'):out[k]=('not-measured','Uncut control did not witness all 500 frames in order')
        elif f and min(raw.get('grantRouteDeltas',[0]))>0:
            out['marked picture']=('FAIL' if f['marked'] else ('not-measured' if f['interiorUnreadable'] or not f['filteredComplete'] or f.get('maxCaptureGapMs',1000)>=40 else 'PASS'),f"{f['marked']} distinct marked; {f['interiorUnreadable']} unreadable inside clip, max capture gap {f.get('maxCaptureGapMs')} ms")
            resolved=(f.get('maxCaptureGapMs',float('inf'))<40 and not f['interiorUnreadable'] and f['first']==0 and f['last']==499)
            out['kept picture at join']=('PASS' if f['filteredComplete'] else 'FAIL' if resolved else 'not-measured',f"{374-len(f['missingKept'])}/374 kept; 136 then 263: {f['joinAdjacent']}; ordered barcode capture")
            hold=f.get('joinHoldMs',{}).get('136-263'); ref=c.get('joinHoldMs',{}).get('136-137');
            if hold is not None and ref is not None:out['real-speed join']=('not-measured',f'136 to 263 took {hold:g} ms; uncut 136 to 137 took {ref:g} ms; continuous-time stall below capture cadence unresolved')
    if a:
        ct=a['tones']; ok=all(abs(t['880']-5040)<=5 and abs(t['440']-5480)<=5 and abs(t['660']-9480)<=5 for t in ct)
        out['sound control']=('PASS' if ok else 'FAIL',f"880 Hz L/R {ct[0]['880']:g}/{ct[1]['880']:g} ms, expected 5040; 48 kHz stereo, 5 ms windows, peak >=0.10 FS")
        if not ok:
            for k in ('marked sound','kept tones','kept PCM at join'):out[k]=('not-measured','Uncut sound control did not reproduce the expected tone durations')
        elif b:
            vals=b['tones'];out['marked sound']=('FAIL' if any(t['880'] for t in vals) else 'PASS',f"880 Hz L/R {vals[0]['880']:g}/{vals[1]['880']:g} ms at >=0.10 FS in 5 ms windows")
            out['kept tones']=('PASS' if all(abs(t['440']-5480)<=5 and abs(t['660']-9480)<=5 for t in vals) else 'FAIL',f"440 Hz L/R {vals[0]['440']:g}/{vals[1]['440']:g} ms; 660 Hz {vals[0]['660']:g}/{vals[1]['660']:g} ms; 5 ms windows")
            if w:out['kept PCM at join']=(('PASS' if not w['prefix']['differentSampleFrames'] and not w['suffix']['differentSampleFrames'] else 'FAIL') if w['valid'] else 'not-measured',w.get('reason') or f"{w['join20msEachSide']['differentSampleFrames']} changed stereo sample frames in +/-20 ms; one fixed offset, 1 LSB")
    ops=raw.get('controlOperations',{});seeks=ops.get('pausedSeeks',[])
    if seeks:out['seek forward/back']=('PASS' if len(seeks)==6 and all(x['expectedFrame'] in x['observed'] for x in seeks) and c.get('controlComplete') else 'not-measured',f"{sum(x['expectedFrame'] in x['observed'] for x in seeks)}/6 exact paused landings witnessed; sampled destinations, not travel between them")
    if 'resumedFrame' in ops:out['persisted resume']=('not-measured',f"Stored {ops['bookmarkTicks']} filtered ticks, resume sampled source frame {ops['resumedFrame']}; first presented frame unresolved")
    return out


def allocated(root):
    total=0
    for path,dirs,files in os.walk(root):
        for n in files:
            try:total+=os.lstat(os.path.join(path,n)).st_blocks*512
            except FileNotFoundError:pass
    return total


def main():
    import resource,threading,shlex
    from datetime import datetime,timezone
    from contextlib import redirect_stdout
    import secrets
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--keep',action='store_true',help='retain private synthetic data and credentials (never running services)');parser.add_argument('--json',action='store_true',help='print a single JSON object on stdout');opts=parser.parse_args()
    if not __debug__:parser.error('Python assertions are required')
    start=time.monotonic(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); uid=secrets.token_hex(5)
    work=Path(tempfile.mkdtemp(prefix='cleanyfin-tv-'));sdk=Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk').resolve()
    output=HERE.parents[1]/'delivery/evidence/runs'/('tv-'+stamp+'-'+uid)
    summary={'startedUtc':stamp,'recording':'Synthetic fixture only; emulator display at requested 60 fps, isolated PipeWire sink monitor s16le stereo 48000 Hz'}
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
            tv=TV(work,sdk,stack);tv.boot();tv.adb('install','--no-streaming',apk,timeout=75)
            summary['androidFingerprint']=tv.adb('shell','getprop','ro.build.fingerprint').decode().strip();summary['audioInputFormat']=tv.call('pactl','list','short','sink-inputs').decode().strip().split('\t')[-1]
            lab=setup_lab(work,tv,stack);summary['pluginBuild']=lab.plugin_build;summary['fixtureSha256']=hashlib.sha256(lab.original).hexdigest()
            row=lab.stub_register()['rows'][0];row['controls']=[];row['status']='experimental'
            ui=Screen(tv);ui.setup(lab);raw['verifiedSettings']=ui.verified;measure(tv,lab,ui,raw)
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
    output.mkdir(parents=True,exist_ok=False);summary['evidenceDirectory']=str(output.relative_to(HERE.parents[1]));summary['candidateWritten']=row is not None
    if row is not None:
        row.update(date=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8],evidenceRef=summary['evidenceDirectory']+'/summary.json',observation='Synthetic emulator only. See control calibration, route receipts and limits. Never grants production eligibility.')
        if min(raw.get('grantRouteDeltas',[0]))>0: row['controls'].append('straight-play')
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



