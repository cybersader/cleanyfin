"""R24 on a real Wholphin presentation tap. Never transfer desktop or recorded-file evidence.

Use the ordinary synthetic format lab to establish the edition, the server clock and the diagnostic
v1 measurement grant. The stock app must still request that grant and the ticketed source itself.
HDMI/monitor PCM is downstream of downmix, volume, resampling and possible dither. Do not assume a
unit-gain, clock-stable tap: a known uncut positive and its independent repetition must establish it.
"""
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import time
from uuid import UUID

import numpy as np

import format_fixtures as ff
import format_measure as fm
import r22_oracle as r22
from tv_pcm import pcm
from tv_score import picture, frame_summary

LAYOUTS = ('mp4-h264-eac351', 'mkv-h264-ac351', 'mkv-h264-eac351')


def make_lab(matrix, directory, line, layout, tv, origin):
    mode = 'Dolby cons_noisegen 1, ' + tv.device_mode()
    if len(mode) > 256:
        raise ValueError('exact Dolby device mode exceeds the register limit')
    class WholphinLab(matrix.FormatLab):
        def enroll_evidence(self, uid):
            self.put(self.data/'profiles'/uid/'evidence-device.txt', f'{tv.app_digest}\n{mode}\n'.encode())

        def stub_register(self):
            return json.loads(self.register_path.read_text())

    fixtures=directory/'movies'; fixtures.mkdir()
    facts=ff.generate(fixtures, layout)
    if getattr(tv,'projection_control',False):
        from tv_channel_control import generate
        generate(fixtures,ff.LAYOUTS[layout]['audio'][0][0])
    lab=WholphinLab(directory/'server', line, fixtures, [layout], (tv.app_digest, '1.1.2'))
    lab.client='Wholphin'; lab.version=lab.wholphin['tuple']['appVersion']; lab.work.mkdir(); lab.public_origin=origin
    lab.r24_layout=layout; lab.r24_fixture=facts; lab.r24_mode=mode
    return lab


def prepare(lab, matrix, tv, wait):
    lab.prepare(); name=lab.r24_layout; item=lab.items[name]; spec=ff.LAYOUTS[name]
    binding=wait(lambda:(lab.detail(item) or {}).get('binding'), 120, 'Dolby edition not verified')
    streams=lab.api('/Items/'+item+'/PlaybackInfo',user=lab.admin)['MediaSources'][0]
    source,_=matrix.describe_source(streams['Container'],streams['MediaStreams'],binding['tracks'],binding['durationMs'])
    row=dict(tuple=lab.tuple_for(source,spec),actions=['cut'],controls=[], evidenceRef='tools/playback-lab/tv_r24.py',
             date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),status='experimental',
             observation='Synthetic lab enrollment for measurement only; not evidence of test admission.',
             results={k:dict(outcome='not-measured',limit='Awaiting the actual TV presentation.') for k in
                      ('markedPicture','markedSound','keptPictureAtJoin','keptSoundAtJoin')})
    row['tuple'].update(appId=lab.client,appVersion=lab.version,deviceMode=lab.r24_mode,appBuild=tv.app_digest)
    lab.put(lab.register_path,json.dumps(dict(registerVersion=1,rows=[row])).encode())
    lab.cfg.update(LabSourceSha256=binding['sha256'],LabOrigin=lab.public_origin);lab.update()
    assert binding['sha256']==lab.r24_fixture['sha256']
    lab.r24_binding=binding; lab.r24_anchor=next(t['anchorSourceMs'] for t in binding['tracks'] if t['kind']=='video')
    lab.arm(item,binding,lab.r24_anchor+5480,lab.r24_anchor+10520)


def aligned_control(reference, captured):
    """Require a unique whole take on the source clock and no unaccounted active PCM outside it.
    Do not use approximate correlations for sample identity on an external asynchronous tap."""
    if not len(reference) or len(reference)%4 or len(captured)%4:
        raise ValueError('missing or partial stereo PCM')
    anchor=reference[4800*4:4896*4]; at=captured.find(anchor)
    if not anchor or at<4800*4 or at%4 or captured.find(anchor,at+1)!=-1:
        raise ValueError('cannot establish unique stereo control clock (gain, clock or downmix differs)')
    at-=4800*4; end=at+len(reference)
    if captured[at:end]!=reference or any(captured[:at]) or any(captured[end:]):
        raise ValueError('uncut 48 kHz stereo is not identical on the full external tap')
    return captured[at:end]


class SoundGate:
    """Known positives come from two *captured* complete controls, not a substituted math vector.
    Exact kept samples may move or be missing. A collision or novel value is unmeasured, never a clean null.
    Digital silence outside the film is harmless only if silence cannot be a marked sample for this position.
    """
    def __init__(self, source, training, held_out, a, b):
        x=aligned_control(source,training); z=aligned_control(source,held_out)
        a,b=max(0,a),min(len(x)//4,b)
        if a>=b:raise ValueError('no marked positive sound control')
        words=np.frombuffer(x,'<u4'); self.marked=np.unique(words[a:b]); self.kept=np.unique(np.r_[words[:a],words[b:]])
        positive=np.frombuffer(z,'<u4')[a:b]
        if not np.all(np.isin(positive,self.marked)):raise ValueError('held-out marked samples not detected')
        self.size=len(positive); self.a=a;self.b=b

    def score(self,raw):
        if not raw or len(raw)%4:raise ValueError('missing stereo take')
        words=np.frombuffer(raw,'<u4');active=np.flatnonzero(words)
        if not len(active):raise ValueError('no active sound in take')
        body=words[active[0]:active[-1]+1];marked=np.isin(body,self.marked);kept=np.isin(body,self.kept)
        detections=int(np.count_nonzero(marked & ~kept)); ambiguous=int(np.count_nonzero(marked & kept))
        unknown=int(np.count_nonzero(~marked & ~kept)); padding=int(len(words)-len(body))
        if padding and 0 in self.marked:ambiguous+=padding
        return dict(markedSoundDetections=detections,ambiguous=ambiguous,unknown=unknown,activeSpan=len(body),
                    calibratedPositiveSamples=self.size,clear=not(detections or ambiguous or unknown))


def resolution(pic,info):
    # A frame that flashes for one display refresh may be shorter than a 25 fps movie frame.
    # Do not certify absence from the original's frame period or a nominal recorder rate.
    timing=info.get('presentationTiming',{})
    if timing.get('incomplete') or timing.get('failedPolls'):return False
    layers=timing.get('layers',[])
    periods=[l['periodNs']/1e6 for l in layers if l.get('periodNs',0)>0 and l.get('presentCount',0)>1]
    return bool(periods and not any(l.get('possibleLostRing') for l in layers) and
                pic.get('fullCaptureMaxGapMs',float('inf')) < min(periods) and not pic.get('interiorUnreadable'))


def seams(source,raw,mark,first,kind):
    # Align known unique head and tail anchors around an observed straight/start/resume, not a
    # nominal HDMI delay. Never shift one side independently to manufacture a pop or sync pass.
    report=dict(silence='not measured',pop='not measured',sync='not measured: no shared presentation/audio timestamp',
                keptSoundLoss='not measured')
    if kind=='seek-resume' or first is None:return report,False
    x=pcm(source); y=pcm(raw)
    ideal=np.concatenate((x[:mark.a],x[mark.b:]))[max(0,mark.kept_index(first*fm.SAMPLES_PER_FRAME)):]
    if len(ideal)<384:return report,False
    def at(pos):
        token=ideal[pos:pos+96].tobytes();n=raw.find(token)
        return n//4 if n>=0 and n%4==0 and raw.find(token,n+1)<0 else None
    a,b=at(96),at(len(ideal)-192)
    if a is None or b is None:return report,False
    begin=a-96;end=b+192
    if not 0<=begin<end<=len(y) or not len(y[begin:end]):return report,False
    # Anchors locate the ideal and real interiors; do not trim away nonzero leading/trailing
    # media for the gate. SoundGate.score searches the ENTIRE capture including those samples.
    actual=y[begin:end];actualQuiet=r22.silence(actual);idealQuiet=r22.silence(ideal)
    report['silence']=dict(extraZeroSamples=max(0,actualQuiet[0]-idealQuiet[0]),extraQuietSamples=max(0,actualQuiet[1]-idealQuiet[1]))
    start=max(0,mark.kept_index(first*fm.SAMPLES_PER_FRAME));join=mark.a-start
    if 120<=join<min(len(actual),len(ideal))-120:
        report['pop']=r22.compare_transient(r22.transient_channels(actual,join),r22.transient_channels(ideal,join),[0,0])
    # Net duration alone is not lost kept sound: a repeated sample could hide an omission.
    if np.array_equal(actual,ideal):report['keptSoundLoss']=0
    report['netShorterSamples']=len(ideal)-len(actual)
    return report,True


def verdict(observations, blocker):
    positions=observations.get('positions',[])
    takes=[t for p in positions for t in p['takes'].values()]
    pictures=sum(t.get('markedPictures',0) for t in takes)
    sounds=sum(t.get('sound',{}).get('markedSoundDetections',0) for t in takes)
    if pictures or sounds:
        return 'FAIL', f'Observed marked picture IDs: {pictures}; marked stereo sample detections: {sounds}. No admission.'
    count=sum(bool(p.get('clear')) for p in positions)
    if not blocker and count==len(positions)==24 and observations.get('admitted'):
        return 'PASS','24/24 fully observed positions clear on this tuple only'
    return 'not-measured',blocker or f'{count}/24 fully observed positions clear; no admission'


def measure(h,tv,lab,ui,out,matrix):
    from tv_dolby_control import read
    layout=lab.r24_layout; spec=ff.LAYOUTS[layout]; binding=lab.r24_binding; av=lab.r24_anchor
    source,_,out['controlClock']=read(lab,matrix)
    scorer=h.module('analyze-capture');out['audioLevels']=tv.configure_audio();out['layout']=layout
    out['plannedPositions']=matrix.POSITIONS;out['tuple']=lab.stub_register()['rows'][0]['tuple'];out['positions']=[]
    out['admitted']=False

    def take(label,item,begin=None):
        info={};out['lastTake']={'label':label,'runtime':info};routes=h.handoff(lab)[0];tv.clear_log();video=audio=None
        try:
            video,audio=h.record(tv,lab,item,label,1,info,begin,display=True,sound=True)
            info['grantRouteDeltas']=[b-a for a,b in zip(routes,h.handoff(lab)[0])]
            info['observedCodec']=spec['audio'][0][0] in info.get('libmpvAudioCodecs',[])
            frames=(picture(video,scorer,include_samples=True) if video and video.is_file() and video.stat().st_size else
                    dict(frame_summary([]),samples=[],unreadableFrames=0,flatFrames=0))
            raw=audio.read_bytes() if audio and audio.is_file() else b''
            return info,frames,raw
        finally:
            tv.adb('shell','am','force-stop',h.APP)
            for path in (video,audio):
                if path:path.unlink(missing_ok=True)
            (tv.work/(label+'-logcat.log')).unlink(missing_ok=True)

    controls=[]
    for label in ('positive','held-out'):
        info,pic,raw=take(label,lab.item)
        control=out.setdefault('controls',{})[label]=dict(pictureCount=pic['distinct'],runtime=info,
            complete=pic['controlComplete'],fullMaxGapMs=pic.get('fullCaptureMaxGapMs'),captured=pic['captured'],
            unreadable=pic.get('unreadableFrames'),flatFrames=pic.get('flatFrames'),tapVerified=False)
        if not (h.live_pcm(info) and info['observedCodec'] and info.get('libmpvEof') and h.picture_calibrated(pic,info)):
            raise ValueError('uncut Dolby control lacks verified sound, all 500 pictures, EOF or real-time playback')
        aligned_control(source,raw);controls.append(raw);control['tapVerified']=True
        control['singleRefreshResolved']=resolution(pic,info)
    if not all(c['singleRefreshResolved'] for c in out['controls'].values()):
        raise ValueError('screen timing cannot exclude a single marked refresh; no admission')

    def session():
        return h.eventually(lambda:next((s for s in lab.api('/Sessions',user=lab.admin) if s.get('DeviceId')==lab.tv_device),None),8,'no exact TV session')
    def command(s,op,query=''):
        lab.api('/Sessions/'+s['Id']+'/Playing/'+op+query,'POST',user=lab.admin)
    def start_at(ticks):
        def begin():
            tv.adb('shell','am','start','-W','-n',h.APP+'/.MainActivity');s=session()
            lab.api('/Sessions/'+s['Id']+'/Playing?playCommand=PlayNow&itemIds='+lab.pointer+'&startPositionTicks='+str(ticks),'POST',user=lab.admin)
        return begin
    def seek_and_stop(target,mark,witness):
        def begin():
            h.play(tv,lab,lab.pointer);s=h.eventually(lambda:h.now(lab,lab.pointer),15,'no playing session for seek')
            if not ui.action('pause'):raise ValueError('stock pause unavailable')
            h.eventually(ui.paused,8,'stock pause not acknowledged')
            for ms in (target+200 if mark.landing(Fraction(target+200,1000)) is not None else max(0,target-200),target):
                command(s,'Seek','?seekPositionTicks='+str(ms*10000));wanted=mark.landing(Fraction(ms,1000))
                h.eventually(lambda:h.shown_frame(tv,scorer)==wanted,5,'stock seek did not reach the intended barcode');witness.append(wanted)
            if not ui.action('play'):raise ValueError('stock resume unavailable')
        return begin
    for rel,end in matrix.POSITIONS:
        end=min(end,binding['durationMs']-av);mark=fm.Mark(dict(videoAnchorMs=av,durationMs=binding['durationMs']),av+rel,av+end)
        lab.arm(lab.item,binding,av+rel,av+end);lab.r24_duration=(binding['durationMs']-end+rel)/1000
        gate=SoundGate(source,*controls,mark.a,mark.b);pos={'mark':mark.label(),'takes':{},'clear':False};out['positions'].append(pos)
        target=rel if mark.landing(Fraction(rel,1000)) is not None else max(0,rel-200)
        starts=sorted({max(0,rel-200),max(0,rel-1),rel,rel+520});landings=[]
        modes=[('straight',None,mark.kept_frames[0]),*[(f'start-{t}',start_at(t*10000),mark.landing(Fraction(t,1000))) for t in starts if mark.landing(Fraction(t,1000)) is not None],('seek-resume',seek_and_stop(target,mark,landings),mark.kept_frames[0])]
        # Restart the app and choose the stock Resume action on a real, persisted filtered bookmark.
        def resume():
            tv.adb('shell','am','start','-n',h.APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.server_ref)))
            node=h.eventually(lambda:next((n for n in ui.tree() if n.get('package')==h.APP and any('resume' in n.get(k,'').lower() for k in ('text','content-desc'))),None),15,'no stock Resume choice')
            ui.activate(node.get('content-desc') or node['text'])
        for key,begin,first in modes+[('persisted-resume',resume,None)]:
            if key=='persisted-resume':
                # Establish the bookmark by stopping real playback; never manufacture user-data ticks.
                h.play(tv,lab,lab.pointer);s=h.eventually(lambda:h.now(lab,lab.pointer),15,'no playing session for bookmark')
                ms=max(1000,min(target,int(lab.r24_duration*1000)-1200));command(s,'Seek','?seekPositionTicks='+str(ms*10000))
                h.eventually(lambda:abs((h.now(lab,lab.pointer) or {}).get('PlayState',{}).get('PositionTicks',-10**9)/10000-ms)<500,6,'bookmark seek not witnessed');ui.key('BACK')
                h.eventually(lambda:not h.now(lab,lab.pointer),8,'stock player did not stop');tick=lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
                if not tick or abs(tick/10000-ms)>1200:raise ValueError('no corresponding persisted filtered bookmark')
                tv.adb('shell','am','force-stop',h.APP);pos['bookmarkTicks']=tick;first=mark.landing(Fraction(tick,10**7))
            info,pic,raw=take('r24-take',lab.pointer,begin);ids=pic.pop('samples')
            present=set(i for i in ids if i is not None); marked=len(present & mark.marked_frames)
            evidence=pos['takes'][key]=dict(markedPictures=marked,first=pic['first'],last=pic['last'],verified=False,clear=False,
                fullMaxGapMs=pic.get('fullCaptureMaxGapMs'),captured=pic['captured'],unreadable=pic.get('unreadableFrames'),flatFrames=pic.get('flatFrames'))
            sound=evidence['sound']=gate.score(raw)
            required=[f for f in mark.kept_frames if first is not None and f>=first]
            seam,span=seams(source,raw,mark,first,key);seam['missingKeptPictures']=len(set(required)-present)
            if key=='seek-resume':
                # Captured from before launch through both landings to EOF; require an audio
                # head and tail from the same presented ranges as well.
                a=mark.kept_frames[0]*fm.SAMPLES_PER_FRAME; b=(mark.kept_frames[-1]+1)*fm.SAMPLES_PER_FRAME
                span=all(raw.count(pcm(source)[p:p+96].tobytes())>=1 for p in (a+96,b-192))
            verified=bool(h.live_pcm(info) and info['observedCodec'] and info.get('libmpvEof') and h.has_routes(info) and
                span and resolution(pic,info) and pic['first']==first and pic['last']==mark.kept_frames[-1] and
                (key!='seek-resume' or len(landings)==2))
            evidence.update(seams=seam,verified=verified,clear=verified and not marked and sound['clear'])
        pos['clear']=all(t['clear'] for t in pos['takes'].values());pos['pausedLandings']=landings
    out['admitted']=len(out['positions'])==24 and all(p['clear'] for p in out['positions'])
