"""Compositor + Android's Wholphin-only media mix. No HDMI or common-clock claim."""
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
        ready=h.eventually(lambda:status('ready'),30,'no initialized stereo playback capture after stock consent')
        if len(ready)!=8 or list(map(int,ready[2:5]))!=[48000,2,2]:raise ValueError('playback mix did not negotiate stereo s16 48000 Hz')
        info.update(captureUid=int(ready[1]),audioTap=self.label,expectedAudioDriver='audiotrack')
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
        call('start')
        if tv.emulator is None:
            print('Approve the Android capture prompt for Disposable TV fixture input on the TV.',file=sys.stderr,flush=True);return
        # Private synthetic guest only. Do not accept any unrelated prompt.
        ui=h.DeviceScreen(tv)
        def approve():
            nodes=ui.tree();sysui=[n for n in nodes if n.get('package')=='com.android.systemui']
            # Structural diagnostics on the synthetic guest only; no arbitrary UI text in receipts.
            known={'start now','start','share','allow','cancel','share screen','entire screen','share entire screen'}
            tv.internal_state['syntheticConsent']={'systemUiNodes':len(sysui),'helperLabelSeen':any('Disposable TV fixture input' in n.get('text','') for n in sysui),
                'stockActions':sorted({n.get('text','').casefold() for n in sysui}&known),'button1':any(n.get('resource-id')=='android:id/button1' for n in sysui)}
            if not any('Disposable TV fixture input' in n.get('text','') for n in sysui):return False
            buttons=[n for n in sysui if n.get('resource-id')=='android:id/button1' and n.get('text','').casefold() in ('start now','start','share','allow') and n.get('enabled')=='true']
            if len(buttons)!=1:return False
            if buttons[0].get('focused')=='true':ui.key('DPAD_CENTER');return True
            ui.key('DPAD_RIGHT');return False
        h.eventually(approve,20,'stock emulator projection consent not verified')
