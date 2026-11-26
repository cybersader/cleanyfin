"""One uncut audio control; never an eligibility scorer."""
import re
import subprocess
import numpy as np
import format_fixtures as ff
from tv_dolby_control import read


def active_track(tv,uid,pid):
    text=tv.transport.projection('tracks').decode(errors='replace')
    for line in text.splitlines():
        if not (re.search(r'\bu/pid:\s*%d/%d\b'%(uid,pid),line) and
                'type:android.media.AudioTrack' in line and 'state:started' in line):continue
        usage=re.search(r'\busage=(USAGE_\w+)',line);content=re.search(r'\bcontent=(CONTENT_TYPE_\w+)',line)
        if usage:
            data={'type':'AudioTrack','uidPidMatched':True,'state':'started','usage':usage[1],
                  'contentType':content[1] if content else None}
            for key,pattern in [('flags',r'flags=(0x[0-9A-Fa-f]+)'),('sampleRate',r'sampleRate[=:]\s*(\d+)'),('channelMask',r'channelMask[=:]\s*(0x[0-9a-fA-F]+|\d+)')]:
                hit=re.search(pattern,line)
                if hit:data[key]=hit[1]
            return data
    return None


def measure(h,tv,lab,out,matrix):
    probe=out['projection']={'consent':'run-scoped emulator PROJECT_MEDIA app-op; ordinary MediaProjection API','scope':'Wholphin UID AND USAGE_MEDIA; no microphone or virtual display'}
    spec=ff.LAYOUTS[lab.r24_layout];_,_,facts=read(lab,matrix);probe.update(facts)
    manifest=h.run(tv.sdk/'build-tools/36.0.0/aapt2','dump','xmltree',tv.work/'release-x86_64'/h.APK,'--file','AndroidManifest.xml').decode()
    target=re.search(r'targetSdkVersion\([^)]*\)=(\d+)',manifest)
    probe['sourceManifest']={'targetSdk':int(target[1]) if target else None,'explicitCapturePolicy':'allowAudioPlaybackCapture' in manifest}
    probe['audioLevels']=tv.configure_audio()
    tv.transport.projection_helper=True; call=tv.transport.projection
    def stage(expected):
        raw=call('status').decode(errors='replace').strip();tokens=raw.split()
        if tokens[:1] in (['failed'],['expired']):raise RuntimeError('Helper '+(raw if re.fullmatch('[A-Za-z0-9 ]{1,80}',raw) else 'failed'))
        return tokens if tokens[:1]==[expected] else None
    from tv_channel_control import NAME,calibration,decode,observation,predict,samples
    def take(label,item,report):
        call('start');ready=h.eventually(lambda:stage('ready'),20,'No initialized playback AudioRecord after projection grant')
        uid,rate,ch,enc=map(int,ready[1:5]);info=report.setdefault('runtime',{});info['captureUid']=uid
        report['AudioRecord']={'sampleRate':rate,'channels':ch,'encoding':enc,'attempts':ready[5].split(','),
                               'channelIndexMask':int(ready[6]),'channelPositionMask':int(ready[7])}
        if rate!=48000 or (ch,enc) not in ((6,4),(2,4),(2,2)):raise RuntimeError('Unexpected negotiated AudioRecord format')
        h.record(tv,lab,item,label,1,info,display=False,sound=False)
        logs=tv.work/(label+'-logcat.log')
        if logs.is_file():
            ao=re.findall(r'\bcplayer,\s+AO: \[audiotrack\] (\d+Hz [A-Za-z0-9.()_-]+ \d+ch [A-Za-z0-9_-]+)',logs.read_text(errors='replace'))
            if ao:info['mpvAudioTrackFormat']=ao[-1]
        call('finish');end=h.eventually(lambda:stage('closed'),12,'Helper did not finalize its PCM')
        pcm=call('pcm');report['helperDeclaredBytes']=int(end[-1]);report['transferredBytes']=len(pcm)
        if ready[1:]!=end[1:-1] or len(pcm)!=int(end[-1]):raise ValueError('Incomplete playback-capture transfer')
        report['nonzeroFrames']=int(np.count_nonzero(np.any(samples(pcm,ch,enc)!=0,axis=1)))
        if not (info.get('mpvAudioTrackFormat')=='48000Hz 5.1 6ch float' and info.get('playMethod')=='DirectPlay'
                  and spec['audio'][0][0] in info.get('libmpvAudioCodecs',[])
                  and (info.get('activeTrack') or {}).get('usage')=='USAGE_MEDIA'):
            raise ValueError('Missing matching ordinary 5.1 float AudioTrack, DirectPlay, or decoded Dolby track')
        tv.adb('shell','am','force-stop',h.APP);call('stop');return pcm,ch,enc
    try:
        call('grant-audio');call('allow');probe['appOpAllowed']=b'allow' in call('policy')
        if not probe['appOpAllowed']:raise RuntimeError('Run-scoped projection grant not observed')
        pcm,ch,enc=take('projection-uncut',lab.item,probe)
        whole_native=decode(lab.fixtures,lab.r24_layout+'.'+spec['container'])
        if len(whole_native)!=facts['decodedFrames']*24:raise ValueError('Incomplete native float decoder reference')
        native=whole_native[facts['discardedBeforeOrigin']*24:];probe['perChannelIdentityCertified']=False
        if (ch,enc)==(6,4):
            probe['nativeFloatComparison']=observation(native,pcm,ch,enc)
            probe['wholeNativeFloatComparison']=observation(whole_native,pcm,ch,enc)
            probe['comparison']=probe['nativeFloatComparison'];probe['perChannelIdentityCertified']=probe['comparison']['outcome']=='bit-exact'
        else:probe['comparison']={'outcome':'unmeasured','reason':'native channels not preserved; await calibrated mix'}
        rows=lab.api('/Items?Recursive=true&IncludeItemTypes=Movie,Video&Fields=Path',user=lab.admin)['Items']
        items=[i['Id'].replace('-','') for i in rows if i.get('Path')=='/media/movies/'+NAME]
        if len(items)!=1:raise ValueError('Independent channel-ID source not uniquely indexed')
        cr=probe['channelIdentification']={};cpcm,cch,cenc=take('projection-channel-id',items[0],cr)
        if (cch,cenc)!=(ch,enc):raise ValueError('Calibration route changed capture format')
        for key in ('flags','sampleRate','channelMask','usage','contentType'):
            if cr['runtime']['activeTrack'].get(key)!=probe['runtime']['activeTrack'].get(key):raise ValueError('Calibration playback attributes changed')
        matrix,cal=calibration(decode(lab.fixtures,NAME),cpcm,ch,enc);cr.update(cal)
        if ch==2:
            cr['predictionEncoding']='float32' if enc==4 else 'nearest-even signed16, saturated'
            pred=predict(native,matrix,enc);probe['empiricalMixComparison']=observation(pred,pcm,ch,enc)
            if cr['validated']:probe['comparison']=dict(probe['empiricalMixComparison'],scope='empirical stereo mix only; never per-channel identity')
            else:probe['comparison']['reason']='held-out channel-ID signal did not validate a stable downmix'
    finally:
        cleanup=probe['cleanup']={}
        for key in ('stop','default','revoke-audio','remove'):
            try:
                call(key);cleanup[key]=True
                if key=='default':cleanup['allowCleared']=b'allow' not in call('policy').split(b';')[0]
            except (subprocess.SubprocessError,RuntimeError):cleanup[key]=False
        cleanup['helperAbsent']=not call('present').strip();tv.transport.projection_helper=False
        if not all(cleanup.values()):raise RuntimeError('Projection cleanup not verified')
