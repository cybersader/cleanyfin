"""Describe what Android captured; no desktop packets or calibration cross this boundary."""
import numpy as np
from scipy.signal import fftconvolve

import format_measure as fm
import r24_detector as detector
from tv_pcm import pcm


def marked_audio(raw):
    # A downstream AudioRecord read is not an mpv AO packet. Reuse the actual
    # detector's input check without inventing a source code, threshold or geometry.
    try:detector.require_audio_trace(raw, ())
    except fm.Unmeasured as exc:
        return {'detector':detector.VERSION,'outcome':'unmeasured','reason':str(exc),'clear':False}
    raise RuntimeError('detector unexpectedly accepted missing AO packet geometry')


def landmark(ref,take):
    # Independent local anchors, not a fitted clock. Refuse a competing peak.
    x=np.asarray(ref,dtype=float);y=np.asarray(take,dtype=float);n=len(x)
    if n<480 or len(y)<n or not np.any(x):return None
    dot=sum(fftconvolve(y[:,k],x[::-1,k],mode='valid') for k in range(2))
    energy=np.r_[0,np.cumsum(np.sum(y*y,axis=1))];den=np.sqrt(np.maximum(0,energy[n:]-energy[:-n])*np.sum(x*x))
    r=np.divide(dot,den,out=np.zeros_like(dot),where=den>0);i=int(np.argmax(r));others=np.r_[r[:max(0,i-48)],r[i+49:]]
    if r[i]<.60 or not len(others) or r[i]-others.max()<.10:return None
    return i


def levels(segment):
    from itertools import groupby
    a=np.asarray(segment,dtype=np.int32);quiet=np.all(np.abs(a)<=8,axis=1)
    return {'peakLSB':int(np.abs(a).max()),'maxStepLSB':int(np.abs(np.diff(a,axis=0)).max()),
            'longestQuietFrames':max((sum(1 for _ in g) for q,g in groupby(quiet) if q),default=0)}


def seam_observation(source,controls,raw,mark,first,kind):
    report={'sync':'unmeasured: independent clocks','silence':'unmeasured','pop':'unmeasured',
            'keptSoundLoss':'unmeasured','baseline':'two uncut captures on this route'}
    if kind=='seek-resume' or first is None:return report
    left,right=mark.a,mark.b; x=pcm(source);w=1920
    if left-w<first*fm.SAMPLES_PER_FRAME or right+w>len(x):return report
    def anchors(take):
        y=pcm(take);a=landmark(x[left-w:left],y);b=landmark(x[right:right+w],y)
        return (y,a+w,b) if a is not None and b is not None and a+w<=b+480 else None
    base=[anchors(t) for t in controls];cut=anchors(raw)
    if len(base)!=2 or any(t is None for t in [*base,cut]):return dict(report,limit='no unique pair of local anchors in every take')
    # The cut window and both uncut flanks are observations, not an audible
    # pop verdict or a sample-loss count. Uncut anchors are far apart by design.
    flanks=[]
    for y,a,b in base:
        if not (480<=a<=len(y)-480 and 480<=b<=len(y)-480):return report
        flanks.append({'before':levels(y[a-480:a+480]),'after':levels(y[b-480:b+480]),'anchorSpanErrorFrames':b-a-(right-left)})
    y,a,b=cut
    if not 480<=min(a,b)<=max(a,b)<=len(y)-480:return report
    return dict(report,uncutFlanks=flanks,cutWindow=levels(y[min(a,b)-480:max(a,b)+480]),
                cutLandmarkGapFrames=b-a,rateHz=48000,quietThresholdLSB=8,
                interpretation='level and low-level run observations; not a perceptual defect classification')
