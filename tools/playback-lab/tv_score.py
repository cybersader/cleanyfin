"""Presentation oracles for the synthetic TV run. No device or server access."""
from array import array
from itertools import groupby
import math
import sys

RATE=48000
START=137*RATE//25
END=263*RATE//25
TOTAL=20*RATE


def samples(raw):
    out=array('h',raw)
    if sys.byteorder!='little': out.byteswap()
    return out


def picture(video,scorer):
    raw=memoryview(scorer.media(video,'ffmpeg','-i','/lab','-map','0:v:0','-vf',
        f'scale={scorer.SCAN_WIDTH}:{scorer.SCAN_HEIGHT}:flags=area,format=gray,settb=1/25,setpts=N',
        '-fps_mode','passthrough','-enc_time_base:v','filter','-threads:v','1','-f','rawvideo','pipe:1',readonly=True))
    size=scorer.SCAN_WIDTH*scorer.SCAN_HEIGHT
    if len(raw)%size: raise ValueError('Partial captured video frame')
    ids=[scorer.read_barcode(raw[i:i+size],500) for i in range(0,len(raw),size)]
    import json
    ts=json.loads(scorer.media(video,'ffprobe','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json','/lab',readonly=True))['frames']
    if len(ts)!=len(ids): raise ValueError('Capture timestamp count differs from barcode count')
    times=[float(f['best_effort_timestamp_time']) for f in ts]
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('Display capture timestamps are not strictly increasing')
    out=frame_summary(ids);runs=[];i=0
    for ident,group in groupby(ids):
        j=i+sum(1 for _ in group)
        runs.append((ident,i,j));i=j
    groups=[(ident,times[i]) for ident,i,_ in runs if ident is not None]
    if times:out['captureSpanS']=round(times[-1]-times[0],4)
    if len(groups)>1:
        out['witnessSpanS']=round(groups[-1][1]-groups[0][1],4)
        out['joinTiming']={}
        for (a,first,end),(b,next_first,_) in zip(runs,runs[1:]):
            if first and (a,b) in ((136,263),(136,137),(262,263)):
                # Physical changes are bracketed by the previous and current
                # display samples; the first witness time alone is not exact.
                ms=lambda f:round(f*1000,2)
                out['joinTiming'][f'{a}-{b}']={
                    'observedMs':ms(times[next_first]-times[first]),
                    'lowerMs':ms(times[end-1]-times[first]),
                    'upperMs':ms(times[next_first]-times[first-1])}
        out['maxWitnessIntervalMs']=round(max(t2-t1 for (_,t1),(_,t2) in zip(groups,groups[1:]))*1000,2)
        inside=[t for t in times if groups[0][1]<=t<=groups[-1][1]]
        out['maxCaptureGapMs']=round(max(b-a for a,b in zip(inside,inside[1:]))*1000,2)
    return out


def frame_summary(ids):
    readable=[i for i,v in enumerate(ids) if v is not None]
    body=ids[readable[0]:readable[-1]+1] if readable else []
    seq=[n for n,_ in groupby(body)]; seen={n for n in seq if n is not None}
    marked=set(range(137,263)); keep=set(range(500))-marked
    return dict(captured=len(ids),readable=len(readable),interiorUnreadable=body.count(None),
                distinct=len(seen),marked=len(seen&marked),missingKept=sorted(keep-seen),
                missingMarked=sorted(marked-seen),outOfOrder=sum(a>b for a,b in zip(body,body[1:]) if a is not None and b is not None),
                duplicates=len(body)-len(seq),controlComplete=seq==list(range(500)),
                filteredComplete=seq==sorted(keep),joinAdjacent=(136,263) in zip(seq,seq[1:]),
                first=body[0] if body else None,last=body[-1] if body else None)


def audio(pcm,scorer):
    raw=samples(pcm.read_bytes())
    if len(raw)%2: raise ValueError('Partial stereo frame')
    results=[]
    for ch in range(2):
        path=pcm.with_suffix(f'.{ch}.s16le'); vals=raw[ch::2]
        if sys.byteorder!='little': vals.byteswap()
        path.write_bytes(vals.tobytes())
        try: results.append(scorer.analyze_audio(path,500,137,263))
        finally: path.unlink()
    noise=raw[:RATE//10*2]
    return dict(rateHz=RATE,channels=2,sampleFormat='signed 16-bit little endian',frames=len(raw)//2,
                preRollPeakLSB=max(map(abs,noise),default=0),
                preRollRmsLSB=math.sqrt(sum(v*v for v in noise)/max(1,len(noise))),
                capturePeakLSB=[max(map(abs,raw[ch::2]),default=0) for ch in range(2)],
                toneRangesMs=[{f:v['captureRangesMs'] for f,v in result['tonesHz'].items()} for result in results],
                tones=[{f:v['presentMs'] for f,v in result['tonesHz'].items()} for result in results])


def compare(control,filtered,source,noise,filtered_noise=None):
    # Independent capture start is NOT the playback clock. For each take use the
    # same fixed first-onset trigger, then apply one offset learned on control.
    # Never search for an alignment at (or after) the join. Calibrate the uncut
    # path even if no filtered render ever starts.
    ctl=samples(control.read_bytes()); ref=samples(source)
    if len(ref)<TOTAL*2: raise ValueError('Decoded original shorter than fixture')
    def onset(v):return next((i//2 for i in range(0,len(v),2) if max(abs(v[i]),abs(v[i+1]))>=512),None)
    src,pre=map(onset,(ref,ctl)); out={'triggerLSB':512,'noisePeakLSB':noise,'valid':False}
    if None in (src,pre) or noise>=512:
        return dict(out,reason='No usable quiet-to-tone onset at 512 LSB in the decoded source or uncut capture')
    offset=-src; pre+=offset; out.update(offsetFromTriggerSamples=offset,controlStart=pre)
    if pre<0 or (pre+TOTAL)*2>len(ctl):
        return dict(out,reason='Incomplete uncut capture at fixed onset')
    def diff(a,b):
        if len(a)!=len(b): raise ValueError('Unequal comparison length')
        changed=[i//2 for i in range(0,len(a),2) if a[i]!=b[i] or a[i+1]!=b[i+1]]
        return dict(comparedSampleFrames=len(a)//2,differentSampleFrames=len(changed),
                    firstDifferentFrame=changed[0] if changed else None,lastDifferentFrame=changed[-1] if changed else None,
                    differentSamplesLR=[sum(x!=y for x,y in zip(a[ch::2],b[ch::2])) for ch in range(2)],
                    peakErrorLSB=[max((abs(x-y) for x,y in zip(a[ch::2],b[ch::2])),default=0) for ch in range(2)])
    out['controlVsDecoded']=diff(ctl[pre*2:(pre+TOTAL)*2],ref[:TOTAL*2])
    control_exact=out['controlVsDecoded']['differentSampleFrames']==0 and noise==0
    if not control_exact:
        out['reason']='The uncut render is not sample-identical to decoded original at the frozen onset; output-path differences cannot be attributed to the cut'
    if filtered is None:
        out.setdefault('reason','No verified filtered render for fixed-offset comparison')
        return out
    cut=samples(filtered.read_bytes()); post=onset(cut)
    cut_noise=noise if filtered_noise is None else filtered_noise
    if post is None or cut_noise>=512:
        out.setdefault('reason','No usable quiet-to-tone onset at 512 LSB in filtered capture')
        return out
    post+=offset; out['filteredStart']=post
    if post<0 or (post+TOTAL-(END-START))*2>len(cut):
        out.setdefault('reason','Incomplete filtered capture at fixed onset')
        return out
    refcut=ref[:START*2]+ref[END*2:TOTAL*2]; actual=cut[post*2:post*2+len(refcut)]
    for label,lo,hi in [('prefix',0,START),('suffix',START,len(refcut)//2),('join20msEachSide',START-960,START+960)]:
        out[label]=diff(actual[lo*2:hi*2],refcut[lo*2:hi*2])
    out['valid']=control_exact and cut_noise==0
    if not out['valid']:out.setdefault('reason','Filtered pre-roll is not quiet at the fixed onset')
    return out
