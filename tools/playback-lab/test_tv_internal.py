"""No device, network or external process: all samples and files are synthetic."""
from contextlib import ExitStack, contextmanager
import copy
import errno
import importlib.util
import io
from pathlib import Path, PurePosixPath
import tempfile
import unittest
import wave

import tv_internal as tap
from tv_device import ADAPTERS

spec=importlib.util.spec_from_file_location('tv_harness_unit',Path(__file__).with_name('tv-harness.py'))
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)


def wav(rate=48000,channels=2,width=2):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setparams((channels,width,rate,0,'NONE',''));w.writeframes(b'\x13\0\x24\0'*100)
    return out.getvalue()


class InternalTests(unittest.TestCase):
    def test_format_is_observed_not_coerced(self):
        good=wav();self.assertEqual(tap.read_pcm(good),good[-400:])
        for invalid in (b'',good[:-1],wav(rate=44100),wav(channels=1),wav(width=4),good+b'X',good[:4]+b'\xff'*4+good[8:]):
            with self.subTest(),self.assertRaises(ValueError):tap.read_pcm(invalid)
        partial=bytearray(good);partial[40:44]=(399).to_bytes(4,'little')
        with self.assertRaises(ValueError):tap.read_pcm(partial)

    def test_observation_point_and_format_must_match(self):
        r={'expectedAudioDriver':'pcm'}
        h.app_runtime('cplayer, AO: [pcm] 48000Hz stereo 2ch s16',r);self.assertTrue(r['libmpvAO'].startswith('pcm '))
        for bad in ('[pcm] 48000Hz stereo 2ch float','[pcm] 44100Hz stereo 2ch s16','[audiotrack] 48000Hz stereo 2ch s16'):
            h.app_runtime('cplayer, AO: '+bad,r);self.assertNotIn('libmpvAO',r)
        r={};h.app_runtime('cplayer, AO: [pcm] 48000Hz stereo 2ch s16',r);self.assertNotIn('libmpvAO',r)
        h.app_runtime('cplayer, AO: [audiotrack] 48000Hz stereo 2ch float',r);self.assertIn('libmpvAO',r)

    def test_private_paths_and_ugreen_identity(self):
        p=tap.Paths('a'*24);self.assertTrue(p.wav.startswith('/sdcard/Android/data/'+tap.APP+'/files/'))
        self.assertNotEqual(p.video,tap.Paths('b'*24).video);self.assertEqual(ADAPTERS['2b89:5389'],'UGREEN 15389')
        with self.assertRaises(ValueError):tap.Paths('../outside')

    def test_interlock_refuses_failed_cleanup_or_camera_substitute(self):
        keys='wholphinAbsentBefore pathsVerifiedAbsentBeforeOptions pcmRemoved screenrecordRemoved labAppAndMpvOptionsRemoved'.split()
        row=dict(mode='device-internal',dryRun=True,device={'emulator':True},captureHarnessSha256='x',restored=True,
                 privateAdbExited=True,privateNamespaceExited=True,emulatorExited=True,scratchRemoved=True,ownedContainersRemaining=0,
                 observations={'controls':{k:{'tapVerified':True} for k in ('positive','held-out')}},internalRestore=dict.fromkeys(keys,True))
        self.assertTrue(tap.emulator_proof(row,'x'));self.assertFalse(tap.emulator_proof(row,'z'))
        for key in ('restored','scratchRemoved','privateNamespaceExited','privateAdbExited'):
            self.assertFalse(tap.emulator_proof(dict(row,**{key:False}),'x'))
        for key in keys:
            miss=copy.deepcopy(row);miss['internalRestore'][key]=False;self.assertFalse(tap.emulator_proof(miss,'x'))
        row['observations']['controls']={'camera':{'tapVerified':True}};self.assertFalse(tap.emulator_proof(row,'x'))

    def test_fresh_and_family_rehearsals_are_not_interchangeable(self):
        restore=dict(wholphinAbsentBefore=False,pathsVerifiedAbsentBeforeOptions=True,pcmRemoved=True,screenrecordRemoved=True)
        row=dict(mode='device-internal',dryRun=True,device={'emulator':True},captureHarnessSha256='x',restored=True,
                 privateAdbExited=True,privateNamespaceExited=True,emulatorExited=True,scratchRemoved=True,ownedContainersRemaining=0,
                 observations={'controls':{k:{'tapVerified':True} for k in ('positive','held-out')}},internalRestore=restore,
                 familyUpdate=dict(visibleStateRestored=True,inPlaceUpdateReported=True,syntheticBaselineSeeded=True,
                                   beforeUI={'profile':'one'},afterUpdate={'profile':'one'},afterRestore={'profile':'one'}))
        self.assertTrue(tap.emulator_proof(row,'x',update=True));self.assertFalse(tap.emulator_proof(row,'x'))
        for key in ('syntheticBaselineSeeded','visibleStateRestored','afterUpdate','afterRestore'):
            miss=copy.deepcopy(row);del miss['familyUpdate'][key];self.assertFalse(tap.emulator_proof(miss,'x',update=True))

    def test_restore_preserves_independent_failure_results(self):
        from unittest.mock import Mock
        tv=Mock();tv.internal_state={'pathsVerifiedAbsentBeforeOptions':True};tv.family.receipt={}
        tv.family.finish.side_effect=ValueError('profile did not match')
        tv.capture.remove.side_effect=[OSError('WAV unreadable'),None]
        h.restore_family_ui(tv)
        tv.family.finish.assert_called_once();self.assertEqual(tv.capture.remove.call_count,2)
        self.assertIs(tv.family.receipt['visibleStateRestored'],False);self.assertIs(tv.internal_state['pcmRemoved'],False)
        self.assertIs(tv.internal_state['screenrecordRemoved'],True)

    def test_one_endpoint_and_timeout_is_inconclusive(self):
        calls=[]
        def refused(addr,timeout):calls.append((addr,timeout));raise ConnectionRefusedError(errno.ECONNREFUSED,'refused')
        self.assertEqual(tap.closed_endpoint('192.168.5.9:42199',refused),'refused');self.assertEqual(calls,[(('192.168.5.9',42199),2)])
        def silent(*a,**kw):raise TimeoutError
        self.assertEqual(tap.closed_endpoint('192.168.5.9:42199',silent),'unverified')
        self.assertEqual(tap.closed_endpoint('192.168.5.9:42199',lambda *a,**kw:ExitStack()),'open')
        with self.assertRaises(ValueError):tap.closed_endpoint('example.invalid:80',refused)

    def test_cleanup_on_success_bad_wav_and_playback_exception(self):
        for mode in ('good','bad','interrupted'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as root:
                files={};stages=[];paths=tap.Paths('a'*24)
                @contextmanager
                def witness(tv,label,limit,out):
                    stages.append('start');out['picture']={'outcome':'witness only'}
                    try:yield
                    finally:stages.append('stop')
                class TV:
                    work=Path(root)
                    def adb(self,*args,timeout=None):
                        if args[:3]==('shell','rm','-f'):files.pop(args[3],None);return b''
                        if args[:3]==('shell','ls','-1a'):return '\n'.join(PurePosixPath(k).name for k in files if str(PurePosixPath(k).parent)==args[3]).encode()
                        if args[:4]==('shell','stat','-c','%s'):return str(len(files[args[-1]])).encode()
                        if args[0]=='pull':Path(args[2]).write_bytes(files[args[1]]);return b''
                        raise AssertionError(args)
                class H:
                    pcm_arrival=staticmethod(h.pcm_arrival)
                    @staticmethod
                    def capture_display(tv,s,label,limit,info,device_path,android_only):
                        assert android_only;files[device_path]=b'X';s.callback(files.pop,device_path);return tv.work/'picture'
                info={};cap=tap.InternalCapture(paths,witness=witness);tv=TV()
                try:
                    with ExitStack() as s:
                        _,audio=cap.begin(H,tv,s,'probe',30,info,sound=True,display=True)
                        files[paths.wav]=wav() if mode!='bad' else bytes(200)
                        if mode=='interrupted':raise InterruptedError
                except InterruptedError:pass
                self.assertFalse(files);self.assertEqual(stages,['start','stop']);self.assertTrue(info['pcmRemoved'])
                self.assertFalse((tv.work/'probe.wav').exists());self.assertEqual(audio.exists(),mode!='bad')
                self.assertEqual(info.get('arrivingAudio',{}).get('ok',False),mode!='bad')
