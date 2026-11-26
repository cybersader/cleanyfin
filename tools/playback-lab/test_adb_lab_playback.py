"""Capability boundary, with no real key, endpoint, server, emulator or device."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock,patch

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

    def test_metadata_distinguishes_nonexistent_from_unreadable(self):
        import subprocess as sp
        self.lab.server.poll.return_value=None;self.lab.client=['adb'];self.lab.target=['-s','synthetic']
        p=Playback(self.lab,self.work,guest=False,family=True);p.qualify('armeabi-v7a');p.bind_capture(self.paths)
        for suffix,missing in [(b'No such file or directory',True),(b'Permission denied',False),(b'',False)]:
            stderr=b'stat: '+self.paths.wav.encode()+b': '+suffix
            with patch('adb_lab_playback.sp.run',side_effect=sp.CalledProcessError(1,['private'],stderr=stderr)):
                if missing:self.assertEqual(p.capture_kind(self.paths.wav),'missing')
                else:
                    with self.assertRaises(LabError):p.capture_kind(self.paths.wav)
        with self.assertRaises(LabError):p.capture_kind('/sdcard/private')
        with self.assertRaises(LabError):p.run('shell','ls','-1a',str(Path(self.paths.wav).parent))

    def test_failure_names_only_fixed_stock_reads(self):
        import subprocess as sp
        self.lab.server.poll.return_value=None;self.lab.client=['adb'];self.lab.target=['-s','synthetic']
        p=Playback(self.lab,self.work,guest=False,family=True);p.qualify('armeabi-v7a')
        for args,expected in [(('shell','wm','size'),'shell wm size'),(('shell','input','text','hwdec=no'),None)]:
            with patch('adb_lab_playback.sp.run',side_effect=sp.CalledProcessError(1,['private'])),self.assertRaises(sp.CalledProcessError) as ctx:
                p.run(*args)
            self.assertEqual(getattr(ctx.exception,'playback_read',None),expected)

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

    def test_fresh_emulator_install_requires_complete_package_inventory(self):
        from hashlib import sha256
        app=self.work/'synthetic.apk';app.write_bytes(b'fixture')
        for rows,valid in ((b'',False),(b'error',False),(b'package:android\nunknown',False),
                           (b'package:android\npackage:'+APP.encode(),False),(b'package:android\npackage:other.app',True)):
            p=Playback(self.lab,self.work,guest=True,family=False);p.qualify('x86_64');p._call=Mock(side_effect=[rows,b'Success'])
            with patch.dict('adb_lab_playback.ASSETS',{'x86_64':('synthetic.apk',sha256(b'fixture').hexdigest())}):
                if valid:p._install(('install','--no-streaming',str(app)),30)
                else:
                    with self.assertRaises(LabError):p._install(('install','--no-streaming',str(app)),30)
            self.assertEqual(p.owned_install,valid);self.assertEqual(p._call.call_count,2 if valid else 1)

    def test_public_apk_sync_never_exposes_private_data_or_accepts_partial_files(self):
        src='/data/app/~~ab==/'+APP+'-ab==/base.apk';dst=self.work/'public.apk'
        self.lab.server.poll.return_value=None;self.lab.bin=['bwrap','--','/adb'];self.lab.target=['-s','synthetic']
        self.p.public={src};self.p._call=Mock(return_value=b'4')
        for payload in (b'12',b'1234'):
            def sync(cmd,**kw):
                self.assertEqual(cmd[-3:],['pull',src,'/transfer/base.apk']);temp=Path(cmd[cmd.index('--bind')+1]);self.assertEqual(list(temp.iterdir()),[])
                (temp/'base.apk').write_bytes(payload)
            with patch('adb_lab_playback.sp.run',side_effect=sync):
                if len(payload)==4:self.p.run('pull',src,dst);self.assertEqual(dst.read_bytes(),payload);dst.unlink()
                else:
                    with self.assertRaises(LabError):self.p.run('pull',src,dst)
                    self.assertFalse(dst.exists())
        for path in ('/data/user/0/'+APP+'/settings','/data/app/else/base.apk'):
            with self.assertRaises(LabError):self.p.run('pull',path,dst)

    def test_seed_old_version_uses_guest_inventory_but_cannot_become_reinstall(self):
        from hashlib import sha256
        old=self.work/'v1.0.9'/'Wholphin-x86_64.apk';old.parent.mkdir();old.write_bytes(b'old')
        p=Playback(self.lab,self.work,guest=True,family=True);p.qualify('x86_64');p._call=Mock(side_effect=[b'package:android',b'Success'])
        with patch('adb_lab_playback.OLD_SHA',sha256(b'old').hexdigest()):
            p._install(('install','--no-streaming',str(old)),30)
            self.assertEqual(p._call.call_args_list[0].args[0],('shell','pm','list','packages'))
            with self.assertRaises(LabError):p._install(('install','-r','--no-streaming',str(old)),30)
            self.assertFalse(p.owned_install)
