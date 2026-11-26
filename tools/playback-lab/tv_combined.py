"""Compositor + Android's Wholphin-only media mix. No HDMI or common-clock claim."""
import re
import subprocess
import sys
from tv_internal import InternalCapture


class CombinedCapture(InternalCapture):
    label='Android screenrecord and Android playback mix, s16 stereo 48 kHz'

    def prepare(self,h,tv,stack):
        self.verify_missing(tv,self.paths.video)
        tv.internal_state['screenrecordAbsentBefore']=True
        if not tv.transport.helper_absent():raise ValueError('capture helper already installed; no replacement')
        tv.internal_state['helperAbsentBefore']=True
        h.install_input(tv,capture_only=True)
        stack.callback(self.cleanup,tv)
        tv.transport.projection('grant-audio')  # Required for playback capture; the helper has no mic source.

    def cleanup(self,tv):
        call=tv.transport.projection;report=tv.internal_state
        for op in ('stop','revoke-audio','remove'):
            try:
                response=call(op)
                report[op]=op!='remove' or response.strip()==b'Success'
            except (RuntimeError,subprocess.SubprocessError):report[op]=False
        report['helperAbsentAfter']=tv.transport.helper_absent();tv.transport.projection_helper=False
        try:self.remove(tv,self.paths.video);report['screenrecordRemoved']=True
        except (RuntimeError,subprocess.SubprocessError):report['screenrecordRemoved']=False
        report['combinedCleanupVerified']=all(report.get(k) is True for k in ('helperAbsentBefore','stop','revoke-audio','remove','helperAbsentAfter','screenrecordRemoved'))

    def begin(self,h,tv,s,label,limit,info,*,sound,display):
        if not sound or not display:raise ValueError('combined capture requires both taps')
        ui=tv.family.ui;server,user=ui.focus_profile()
        if (server,user)!=(ui.lab_name,ui.lab_user):raise ValueError('capture not armed on verified lab profile')
        ui.servers_page();ui.select_known_profile(server,user)
        self.verify_missing(tv,self.paths.video);call=tv.transport.projection
        self.start_consent(h,tv,call)
        def status(want):
            text=call('status').decode('ascii').split()
            if text[:1] in (['failed'],['expired']):raise RuntimeError('playback capture failed or expired')
            return text if text[:1]==[want] else None
        ready=h.eventually(lambda:status('ready'),120 if tv.emulator is None else 30,'no fresh projection callback with initialized stereo playback capture after stock consent')
        if len(ready)!=8 or list(map(int,ready[2:5]))!=[48000,2,2]:raise ValueError('playback mix did not negotiate stereo s16 48000 Hz')
        info.update(freshProjectionCallback=True,captureUid=int(ready[1]),audioTap=self.label,expectedAudioDriver='audiotrack')
        audio=tv.work/(label+'.pcm');audio.unlink(missing_ok=True)
        video=h.capture_display(tv,s,label,limit,info,device_path=self.paths.video,android_only=True)
        def finish():
            call('finish');closed=h.eventually(lambda:status('closed'),12,'playback capture did not close');blob=call('pcm')
            if len(closed)!=9 or ready[1:]!=closed[1:-1] or len(blob)!=int(closed[-1]) or len(blob)%4 or not 0<len(blob)<=48000*4*90:raise ValueError('incomplete stereo playback capture transfer')
            audio.write_bytes(blob);h.pcm_arrival(audio,info,'Android AudioRecord stereo s16le 48000 Hz',True)
            info['transferComplete']=True;call('stop')
        def close_audio():
            try:finish()
            except (ValueError,RuntimeError,subprocess.SubprocessError):
                info['audioCaptureFailure']='no complete playback-mix transfer';call('stop')
        s.callback(close_audio);return video,audio

    def start_consent(self,h,tv,call):
        # No shell app-op grant, on either route. Actual owner consent on the box.
        label=tv.capture_helper_label
        if not re.fullmatch('CF Lab [a-f0-9]{10}',label):raise ValueError('no unique verified helper label')
        if tv.emulator is None:
            print(f'On the TV, use the remote only if the stock Share screen dialog names {label} exactly. Cancel if it differs or is unexpected. Waiting up to two minutes for this request; the harness will not press a key.',file=sys.stderr,flush=True)
        call('start')
        if tv.emulator is not None:self.approve_private(h,tv)

    def approve_private(self,h,tv):
        if tv.emulator is None or not tv.transport.guest or tv.found.get('emulator') is not True:raise ValueError('private consent requires a verified synthetic guest')
        ui=h.DeviceScreen(tv)
        def approve():
            key=private_consent_key(ui.tree(),tv.capture_helper_label)
            if key is None:return False
            ui.key(key);return key=='DPAD_CENTER'
        h.eventually(approve,30,'stock emulator projection consent not verified')


def private_consent_key(nodes,label):
    if not re.fullmatch('CF Lab [a-f0-9]{10}',label):return None
    sysui=[n for n in nodes if n.get('package')=='com.android.systemui'];prefix='com.android.systemui:id/'
    def unique(resource):
        found=[n for n in sysui if n.get('resource-id')==resource];return found[0] if len(found)==1 else None
    root=unique(prefix+'screen_share_permission_dialog');title=unique(prefix+'screen_share_dialog_title');warning=unique(prefix+'text_warning')
    share=unique('android:id/button1');cancel=unique('android:id/button2')
    if any(n is None for n in (root,title,warning,share,cancel)):return None
    if (title.get('text')!=f'Share your screen with {label}?' or
        warning.get('text','').count('visible to '+label+'.')!=1 or not any(n.get('text')=='Share entire screen' for n in sysui) or
        share.get('class')!='android.widget.Button' or share.get('text')!='SHARE SCREEN' or share.get('enabled')!='true' or
        cancel.get('class')!='android.widget.Button' or cancel.get('text')!='CANCEL'):return None
    focus=[n for n in nodes if n.get('focused')=='true']
    if len(focus)!=1 or focus[0].get('package')!='com.android.systemui':return None
    if focus[0] is share:return 'DPAD_CENTER'
    if focus[0] is cancel:return 'DPAD_RIGHT'
    if focus[0] is root or focus[0] is unique(prefix+'screen_share_mode_options'):return 'DPAD_DOWN'
    return None
