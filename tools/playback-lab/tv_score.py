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


def parse_latency(raw):
    """Parse the Android latency dump; only completed actual-present fences count."""
    rows=raw.decode('ascii').splitlines()
    if not rows or not rows[0].strip().isdecimal():
        raise ValueError('No SurfaceFlinger latency data for named layer')
    period=int(rows[0]); actual=[]
    for line in rows[1:]:
        if not line.strip():continue
        fields=line.split()
        if len(fields)!=3 or not all(x.isdecimal() for x in fields):
            raise ValueError('Unrecognized SurfaceFlinger latency row')
        t=int(fields[1])
        if 0<t<2**63-1:actual.append(t)
    return period,set(actual)


def picture(video,scorer,*,include_samples=False):
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
    if include_samples:
        out['samples']=ids
        out['unreadableFrames']=ids.count(None)
        # Black/single-level frames are separate from menus or an undecodable logo.
        out['flatFrames']=sum(len(set(raw[i:i+size]))<=1 for i in range(0,len(raw),size))
        # Include the menu-to-first and last-to-menu windows. An unobserved boundary refresh
        # could contain a marked flash even with a perfect interior cadence.
        if len(times)>1:out['fullCaptureMaxGapMs']=1000*max(b-a for a,b in zip(times,times[1:]))
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
        first,last=next(i for i,n in enumerate(ids) if n is not None),max(i for i,n in enumerate(ids) if n is not None)
        gaps=sorted(((times[i+1]-times[i],i) for i in range(first,last)),reverse=True)
        out['maxCaptureGapMs']=round(gaps[0][0]*1000,2)
        out['largestCaptureGaps']=[{'ms':round(delta*1000,2),'before':ids[i],'after':ids[i+1],
                                   'fromS':times[i],'toS':times[i+1]} for delta,i in gaps[:5]]
        neighbours={i for i,n in enumerate(ids) if n is not None and (132<=n<=137 or 262<=n<=267)}
        out['joinWindow']=[{'frame':ids[i],'ptsS':times[i]} for i in sorted(neighbours)]
        if neighbours:
            window=range(min(neighbours),max(neighbours))
            out['joinMaxCaptureGapMs']=round(max((times[i+1]-times[i] for i in window),default=0)*1000,2)
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


TRIGGER = 512
JOIN_HALF = RATE // 50  # 20 ms
KEPT = TOTAL - (END - START)
REGIONS = {'prefix': (0, START), 'suffix': (START, KEPT),
           'joinBefore20ms': (START-JOIN_HALF, START),
           'joinAfter20ms': (START, START+JOIN_HALF)}


def onset(pcm):
    return next((i//2 for i in range(0,len(pcm),2) if max(abs(pcm[i]),abs(pcm[i+1]))>=TRIGGER),None)


def pcm_diff(a,b):
    if len(a)!=len(b) or len(a)%2 or not a:
        raise ValueError('PCM comparisons need equal, nonempty stereo blocks')
    n=len(a)//2; peak=[0,0]; energy=[0,0]; signal=[0,0]; channels=[0,0]
    first=last=None; changed=0
    for i in range(0,len(a),2):
        if a[i]!=b[i] or a[i+1]!=b[i+1]:
            changed+=1
            if first is None:first=i//2
            last=i//2
        for ch in range(2):
            d=abs(a[i+ch]-b[i+ch]); peak[ch]=max(peak[ch],d)
            energy[ch]+=d*d; signal[ch]+=a[i+ch]**2; channels[ch]+=bool(d)
    return dict(comparedSampleFrames=n, differentSampleFrames=changed,
                differentSamplesLR=channels, differentDurationMs=round(changed*1000/RATE,5),
                firstDifferentFrame=first, lastDifferentFrame=last,
                peakErrorLSB=peak, errorEnergyLSB2=energy,
                errorRmsLSB=[math.sqrt(e/n) for e in energy], referenceRmsLSB=[math.sqrt(e/n) for e in signal])


def compare(control,repeat,filtered,source,noise):
    # Use decoded fixture only to locate its first threshold crossing relative
    # to sample zero. Then fix ONE origin per capture using that same crossing.
    # No waveform reference from the decoded file and no search near the splice.
    ref=samples(source); trigger=onset(ref)
    if len(ref)<TOTAL*2 or trigger is None:raise ValueError('Missing decoded onset or truncated original')
    out=dict(triggerLSB=TRIGGER, offsetFromTriggerSamples=-trigger, noisePeakLSB=noise, starts={}, valid=False)
    def aligned(path,label,frames):
        pcm=samples(path.read_bytes()); t=onset(pcm)
        if len(pcm)%2 or t is None or noise[label]>=TRIGGER or t<trigger:
            raise ValueError(label+': no usable quiet-to-tone onset at 512 LSB')
        start=t-trigger; out['starts'][label]=start
        if (start+frames)*2>len(pcm):raise ValueError(label+': truncated capture at frozen onset')
        return pcm[start*2:(start+frames)*2]
    def kept(pcm):return pcm[:START*2]+pcm[END*2:TOTAL*2]
    def regions(a,b):
        return {key:pcm_diff(a[lo*2:hi*2],b[lo*2:hi*2]) for key,(lo,hi) in REGIONS.items()}
    try:
        a=kept(aligned(control,'control',TOTAL)); b=kept(aligned(repeat,'repeatControl',TOTAL))
        out['repeatability']=regions(a,b)
        if filtered is None:return dict(out,reason='No verified filtered render for the frozen-offset PCM comparison')
        c=aligned(filtered,'filtered',KEPT)
        out['controlVsFiltered']=regions(a,c)
        # Compare against the floor separately on each side. If repeated renders
        # differ more than the reference signal itself, that floor cannot resolve
        # preservation. Above-floor energy is still an observation, not proof of
        # an exact number of samples inserted/deleted by the player.
        for key,floor in out['repeatability'].items():
            obs=out['controlVsFiltered'][key]
            obs['withinFloor']=(obs['differentSampleFrames']<=floor['differentSampleFrames'] and
                all(x<=y for k in ('peakErrorLSB','errorEnergyLSB2') for x,y in zip(obs[k],floor[k])))
            obs['floorBelowSignal']=all(e<sig for e,sig in zip(floor['errorRmsLSB'],floor['referenceRmsLSB']))
    except ValueError as exc:return dict(out,reason=str(exc))
    out['valid']=True
    return out
