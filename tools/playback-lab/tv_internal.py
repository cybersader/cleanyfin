"""Synthetic-only Android composition and decoded mpv output, not HDMI/AudioTrack.

The two taps do not share a clock. Do not infer A/V sync or sound-device timing
from a WAV file. No protected-surface fallback, root, run-as or storage grants.
"""
import errno
import hashlib
import json
import subprocess
import io
import re
import socket
import struct
import wave
from pathlib import PurePosixPath

from tv_device import APP, lan_address

LABEL = 'Android screenrecord and libmpv ao-pcm stereo s16le 48000Hz'
MAX_WAV = 12 * 1024 * 1024


class Paths:
    def __init__(self, token):
        if not re.fullmatch('[0-9a-f]{24}',token):raise ValueError('invalid capture nonce')
        leaf='cleanyfin-'+token
        self.wav=f'/sdcard/Android/data/{APP}/files/{leaf}.wav'
        self.video=f'/data/local/tmp/{leaf}.mp4'

    @property
    def options(self):
        return '\n'.join(('ao=pcm','ao-pcm-waveheader=yes','ao-pcm-file='+self.wav,
                          'audio-format=s16','audio-channels=stereo','audio-samplerate=48000'))


def read_pcm(blob):
    """Accept finalized integer WAV only; never repair a short write or resample it."""
    if not 44<=len(blob)<=MAX_WAV or blob[:4]!=b'RIFF' or blob[8:12]!=b'WAVE' or int.from_bytes(blob[4:8],'little')!=len(blob)-8:
        raise ValueError('missing, unfinished or oversized RIFF/WAVE')
    # Validate explicit data chunk length too (wave.getnframes rounds down).
    i=12;sizes=[]
    while i+8<=len(blob):
        n=int.from_bytes(blob[i+4:i+8],'little')
        if blob[i:i+4]==b'data':sizes.append(n)
        i+=8+n+(n%2)
    if i!=len(blob) or len(sizes)!=1 or not sizes[0] or sizes[0]%4:raise ValueError('invalid stereo data chunk')
    try:
        with wave.open(io.BytesIO(blob)) as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(2,2,48000,'NONE'):
                raise ValueError('mpv WAV is not signed 16-bit 48 kHz stereo PCM')
            data=w.readframes(w.getnframes())
    except (EOFError,struct.error,wave.Error) as exc:raise ValueError('mpv WAV header unsupported') from exc
    if len(data)!=sizes[0]:raise ValueError('truncated PCM')
    return data


def closed_endpoint(address, connect=socket.create_connection):
    """One probe, only the confirmed endpoint. Silence/unreachable is NOT proof."""
    ip,port=lan_address(address,'Debugging address').split(':')
    try:
        with connect((ip,int(port)),timeout=2):return 'open'
    except OSError as exc:
        return 'refused' if exc.errno==errno.ECONNREFUSED else 'unverified'


class InternalCapture:
    label=LABEL

    def __init__(self, paths, *, witness=None):
        # Future optional physical-camera recording: witness(tv,label,limit,receipt)
        # returns a context manager. It owns its recorder and stops on errors too.
        # No witness is installed by default, and no witness value enters the gate.
        self.paths,self.witness=paths,witness

    def verify_missing(self,tv,path):
        # ls of the containing directory must succeed: permission denied is not absence.
        p=PurePosixPath(path)
        entries=tv.adb('shell','ls','-1a',str(p.parent),timeout=5).decode().splitlines()
        if p.name in entries:raise RuntimeError('owned synthetic recording remains on the device')

    def remove(self,tv,path):
        tv.adb('shell','rm','-f',path,timeout=5);self.verify_missing(tv,path)

    def begin(self,h,tv,s,label,limit,info,*,sound,display):
        if not sound or not display:raise ValueError('internal taps must run together')
        if self.witness is not None:s.enter_context(self.witness(tv,label,limit,info.setdefault('supplementalWitness',{})))
        remote=self.paths.wav
        # Setup has already shown this random app-specific filename was unused.
        # A persisted-bookmark setup may itself have produced PCM in this slot.
        self.remove(tv,remote)
        self.verify_missing(tv,self.paths.video)
        audio=tv.work/(label+'.pcm');audio.unlink(missing_ok=True)
        video=h.capture_display(tv,s,label,limit,info,device_path=self.paths.video,android_only=True)
        def finish():
            temp=audio.with_suffix('.wav');info['pcmRemoved']=False
            try:
                n=int(tv.adb('shell','stat','-c','%s',remote,timeout=5))
                if not 44<=n<=MAX_WAV:raise ValueError('decoded PCM file missing or outside capture budget')
                tv.adb('pull',remote,temp,timeout=15)
                if temp.stat().st_size!=n:raise ValueError('decoded PCM transfer incomplete')
                audio.write_bytes(read_pcm(temp.read_bytes()))
                h.pcm_arrival(audio,info,'libmpv ao=pcm WAV, signed 16-bit 48000 Hz stereo; no sound-device clock',True)
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                info['pcmFailure']=str(exc) if isinstance(exc,ValueError) else 'could not read a finalized WAV from app-specific storage'
            finally:
                temp.unlink(missing_ok=True);self.remove(tv,remote);info['pcmRemoved']=True
        s.callback(finish)
        info.update(audioTap='libmpv ao=pcm file before the sound device; not AudioTrack, HDMI or passthrough',
                    expectedAudioDriver='pcm')
        return video,audio


def harness_digest(here):
    h=hashlib.sha256()
    for name in ('tv-harness.py','tv_internal.py','tv_r24.py','tv_device.py','tv_score.py','tv_pcm.py','tv_family_ui.py','tv_update.py'):
        h.update((here/name).read_bytes());h.update(b'\0')
    return h.hexdigest()


def emulator_proof(row,digest,*,update=False):
    """An operator interlock, not an eligibility grant. Controls + cleanup, not a filename."""
    obs=row.get('observations',{});controls=obs.get('controls',{});restore=row.get('internalRestore',{})
    family=row.get('familyUpdate',{})
    if update:
        if not (family.get('visibleStateRestored') is True and family.get('inPlaceUpdateReported') is True and
                family.get('beforeUI') and family['beforeUI']==family.get('afterUpdate')==family.get('afterRestore') and
                family.get('syntheticBaselineSeeded') is True and restore.get('wholphinAbsentBefore') is False):return False
    elif not (restore.get('wholphinAbsentBefore') is True and restore.get('labAppAndMpvOptionsRemoved') is True):return False
    return (row.get('mode')=='device-internal' and row.get('dryRun') is True and row.get('device',{}).get('emulator') is True
            and row.get('captureHarnessSha256')==digest and row.get('restored') is True and row.get('scratchRemoved') is True
            and row.get('privateNamespaceExited') is True and row.get('emulatorExited') is True and row.get('ownedContainersRemaining')==0
            and all(restore.get(k) is True for k in ('pathsVerifiedAbsentBeforeOptions','pcmRemoved','screenrecordRemoved'))
            and all(controls.get(k,{}).get('tapVerified') is True for k in ('positive','held-out')))


def prior_dry_run(runs,digest,*,update=False):
    for path in sorted(runs.glob('tv-*/summary.json'),reverse=True):
        try:
            if emulator_proof(json.loads(path.read_bytes()),digest,update=update):return path.parent.name
        except (ValueError,OSError,AttributeError):continue
    return None
