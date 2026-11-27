#!/usr/bin/env python3
"""Offline challenge, full-length fixture and pinned Dolby codecs. Prints counts, not PCM.
Pass an existing private scratch directory; nothing other than a temporary empty
Docker mount is persisted. No device, network, movie, eligibility or threshold fitting.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from format_fixtures import AUDIO, SOURCE_SAMPLES
from hard_fixture_lab import media, IMAGE
from tv_sound import Witness, source_pcm, android_mix, VERSION, RESOLUTION


def drift(x,ppm):
    t=np.arange(0,len(x)-.5,1+ppm/1e6)
    return np.clip(np.stack([np.interp(t,np.arange(len(x)),lane) for lane in x.T],axis=1).round(),-32768,32767).astype('<i2')


def challenge(work,codec):
    # Independent default FFmpeg stereo reference and alternative playback mix
    # derived from all six *decoded* lanes. Quantize only after the alternative
    # channel matrix. Hold fixture and thresholds fixed for both codecs.
    def ff(data,*args):return media(work,'ffmpeg',*args,readonly=True,data=data)
    encoded=ff(source_pcm(SOURCE_SAMPLES).tobytes(),'-f','f32le','-ar','48000','-ch_layout','5.1(side)','-i','pipe:0',
               '-c:a',codec,*AUDIO[codec][1],'-f',codec,'pipe:1')
    decode=('-cons_noisegen','1','-f',codec,'-i','pipe:0')
    ref=np.frombuffer(ff(encoded,*decode,'-ac','2','-f','s16le','pipe:1'),'<i2').reshape(-1,2)
    six=np.frombuffer(ff(encoded,*decode,'-f','f32le','pipe:1'),'<f4').reshape(-1,6)
    assert len(ref)==len(six)
    mat=np.array([[1,0],[0,1],[.707,.707],[.5,.5],[.707,0],[0,.707]])
    alt=six@mat*np.array([.61,.43])*32768
    prediction=android_mix(six)
    a,b=240019,570031; n=len(ref); witness=Witness(prediction); rng=np.random.default_rng(75107 if codec=='ac3' else 61843)
    tests={}; all_ok=True
    # Each synthetic 75ms take includes one substituted marked interval. At
    # 'join' the underlying kept sound also jumps across the removed interval.
    # Advance the phase and source position on every repetition.
    for frames in (48,120,240,360,480,960):
        by_place={};tests[str(frames)] = by_place
        for place in ('start','join','interior','end','left-flank','right-flank'):
            count={'fail':0,'unmeasured':0,'pass':0}
            for r in range(12):
                left=1100+int(rng.integers(0,120)); sample=int(rng.integers(a+240,b-frames-240))
                if place in ('left-flank','right-flank'):
                    # An under-cut: extra marked audio contiguous with either kept flank.
                    base=(np.concatenate([alt[a-left:a+frames],alt[b:b+3600-left]]) if place=='left-flank' else
                          np.concatenate([alt[a-left:a],alt[b-frames:b+3600-left]]))
                elif place=='join':base=np.concatenate([alt[a-left:a],alt[b:b+3600-left]])
                else:
                    ix=int(rng.integers(1920,a-6000));base=alt[ix:ix+3600].copy()
                if not place.endswith('-flank'):
                    at={'start':0,'join':left,'interior':left+380,'end':len(base)-frames}[place]
                    base[at:at+frames]=alt[sample:sample+frames]
                y=drift(base,(-400,0,400)[r%3]);v=witness.score(y.tobytes(),(a,b))
                count[v['outcome']]+=1
            by_place[place]=count
            if frames>=RESOLUTION:all_ok &= count['fail']==12
    negatives={};ideal=np.clip(np.rint(prediction*.72),-32768,32767).astype('<i2'); kept=np.concatenate([ideal[:a],ideal[b:]])
    # An independently downmixed and quantized kept capture. It is not the
    # reference used by the index. The absolute residual still gates clearance.
    altcut=np.concatenate([alt[:a],alt[b:]])
    for label,buf in [('ideal',kept),('heldOut',drift(altcut,0)),('ffmpegDefault',np.concatenate([ref[:a],ref[b:]])),('heldOutSlow',drift(altcut,-400)),('heldOutFast',drift(altcut,400))]:
        v=witness.score(buf.tobytes(),(a,b)); negatives[label]={k:v[k] for k in ('blocks','keptBlocks','unidentifiedBlocks','markedSoundDetections','continuousCoverage','maxKeptResidualLSB','blockOutcome','outcome','reason')}
        all_ok &= v['markedSoundDetections']==0
        if label=='ideal':all_ok &= v['outcome']=='pass'
    return dict(passed=bool(all_ok),injected=tests,negative=negatives,sourceFrames=n)


def muxed(work,codec):
    """Exercise the exact fixture, probe, clock and render-reference code the TV harness uses."""
    import importlib.util
    from types import SimpleNamespace
    from format_fixtures import generate
    from tv_sound import route_challenge
    from tv_dolby_control import read, playback_mix_reference
    spec=importlib.util.spec_from_file_location('format_matrix',Path(__file__).with_name('format-matrix.py'))
    matrix=importlib.util.module_from_spec(spec);spec.loader.exec_module(matrix)
    layout={'ac3':'mkv-h264-ac351','eac3':'mp4-h264-eac351'}[codec]
    facts=generate(work,layout,signature=VERSION);lab=SimpleNamespace(fixtures=work,r24_layout=layout)
    _,whole,clock=read(lab,matrix);mix,sha=playback_mix_reference(lab,len(whole)//4);witness=Witness(mix)
    controls={};counts={}
    for name,g in [('positive',.64),('held-out',.47)]:
        raw=np.rint(mix*g).astype('<i2').tobytes();row=witness.score(raw,preroll=clock['discardedBeforeOrigin'])
        controls[name]=(raw,{'soundControl':row,'soundCalibrated':row['clear']});counts[name]=[row['blocks'],row['unidentifiedBlocks'],row['outcome']]
    a,b=(t*48+clock['discardedBeforeOrigin'] for t in (5480,10520));checks=route_challenge(witness,controls,(a,b))
    cut=np.rint(np.concatenate([mix[:a],mix[b:]])*.57).astype('<i2');row=witness.score(cut.tobytes(),(a,b),preroll=clock['discardedBeforeOrigin'])
    # Unlike the small coefficient rounding in the leak battery, attenuate
    # centre and surrounds substantially. This can still identify positions
    # without explaining the entire kept waveform. Abstention is legitimate.
    raw51=media(work,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+facts['file'],'-map','0:a:0',
                '-ch_layout','5.1(side)','-ar','48000','-f','f32le','pipe:1',readonly=True)
    six=np.frombuffer(raw51,'<f4').reshape(-1,6); assert len(six)==len(mix)
    other=six@np.array([[1,0],[0,1],[.50,.50],[0,0],[.35,0],[0,.35]])*32768*np.array([.62,.51])
    alternate=[]
    for ppm in (-400,400):
        r=witness.score(drift(np.concatenate([other[:a],other[b:]]),ppm).tobytes(),(a,b),preroll=clock['discardedBeforeOrigin'])
        alternate.append(dict(ppm=ppm,**{k:r[k] for k in ('blocks','keptBlocks','unidentifiedBlocks','markedSoundDetections','blockOutcome','outcome','maxKeptResidualLSB')}))
    return dict(passed=bool(checks['passed'] and row['clear'] and all(r['markedSoundDetections']==0 for r in alternate)),
                fixtureSha256=facts['sha256'],decoded51Sha256=sha,clock=clock,controlCounts=counts,challenge=checks,
                alternateFold=alternate,outcome=row['outcome'],reason=row['reason'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('scratch',type=Path);p.add_argument('--codec',choices=('ac3','eac3','both'),default='both')
    p.add_argument('--muxed',action='store_true',help='test actual MP4/MKV fixture and TV source clocks instead of leak battery');args=p.parse_args()
    with tempfile.TemporaryDirectory(dir=args.scratch) as d:
        results={c:(muxed if args.muxed else challenge)(Path(d),c) for c in (('ac3','eac3') if args.codec=='both' else (args.codec,))}
    print(json.dumps(dict(witness=VERSION,image=IMAGE,results=results),indent=2));sys.exit(not all(r['passed'] for r in results.values()))
