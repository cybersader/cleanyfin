"""R24 on a real Wholphin presentation tap. Never transfer desktop or recorded-file evidence.

Use the ordinary synthetic format lab to establish the edition, the server clock and the diagnostic
v1 measurement grant. The stock app must still request that grant and the ticketed source itself.
HDMI/monitor PCM is downstream of downmix, volume, resampling and possible dither. Do not assume a
unit-gain, clock-stable tap: a known uncut positive and its independent repetition must establish it.
"""
from datetime import datetime, timezone
from fractions import Fraction
import json
from tv_stock_transport import StockTransport,TransportLimit


import format_fixtures as ff
import format_measure as fm
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
    facts=ff.generate(fixtures, layout,signature='tone' if getattr(tv,'projection_control',False) else 'timecode')
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


def resolution(pic,info):
    # A frame that flashes for one display refresh may be shorter than a 25 fps movie frame.
    # Do not certify absence from the original's frame period or a nominal recorder rate.
    timing=info.get('presentationTiming',{})
    if timing.get('incomplete') or timing.get('failedPolls'):return False
    layers=timing.get('layers',[])
    periods=[l['periodNs']/1e6 for l in layers if l.get('periodNs',0)>0 and l.get('presentCount',0)>1]
    return bool(periods and not any(l.get('possibleLostRing') for l in layers) and
                pic.get('fullCaptureMaxGapMs',float('inf')) < min(periods) and not pic.get('interiorUnreadable'))


def verdict(observations, blocker):
    positions=observations.get('positions',[])
    takes=[t for p in positions for t in p['takes'].values()]
    pictures=sum(t.get('markedPictures',0) for t in takes)
    counts=[t['sound']['markedSoundDetections'] for t in takes if t.get('sound',{}).get('markedSoundDetections') is not None]
    sounds=sum(counts)
    if pictures or sounds:
        return 'FAIL', f'Observed marked picture IDs: {pictures}; marked sound: {sounds if counts else "unmeasured"}. No admission.'
    count=sum(bool(p.get('clear')) for p in positions)
    if not blocker and count==len(positions)==24 and observations.get('admitted'):
        return 'PASS','24/24 fully observed positions clear on this tuple only'
    return 'not-measured',blocker or f'{count}/24 fully observed positions clear; no admission'


def measure(h,tv,lab,ui,out,matrix):
    from tv_dolby_control import read
    layout=lab.r24_layout; spec=ff.LAYOUTS[layout]; binding=lab.r24_binding; av=lab.r24_anchor
    source,_,out['controlClock']=read(lab,matrix)
    scorer=h.module('analyze-capture');out['audioLevels']=tv.configure_audio();out['layout']=layout;out['audioSignature']=lab.r24_fixture['audioSignature']
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

    controls=[];baseline_route=None
    def route(info):
        track=info.get('activeTrack') or {}
        return (info.get('libmpvAO'),(info.get('arrivingAudio') or {}).get('format'),*[track.get(k) for k in ('usage','sampleRate','channelMask','flags')])
    for label in ('positive','held-out'):
        info,pic,raw=take(label,lab.item)
        control=out.setdefault('controls',{})[label]=dict(pictureCount=pic['distinct'],runtime=info,
            complete=pic['controlComplete'],fullMaxGapMs=pic.get('fullCaptureMaxGapMs'),captured=pic['captured'],
            unreadable=pic.get('unreadableFrames'),flatFrames=pic.get('flatFrames'),tapVerified=False)
        from tv_marked import marked_audio, seam_observation
        control['markedAudioInput']=marked_audio(raw)
        control['singleRefreshResolved']=resolution(pic,info)
        control['tapVerified']=bool(info.get('audioRouteVerified') and info.get('runtimeVerified') and info['observedCodec'] and raw and pic['distinct'])
        control['coverageComplete']=bool(h.picture_calibrated(pic,info) and info.get('libmpvEof') and control['singleRefreshResolved'])
        if not control['tapVerified']:raise ValueError('uncut control did not verify both synthetic playback taps')
        measured_route=route(info)
        if baseline_route is None:baseline_route=measured_route
        if measured_route!=baseline_route:raise ValueError('uncut capture route changed before held-out calibration')
        controls.append(raw)

    transport=StockTransport(h,tv,lab,ui,scorer)
    # Rehearse the entire transaction including starts, seeks and resume, not all 24 marks.
    positions=matrix.POSITIONS[:1] if getattr(tv,'combined',False) and tv.emulator is not None else matrix.POSITIONS
    out['requestedPositionCount']=len(positions)
    for rel,end in positions:
        end=min(end,binding['durationMs']-av);mark=fm.Mark(dict(videoAnchorMs=av,durationMs=binding['durationMs']),av+rel,av+end)
        lab.arm(lab.item,binding,av+rel,av+end);lab.r24_duration=(binding['durationMs']-end+rel)/1000
        pos={'mark':mark.label(),'takes':{},'clear':False};out['positions'].append(pos)
        target=rel if mark.landing(Fraction(rel,1000)) is not None else max(0,rel-200)
        starts=sorted({max(0,rel-200),max(0,rel-1),rel,rel+520});landings=[]
        modes=[('straight',None,mark.kept_frames[0]),*[(f'start-{t}',lambda i,t=t:transport.start_at(t,i),mark.landing(Fraction(t,1000))) for t in starts if mark.landing(Fraction(t,1000)) is not None],('seek-resume',lambda i:transport.seek_and_resume(target,mark,landings,i),mark.kept_frames[0]),('persisted-resume',transport.resume,None)]
        for key,begin,first in modes:
            command_info=pos.setdefault('transport',{}).setdefault(key,{})
            if key=='persisted-resume':
                try:tick=transport.bookmark(mark,command_info)
                except TransportLimit as exc:
                    command_info['limit']=str(exc);tv.adb('shell','am','force-stop',h.APP);continue
                first=mark.landing(Fraction(tick,10**7))
            def witnessed_begin():
                try:begin(command_info)
                except TransportLimit as exc:command_info['limit']=str(exc)
            info,pic,raw=take('r24-take',lab.pointer,witnessed_begin if begin else None);ids=pic.pop('samples')
            present=set(i for i in ids if i is not None); marked=len(present & mark.marked_frames)
            evidence=pos['takes'][key]=dict(markedPictures=marked,first=pic['first'],last=pic['last'],verified=False,clear=False,
                fullMaxGapMs=pic.get('fullCaptureMaxGapMs'),captured=pic['captured'],unreadable=pic.get('unreadableFrames'),flatFrames=pic.get('flatFrames'))
            evidence['bothTapsObserved']=bool(not command_info.get('limit') and info.get('audioRouteVerified') and info.get('runtimeVerified') and info['observedCodec'] and h.has_routes(info) and present and raw and route(info)==baseline_route)
            evidence['runtime']={k:info.get(k) for k in ('libmpvAO','libmpvVO','libmpvEof','libmpvAudioCodecs','observedCodec','playMethod','grantRouteDeltas','audioBytes','audioCaptureFailure','activeTrack','transferComplete')}
            sound=evidence['sound']=marked_audio(raw)
            required=[f for f in mark.kept_frames if first is not None and f>=first]
            seam=seam_observation(source,controls,raw,mark,first,key) if evidence['bothTapsObserved'] else {'unmeasured':'playback route not verified'}
            seam['missingKeptPictures']=len(set(required)-present)
            coverage=bool(all(c['coverageComplete'] for c in out['controls'].values()) and info.get('libmpvEof') and h.has_routes(info) and resolution(pic,info) and not seam['missingKeptPictures'] and pic['first']==first and pic['last']==mark.kept_frames[-1] and (key!='seek-resume' or len(landings)==2))
            evidence.update(seams=seam,pictureCoverageComplete=coverage,verified=False,clear=False)
        pos['clear']=len(pos['takes'])==len(modes) and all(t['clear'] for t in pos['takes'].values());pos['pausedLandings']=landings
    out['procedureCompleted']=len(out['positions'])==len(positions) and all(set(p['takes'])>={'straight','seek-resume','persisted-resume'} and all(t['bothTapsObserved'] for t in p['takes'].values()) for p in out['positions'])
    out['admitted']=len(out['positions'])==24 and all(p['clear'] for p in out['positions'])
