"""Synthetic transaction and failure cases; no ADB process or real app data."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tv_update import APP,Build,Update,Visible,OFFICIAL_CERT,TARGET,check_upgrade,installed_build

OLD=Build(APP,'1.0.9-0-g2d87d526',60,OFFICIAL_CERT)
NEW=Build(APP,TARGET,63,OFFICIAL_CERT)
BASE=Visible(('A','B'),'B','synthetic','MPV','hwdec=no')


class FamilyProtection(unittest.TestCase):
    def test_exact_update_scope_requires_flag_versions_and_matching_signer(self):
        check_upgrade(OLD,NEW,authorized=True)
        for before,new,flag in ((OLD,NEW,False),(NEW,OLD,True),(OLD,replace(NEW,code=59),True),
                                (replace(OLD,certificate='0'*64),NEW,True),(OLD,replace(NEW,package='elsewhere'),True),
                                (OLD,replace(NEW,version='1.2.0'),True)):
            with self.subTest(),self.assertRaises(ValueError):check_upgrade(before,new,authorized=flag)

    def test_family_flag_does_not_authorize_another_onn_or_a_real_rehearsal(self):
        from tv_update import check_update_target
        box=dict(emulator=False,device='jarvis2',sdk='34',abi='armeabi-v7a',abiList=['armeabi-v7a','armeabi'])
        guest=dict(emulator=True,abi='x86_64');check_update_target(box,rehearsal=False);check_update_target(guest,rehearsal=True)
        for target,rehearsal in ((box,True),(guest,False),(dict(box,device='other'),False),(dict(box,abi='arm64-v8a'),False)):
            with self.subTest(),self.assertRaises(ValueError):check_update_target(target,rehearsal=rehearsal)

    def test_incomplete_or_unrestorable_state_cannot_arm_update(self):
        for bad in (replace(BASE,servers=('A','A')),replace(BASE,active='missing'),replace(BASE,user=''),
                    replace(BASE,backend='unknown'),replace(BASE,options='unknown option with spaces')):
            with self.assertRaises(ValueError):bad.validate()
        self.assertNotEqual(BASE.proof(b'a'),BASE.proof(b'b'));self.assertNotIn('synthetic',str(BASE.proof(b'a')))

    def test_no_app_data_paths_may_be_pulled_as_package_code(self):
        tv=Mock();tv.adb.return_value=b'package:/data/user/0/private/base.apk\n'
        with tempfile.TemporaryDirectory() as root,self.assertRaises(ValueError):installed_build(tv,Path(root),None,None)
        self.assertEqual(tv.adb.call_count,1)

    def test_only_in_place_install_then_prove_state_before_lab_and_after_restore(self):
        tv=Mock();tv.adb.return_value=b'Performing Streamed Install\nSuccess\n';ui=Mock();ui.snapshot.return_value=BASE
        receipt={};tx=Update(tv,ui,None,None,None,receipt)
        manifest={'wholphinVersion':OLD.version,'packages':{APP:'60'}}
        with patch('tv_update.installed_build',side_effect=[OLD,NEW,NEW]),patch.object(Build,'inspect',return_value=NEW):
            tx.begin('test.apk',manifest,authorized=True);tx.lab_added=True;tx.finish()
        tv.adb.assert_called_once_with('install','-r','--no-streaming','test.apk',timeout=240)
        ui.restore.assert_called_once_with(BASE);self.assertTrue(receipt['visibleStateRestored'])
        self.assertEqual(receipt['beforeUI'],receipt['afterUpdate']);self.assertEqual(receipt['beforeUI'],receipt['afterRestore'])

    def test_signin_changed_after_upgrade_refuses_testing_without_wipe_fallback(self):
        tv=Mock();tv.adb.return_value=b'Success\n';ui=Mock();ui.snapshot.side_effect=[BASE,replace(BASE,user='changed')]
        receipt={};tx=Update(tv,ui,None,None,None,receipt)
        with patch('tv_update.installed_build',side_effect=[OLD,NEW]),patch.object(Build,'inspect',return_value=NEW),self.assertRaises(ValueError):
            tx.begin('test.apk',{'wholphinVersion':OLD.version,'packages':{APP:'60'}},authorized=True)
        self.assertFalse(tx.lab_added);self.assertNotIn('visibleStateRestored',receipt);self.assertEqual(tv.adb.call_count,1);ui.restore.assert_not_called()

    def test_unreadable_baseline_never_attempts_install(self):
        tv=Mock();ui=Mock();ui.snapshot.side_effect=ValueError('incomplete');tx=Update(tv,ui,None,None,None,{})
        with patch('tv_update.installed_build',return_value=OLD),patch.object(Build,'inspect',return_value=NEW),self.assertRaises(ValueError):
            tx.begin('test.apk',{'wholphinVersion':OLD.version,'packages':{APP:'60'}},authorized=True)
        tv.adb.assert_not_called()

    def test_profile_name_and_server_confirmed_independently_of_avatar_initial(self):
        from tv_family_ui import identify_profile
        for header in (['one','House'],['O','one','House'],['H','House','one']):
            self.assertEqual(identify_profile(header,['Select User','House','O','one','Add User']),('House','one'))
        for header,page in ((['one','House','unrelated'],['Select User','House','one']),(['O','House','one'],['Select User','Else','one']),
                            (['one','House'],['Select User','House','two'])):
            with self.assertRaises(ValueError):identify_profile(header,page)
