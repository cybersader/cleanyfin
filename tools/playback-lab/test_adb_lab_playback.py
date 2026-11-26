"""Capability boundary, with no real key, endpoint, server, emulator or device."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from adb_lab import LabError
from adb_lab_playback import ACT,APP,Playback
from tv_internal import Paths

class PlaybackScope(TestCase):
    def setUp(self):
        root=TemporaryDirectory();self.addCleanup(root.cleanup);self.work=Path(root.name)
        self.lab=Mock();self.lab.connected=True;self.p=Playback(self.lab,self.work,guest=False,family=True)
        self.p.qualify('armeabi-v7a');self.p._call=Mock(return_value=b'')
        self.paths=Paths('a'*24);self.p.bind_capture(self.paths)

    def test_family_scope_forbids_destructive_or_unrelated_commands(self):
        for argv in [('shell','pm','clear',APP),('uninstall',APP),('root',),('shell','sh','-c','id'),
                     ('exec-out','cat','/data/data/'+APP+'/files/private'),('reverse','tcp:80','tcp:80'),
                     ('reverse','--no-rebind','tcp:18097','tcp:18097'),('shell','input','text','ok;id'),
                     ('shell','input','text','http://outside.test'),('shell','input','text','ao-pcm-file=/sdcard/other.wav'),
                     ('shell','am','start','-n','other/.MainActivity'),('shell','kill','-INT','1'),
                     ('shell','rm','-f','/data/local/tmp/other'),('logcat','-c')]:
            with self.subTest(argv=argv),self.assertRaises(LabError):self.p.run(*argv)
        self.p._call.assert_not_called()

    def test_stock_ui_enters_only_lab_values_or_the_verified_original(self):
        from tv_update import Visible
        for text in ('http://127.0.0.1:18096/labbase','ao=pcm','ao-pcm-file='+self.paths.wav):
            self.p.run('shell','input','text',text)
        with self.assertRaises(LabError):self.p.run('shell','input','text','speed=1')
        self.p.bind_original_options(Visible(('one',),'one','user','MPV','speed=1'))
        self.p.run('shell','input','text','speed=1')
        with self.assertRaises(LabError):self.p.run('shell','input','text','http://127.0.0.1:18097/labbase')

    def test_owned_files_only_and_no_destructive_fallback(self):
        xml=self.p.ui_path();self.p.run('shell','uiautomator','dump',xml);self.p.run('exec-out','cat',xml)
        self.p.run('shell','rm','-f',xml)
        with self.assertRaises(LabError):self.p.run('exec-out','cat',xml)
        self.p.run('shell','rm','-f',self.paths.video);self.p.run('shell','am','force-stop',APP)
        self.p.owned_install=True
        with self.assertRaises(LabError):self.p.run('uninstall',APP)
        self.p.family=False;self.p.run('uninstall',APP)
        self.p.owned_install=False
        with self.assertRaises(LabError):self.p.run('uninstall',APP)

    def test_reverse_must_match_ports_and_removal_is_owned(self):
        self.p.run('reverse','--no-rebind','tcp:18096','tcp:18096');self.p.run('reverse','--remove','tcp:18096')
        with self.assertRaises(LabError):self.p.run('reverse','--remove','tcp:18096')
        with self.assertRaises(LabError):self.p.run('reverse','--no-rebind','tcp:18096','tcp:5037')

    def test_capture_stop_checks_actual_process_and_owned_path(self):
        for cmd in (b'sh\0',b'screenrecord\0other.mp4\0'):
            self.p._call.reset_mock();self.p._call.return_value=cmd
            with self.assertRaises(LabError):self.p.stop_recorder(self.paths.video,'14')
            self.assertEqual(self.p._call.call_count,1)
        self.p._call.return_value=b'/system/bin/screenrecord\0--size\0' + b'320x180\0'+self.paths.video.encode()+b'\0';self.p.stop_recorder(self.paths.video,'14')
        self.p._call.assert_called_with(('shell','kill','-INT','14'))

    def test_identity_before_any_playback_mutation(self):
        p=Playback(self.lab,self.work,guest=True,family=False);p._call=Mock(return_value=b'')
        p.run('shell','wm','size')
        with self.assertRaises(LabError):p.run('shell','am','start','-n',ACT)
        with self.assertRaises(LabError):p.qualify('armeabi-v7a')
        p.qualify('x86_64');p.run('shell','am','start','-n',ACT)
