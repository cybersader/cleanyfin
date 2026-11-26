"""Synthetic transaction and failure cases; no ADB process or real app data."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tv_update import APP,Build,Update,Profile,PlayerSettings,OFFICIAL_CERT,TARGET,check_upgrade,installed_build

OLD=Build(APP,'1.0.9-0-g2d87d526',60,OFFICIAL_CERT)
NEW=Build(APP,TARGET,63,OFFICIAL_CERT)
BASE=Profile(('A','B'),'B','synthetic')


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
                    PlayerSettings('unknown',''),PlayerSettings('MPV','unknown option with spaces')):
            with self.assertRaises(ValueError):bad.validate()
        self.assertNotEqual(BASE.proof(b'a'),BASE.proof(b'b'));self.assertNotIn('synthetic',str(BASE.proof(b'a')))

    def test_no_app_data_paths_may_be_pulled_as_package_code(self):
        tv=Mock();tv.adb.return_value=b'package:/data/user/0/private/base.apk\n'
        with tempfile.TemporaryDirectory() as root,self.assertRaises(ValueError):installed_build(tv,Path(root),None,None)
        self.assertEqual(tv.adb.call_count,1)

    def test_only_in_place_install_then_prove_state_before_lab_and_after_restore(self):
        tv=Mock();tv.adb.return_value=b'Performing Streamed Install\nSuccess\n';ui=Mock();ui.snapshot.return_value=BASE;ui.inventory.return_value=BASE.servers
        receipt={};tx=Update(tv,ui,None,None,None,receipt)
        manifest={'wholphinVersion':OLD.version,'packages':{APP:'60'}}
        with patch('tv_update.installed_build',side_effect=[OLD,NEW,NEW]),patch.object(Build,'inspect',return_value=NEW):
            tx.begin('test.apk',manifest,authorized=True);tx.lab_added=True;tx.finish()
        tv.adb.assert_called_once_with('install','-r','--no-streaming','test.apk',timeout=240)
        ui.restore.assert_called_once_with(BASE,None,None);self.assertTrue(receipt['visibleStateRestored'])
        self.assertEqual(receipt['beforeUI']['servers'],receipt['afterUpdate']['servers']);self.assertEqual(receipt['afterUpdate'],receipt['afterRestore'])
        ui.snapshot.assert_called_once();self.assertEqual(ui.inventory.call_count,2);self.assertIn('re-select',receipt['expectedDifference'])

    def test_missing_server_refuses_testing_without_wipe_fallback(self):
        tv=Mock();tv.adb.return_value=b'Success\n';ui=Mock();ui.snapshot.return_value=BASE;ui.inventory.return_value=('A',)
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

    def test_original_profile_reentry_refuses_wrong_server_or_user(self):
        from tv_family_ui import FamilyUI
        for words,okay in ((['Select User','A','person'],True),(['Select User','B','person'],False),
                            (['Select User','A','person','person'],False),(['Select User','A','Add User'],False)):
            import xml.etree.ElementTree as ET
            nodes=[ET.Element('node',{'package':APP,'text':w,'bounds':'[0,105][60,120]'}) for w in words]
            card=ET.Element('node',{'package':APP,'focusable':'true','focused':'true','clickable':'true','bounds':'[0,0][100,100]'})
            ui=Mock();ui.tree.return_value=[*nodes,card];ui.home_drawer.return_value=True
            family=FamilyUI(ui);family.server_entries=Mock()
            if okay:family.select_known_profile('A','person');self.assertEqual(ui.key.call_args.args,('DPAD_CENTER',))
            else:
                with self.assertRaises(ValueError):family.select_known_profile('A','person')
                self.assertNotIn(('DPAD_CENTER',),[c.args for c in ui.key.call_args_list])
            family.server_entries.assert_called_once_with('A')

    def test_complete_non_scrolling_picker_can_put_add_first(self):
        import xml.etree.ElementTree as ET
        from tv_family_ui import picker_cards
        def card(*values):
            n=ET.Element('node',{'package':APP,'focusable':'true','clickable':'true'})
            for v in values:ET.SubElement(n,'node',{'package':APP,'text':v})
            return n
        title=ET.Element('node',{'package':APP,'text':'Select Server'});a=card('Add Server');b=card('S','Server','http://local');c=card('Other')
        for items in ([a,b,c],[b,c,a]):self.assertEqual(set(picker_cards([title,*items])),{'Server','Other'})
        for items in ([b,c],[a,b,b],[a,b,card('unrelated','labels')],[a,b,ET.Element('node',{'package':APP,'scrollable':'true'})]):
            with self.assertRaises(ValueError):picker_cards([title,*items])

    def test_initial_only_server_card_needs_one_matching_visible_caption(self):
        import xml.etree.ElementTree as ET
        from tv_family_ui import picker_cards
        def node(**kw):return ET.Element('node',dict(package=APP,**kw))
        title=node(text='Select Server');card=node(focusable='true',bounds='[100,100][250,250]');card.append(node(text='S'))
        add=node(focusable='true',**{'content-desc':'Add Server'});caption=node(text='Server One',bounds='[114,257][235,276]')
        self.assertEqual(picker_cards([title,add,card,caption]),{'Server One':card})
        for more in ([],[caption,caption],[node(text='Server One',bounds='[300,257][420,276]')]):
            with self.assertRaises(ValueError):picker_cards([title,add,card,*more])

    def test_private_label_record_contains_no_settings_or_credentials(self):
        import json,stat
        from tv_update import private_labels
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'records';path=Path(private_labels(BASE,root))
            self.assertEqual(json.loads(path.read_bytes()),{'serverLabel':'B','userLabel':'synthetic'})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600);root.chmod(0o755)
            with self.assertRaises(ValueError):private_labels(BASE,root)

    def test_lab_baseline_from_active_test_profile_not_preupgrade_family_options(self):
        tx=Update(Mock(),Mock(),None,None,None,{});tx.original=BASE;tx.ui.read_backend.return_value='MPV';tx.ui.read_settings.return_value=('MPV','speed=1')
        tx.start_settings();self.assertEqual(tx.lab_settings.options,'speed=1');self.assertFalse(hasattr(tx.original,'options'))
        tx.tv.transport.bind_original_options.assert_called_once_with(tx.lab_settings)
        self.assertEqual(set(tx.receipt['labSettingsBefore']),{'options','backend'});self.assertNotIn('speed=1',str(tx.receipt))

    def test_before_snapshot_reads_labels_only_not_family_settings(self):
        from tv_family_ui import FamilyUI
        family=FamilyUI(Mock());family.focus_profile=Mock(return_value=('B','synthetic'));family.servers_page=Mock()
        family.server_entries=Mock(return_value=('A','B'));family.select_known_profile=Mock();family.read_settings=Mock()
        self.assertEqual(family.snapshot(),BASE);family.read_settings.assert_not_called()

    def test_lab_server_reentry_never_selects_household_user_card(self):
        from tv_family_ui import FamilyUI
        family=FamilyUI(Mock());family.lab_name='Cleanyfin Lab 0000000000';family.focus_profile=Mock(return_value=('B','synthetic'))
        family.select_user=Mock();node=Mock();node.get=lambda k:dict(package=APP,text='Home').get(k);family.ui.tree.return_value=[node]
        with self.assertRaises(ValueError):family.verify_lab_home('synthetic')
        family.select_user.assert_not_called();family.focus_profile.assert_called_once_with(restart=False)

    def test_stock_delete_popup_accepts_only_release_icon_title_and_exact_actions(self):
        from collections import Counter
        from tv_family_ui import exact_delete_popup
        title='Cleanyfin Lab 0000000000';expected=Counter([title,'Delete','Switch servers']);before=Counter([title,'Select Server','Synthetic Household'])
        for bg in (before,Counter()):
            for icon in (Counter(),Counter([''])):self.assertTrue(exact_delete_popup(bg+expected+icon,before,title))
        for invalid in (['Delete','Switch servers'],['other','Switch servers','Delete'],[title,'Delete'],[title,'Switch servers','delete'],[title,'Switch servers','Delete','unknown']):
            self.assertFalse(exact_delete_popup(Counter(invalid),before,title))
