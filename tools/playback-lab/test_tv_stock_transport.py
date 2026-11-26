"""Synthetic regressions for the stock v1.1.2 transport, not playback evidence."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from fractions import Fraction
from adb_lab import LabError
from adb_lab_playback import ACT,APP,Playback
from tv_stock_transport import StockTransport,TransportLimit


def once(fn,limit,reason):
    v=fn()
    if not v:raise RuntimeError(reason)
    return v


class StockTests(unittest.TestCase):
    def setUp(self):
        self.lab=SimpleNamespace(tv_device='known',viewer='u',client='Wholphin',version='1.1.2',pointer='p',admin='admin',one='lab',r24_duration=15,api=Mock())
        self.h=Mock();self.h.eventually=once;self.tv=Mock();self.ui=Mock();self.s=StockTransport(self.h,self.tv,self.lab,self.ui,None)
        self.session={'DeviceId':'known','UserId':'u','Client':'Wholphin','ApplicationVersion':'1.1.2','NowPlayingItem':{'Id':'p'},'SupportsRemoteControl':True,'PlayState':{'CanSeek':True}}

    def test_ambiguous_sessions_wrong_users_and_unready_remote_seek(self):
        for rows in ([],[self.session,self.session],[dict(self.session,UserId='other')],[dict(self.session,Client='other')],[dict(self.session,SupportsRemoteControl=False)],[dict(self.session,PlayState={})]):
            self.lab.api.return_value=rows
            with self.assertRaises(TransportLimit):self.s.session({},item='p',seek=True)
        self.lab.api.return_value=[self.session];self.assertEqual(self.s.session({},item='p',seek=True),self.session)

    def test_bounded_millisecond_extra_on_exact_playback_intent_only(self):
        with tempfile.TemporaryDirectory() as w:
            port=Playback(Mock(),Path(w),guest=False,family=True);port.qualify('armeabi-v7a');port._call=Mock(return_value=b'')
            uuid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';args=['shell','am','start','-n',ACT,'-a',APP+'.PLAYBACK','--es','itemId',uuid,'--es','userId',uuid,'--es','serverId',uuid,'--el','position','5480']
            port.run(*args);port._call.assert_called_once();port._call.reset_mock()
            for i,bad in [(16,'--es'),(17,'offset'),(18,'20001'),(18,'-1'),(18,'5480; echo'),(6,'android.intent.action.VIEW'),(9,'not-a-uuid')]:
                changed=args[:];changed[i]=bad
                with self.assertRaises(LabError):port.run(*changed)
            port._call.assert_not_called()

    def test_bookmark_must_change_and_match_a_real_stopped_frame(self):
        self.h.shown_frame.return_value=100;self.h.now.return_value=None;self.h.APP=APP
        mark=SimpleNamespace(kept_frames=[100],frame_time=lambda f:Fraction(4))
        for new in (0,39000000,80000000,40000000):
            self.lab.api.side_effect=[{'UserData':{'PlaybackPositionTicks':39000000}},[self.session],[self.session],{'UserData':{'PlaybackPositionTicks':new}}]
            info={}
            if new==40000000:self.assertEqual(self.s.bookmark(mark,info),new)
            else:
                with self.assertRaises(TransportLimit):self.s.bookmark(mark,info)
            self.assertEqual(info['pausedFrameBeforeStop'],100);self.assertFalse(any('POST' in c.args for c in self.lab.api.call_args_list))
