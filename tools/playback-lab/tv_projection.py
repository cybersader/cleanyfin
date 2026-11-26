"""One uncut audio control; never an eligibility scorer."""
from fractions import Fraction
import hashlib
import re
import subprocess
import numpy as np
import format_fixtures as ff
import format_measure as fm
from hard_fixture_lab import media
from tv_r24 import aligned_control


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


def compare(ref,raw,frames):
    out={'referenceFrames':len(ref)//4,'capturedFrames':len(raw)//4,'expectedFrames':frames,
         'referenceSha256':hashlib.sha256(ref).hexdigest(),'captureSha256':hashlib.sha256(raw).hexdigest(),'outcome':'unmeasured'}
    if not raw or len(raw)%4 or len(ref)!=frames*4:
        out['reason']='missing complete stereo reference or capture';return out
    y=np.frombuffer(raw,dtype='<i2').reshape(-1,2);nz=np.flatnonzero(y.any(axis=1));out['nonSilentFrames']=len(nz)
    if not len(nz):out['reason']='silent capture';return out
    out['containsFullReferenceBytes']=raw.find(ref)>=0
    try:aligned_control(ref,raw)
    except ValueError:pass
    else:out.update(outcome='bit-exact',equalStereoFrames=frames);return out
    # Approximate alignment cannot certify identity. Require distinct beginning/middle/end
    # matches on one clock and complete coverage before reporting a numerical deviation.
    from scipy.signal import correlate
    x=np.frombuffer(ref,dtype='<i2').reshape(-1,2);matches=[]
    for at in (0,frames//2,frames-24000):
        window=x[at:at+24000,0].astype(float); signal=y[:,0].astype(float); n=len(window)
        if len(signal)<n or not window.any():break
        powers=np.r_[0.,np.cumsum(signal**2)];scores=correlate(signal,window,mode='valid',method='fft')/np.sqrt(np.maximum(1.,(powers[n:]-powers[:-n])*np.dot(window,window)))
        best=int(np.argmax(scores)); peak=float(scores[best]);other=scores.copy();other[max(0,best-95):best+96]=0
        match=dict(at=at,offset=best-at,score=round(peak,5),other=round(float(other.max()),5))
        if peak>=.90 and peak-other.max()>=.005:
            delta=y[best:best+n].astype('int32')-x[at:at+n]
            match.update(windowFrames=n,unequalChannelSamples=int(np.count_nonzero(delta)),maxAbsDifference=int(abs(delta).max()))
        matches.append(match)
    out['alignmentWindows']=matches
    if len(matches)!=3 or any(m['score']<.90 or m['score']-m['other']<.005 for m in matches) or len({m['offset'] for m in matches})!=1:
        out['reason']='no unique complete common source clock';return out
    start=matches[0]['offset'];end=start+frames
    if start<0 or end>len(y):out['reason']='incomplete aligned take';return out
    d=y[start:end].astype('int32')-x;out.update(outcome='deviation',unequalChannelSamples=int(np.count_nonzero(d)),maxAbsDifference=int(abs(d).max()),outsideNonzeroFrames=int(np.count_nonzero((nz<start)|(nz>=end))))
    return out


def measure(h,tv,lab,out,matrix):
    probe=out['projection']={'consent':'run-scoped emulator PROJECT_MEDIA app-op; ordinary MediaProjection API','scope':'Wholphin UID AND USAGE_MEDIA; no microphone or virtual display'}
    spec=ff.LAYOUTS[lab.r24_layout];src=lab.r24_layout+'.'+spec['container']; plan=fm.plan_for(matrix.probe_json(lab.fixtures,src));origin=round((plan['videoAnchorMs']+plan['zeroMs'])*48)
    frames=fm.FRAMES*fm.SAMPLES_PER_FRAME
    ref=media(lab.fixtures,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+src,'-map','0:a:0','-af',f'aresample=48000:first_pts={origin},atrim=end_sample={frames}','-ac','2','-ar','48000','-f','s16le','pipe:1',readonly=True)
    # Decoder priming can place real samples before video time zero. Do not pad the tail
    # to the picture duration. Independently count decoded audio frames and verify that
    # the existing R24 reference equals the precise slice of the untrimmed decode.
    decoded=ff.probe(lab.fixtures,src,'-select_streams','a:0','-show_entries','frame=nb_samples,pts_time')['frames']
    begin=Fraction(decoded[0]['pts_time'])*48000; clock=begin
    for frame in decoded:
        if Fraction(frame['pts_time'])*48000!=clock:raise ValueError('Noncontiguous decoder frame timestamps')
        clock+=int(frame['nb_samples'])
    if begin.denominator!=1 or begin>origin:raise ValueError('No verified reference origin')
    whole=media(lab.fixtures,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+src,'-map','0:a:0',
                '-ac','2','-ar','48000','-f','s16le','pipe:1',readonly=True)
    skip=origin-int(begin); expected=min(frames,int(clock)-origin)
    if len(whole)!=4*int(clock-begin) or ref!=whole[4*skip:4*(skip+expected)]:raise ValueError('Independent uncut decoder vector disagrees')
    manifest=h.run(tv.sdk/'build-tools/36.0.0/aapt2','dump','xmltree',tv.work/'release-x86_64'/h.APK,'--file','AndroidManifest.xml').decode()
    target=re.search(r'targetSdkVersion\([^)]*\)=(\d+)',manifest)
    probe['sourceManifest']={'targetSdk':int(target[1]) if target else None,'explicitCapturePolicy':'allowAudioPlaybackCapture' in manifest}
    probe.update(referenceFrames=len(ref)//4,expectedReferenceFrames=expected,pictureClockFrames=frames,
                 firstDecodedSample=int(begin),decodedFrames=int(clock-begin),discardedBeforeOrigin=skip,
                 referenceOrigin=origin,audioLevels=tv.configure_audio())
    tv.transport.projection_helper=True; call=tv.transport.projection;rt=probe['runtime']={}
    def stage(expected):
        raw=call('status').decode(errors='replace').strip();tokens=raw.split()
        if tokens[:1] in (['failed'],['expired']):raise RuntimeError('Helper '+(raw if re.fullmatch('[A-Za-z0-9 ]{1,80}',raw) else 'failed'))
        return tokens if tokens[:1]==[expected] else None
    try:
        call('grant-audio');call('allow');probe['appOpAllowed']=b'allow' in call('policy')
        if not probe['appOpAllowed']:raise RuntimeError('Run-scoped projection grant not observed')
        call('start');ready=h.eventually(lambda:stage('ready'),18,'No initialized playback AudioRecord after projection grant')
        uid,rate,ch,enc=map(int,ready[1:]);rt['captureUid']=uid
        probe['AudioRecord']={'sampleRate':rate,'channels':ch,'encoding':enc}
        if (rate,ch,enc)!=(48000,2,2):raise RuntimeError('Unexpected negotiated AudioRecord format')
        h.record(tv,lab,lab.item,'projection-uncut',1,rt,display=False,sound=False)
        logs=tv.work/'projection-uncut-logcat.log'
        if logs.is_file():
            ao=re.findall(r'\bcplayer,\s+AO: \[audiotrack\] (\d+Hz [A-Za-z0-9.()_-]+ \d+ch [A-Za-z0-9_-]+)',logs.read_text(errors='replace'))
            if ao:rt['mpvAudioTrackFormat']=ao[-1]
        call('finish');end=h.eventually(lambda:stage('closed'),10,'Helper did not finalize its PCM')
        pcm=call('pcm');probe['helperDeclaredBytes']=int(end[-1]);probe['transferredBytes']=len(pcm)
        if ready[1:]!=end[1:-1] or len(pcm)!=int(end[-1]):raise ValueError('Incomplete playback-capture transfer')
        if not (rt.get('mpvAudioTrackFormat') and rt.get('playMethod')=='DirectPlay' and rt.get('libmpvEof') and spec['audio'][0][0] in rt.get('libmpvAudioCodecs',[]) and (rt.get('activeTrack') or {}).get('usage')=='USAGE_MEDIA'):
            probe['comparison']={'outcome':'unmeasured','reason':'missing direct uncut playback, EOF, decoder or active UID/media AudioTrack evidence','nonzeroBytes':sum(bool(v) for v in pcm)}
        else:
            probe['comparison']=compare(ref,pcm,expected)
            probe['wholeDecoderComparison']=compare(whole,pcm,int(clock-begin))
    finally:
        cleanup=probe['cleanup']={}
        for key in ('stop','default','revoke-audio','remove'):
            try:
                call(key);cleanup[key]=True
                if key=='default':cleanup['allowCleared']=b'allow' not in call('policy').split(b';')[0]
            except (subprocess.SubprocessError,RuntimeError):cleanup[key]=False
        cleanup['helperAbsent']=not call('present').strip();tv.transport.projection_helper=False
        if not all(cleanup.values()):raise RuntimeError('Projection cleanup not verified')
