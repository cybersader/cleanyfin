"""No Android process: exact private dialog and fresh callback boundaries."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from adb_lab_playback import Playback
from tv_combined import CombinedCapture,private_consent_key


class ConsentTests(unittest.TestCase):
    def test_owner_route_never_inspects_or_clicks_stock_prompt(self):
        tv=SimpleNamespace(emulator=None,capture_helper_label='CF Lab 0088008800');h=Mock();call=Mock()
        CombinedCapture.start_consent(None,h,tv,call);call.assert_called_once_with('start');h.DeviceScreen.assert_not_called()

    def test_exact_private_caller_and_focused_stock_share_action(self):
        label='CF Lab 123456abcd';p='com.android.systemui:id/'
        def n(id,text='',cls='android.widget.TextView',focus=False):return {'package':'com.android.systemui','resource-id':id,'class':cls,'text':text,'enabled':'true','focused':'true' if focus else 'false'}
        nodes=[n(p+'screen_share_permission_dialog'),n(p+'screen_share_dialog_title',f'Share your screen with {label}?'),n(p+'text_warning','Your screen is visible to '+label+'.'),n('','Share entire screen'),n('android:id/button1','SHARE SCREEN','android.widget.Button',True),n('android:id/button2','CANCEL','android.widget.Button')]
        self.assertEqual(private_consent_key(nodes,label),'DPAD_CENTER')
        for idx,key,bad in ((0,'package','other'),(1,'text','Share your screen?'),(1,'text',f'Share your screen with {label[:-1]}…?'),(2,'text','Other caller'),(3,'text','single app'),(4,'text','Share'),(4,'enabled','false'),(5,'text','Ignore')):
            changed=copy.deepcopy(nodes);changed[idx][key]=bad;self.assertIsNone(private_consent_key(changed,label))
        self.assertIsNone(private_consent_key(nodes+[nodes[4]],label));nodes[4]['focused']='false';nodes[5]['focused']='true';self.assertEqual(private_consent_key(nodes,label),'DPAD_RIGHT')

    def test_ready_from_an_earlier_or_untagged_callback_cannot_confirm_consent(self):
        p=Playback(Mock(),Path('/tmp'),guest=False,family=True);p.active=True;p.projection_helper=True;p.family_capture_helper=True;p.helper_installed=True;p._call=Mock(return_value=b'')
        p.projection('start');one=p.projection_request_id;p.projection('start');two=p.projection_request_id;self.assertNotEqual(one,two)
        for data in (b'ready 1',one.encode()+b' ready 1'):
            p._call.return_value=data;self.assertEqual(p.projection('status'),b'pending')
        p._call.return_value=two.encode()+b' ready 481';self.assertEqual(p.projection('status'),b'ready 481')
