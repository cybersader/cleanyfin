"""Independent controls for the join experiment. Synthetic files only."""
import json
from pathlib import Path
import random
import re
import runpy
import shutil
import subprocess
import sys
import time

import join_lab as j
from exact_audio import kept_pcm, marked_witness, summary
from hard_fixture_lab import FPS, IDENTIFIABLE_CUT, write_identifiable_movie


def emit(test, **metrics):
    print(json.dumps({'test': test, **metrics}, separators=(',', ':')), flush=True)


def probe(w, src, *args):
    return json.loads(j.container(w, j.MEDIA, '/usr/lib/jellyfin-ffmpeg/ffprobe',
                     ['-v','error',*args,'-of','json',src]))


def ideal(raw, a=j.A, b=j.B, size=4):
    return raw[:a*size] + raw[b*size:j.D*size]


def trace(w):
    src = 'source.mp4'
    packets=probe(w,src,'-select_streams','a','-show_packets','-show_entries','packet=pts,pos,size')['packets']
    data=(w/src).read_bytes(); windows=[]
    for i in range(492,496):
        p=packets[i]; bits=f'{int.from_bytes(data[int(p["pos"]):int(p["pos"])+2],"big"):016b}'
        # Fixture-specific: raw_data_block starts with a common-window stereo CPE.
        assert bits[:3]=='001' and bits[7:9]=='10'
        windows.append([i,p['pts'],int(bits[9:11],2)])
    emit('aac-window-sequences',packetIndex_startPCM_sequence=windows)
    for av in (False,True):
        raw=j.play(w,src,av=av); out=j.play(w,'fixture.mpv.edl','--log-file=trace.log','--msg-level=all=trace',av=av)
        lines=(w/'trace.log').read_text().split('switch to segment 1',1)[1].splitlines()
        pts=next(re.search(r' pts=([-.0-9]+)',l).group(1) for l in lines if '[lavf] append packet to audio:' in l)
        ref=ideal(raw); diff=j.diff(ref,out)
        if not av:
            x=memoryview(ref).cast('h'); y=memoryview(out).cast('h')
            # Actual minus expected, both channels, in consecutive order starting at the emitted index.
            diff['startAndSignedErrorLR']=[j.A,[[y[2*n]-x[2*n],y[2*n+1]-x[2*n+1]] for n in range(j.A,diff['lastUnequal']+1)]] if diff['lastUnequal'] is not None else []
        emit('AAC-seek-trace',av=av,firstPostSwitchInputPTS=pts,**diff)
        raw=j.play(w,src,av=av,fmt='float'); out=j.play(w,'fixture.mpv.edl',av=av,fmt='float'); ref=ideal(raw,size=8)
        emit('native-float',av=av,delta=(len(out)-len(ref))//8,unequal=sum(ref[i:i+8]!=out[i:i+8] for i in range(0,min(len(ref),len(out)),8)))
    av_raw=j.play(w,src,av=True); ref=ideal(av_raw)
    for ms in (5280,5440,5480,5520,6000,10000):
        out=j.play(w,'fixture.mpv.edl',f'--start={ms/1000:.3f}',av=True); i=ms*48
        emit('fresh-start',startMs=ms,oneExtraPrecedingSample=(out==ref[(i-1)*4:]),**j.diff(ref[i*4:],out,max(0,j.A-i)))


def split_video(src, kept):
    return '!delay_open,media_type=video,codec=h264,w=1280,h=720,fps=25\n'+kept+'!new_stream\n'


def selection(w):
    src='source.mp4'; base=j.segments(src); ap='!delay_open,media_type=audio,codec=aac,samplerate=48000\n'
    off=4096
    (w/'padded.edl').write_text('# mpv EDL v0\n'+f'{src},{j.seconds(j.B-off)},{j.seconds(j.D-j.B+off)}\n')
    for av in (False,True):
        raw=j.play(w,src,av=av); ref=ideal(raw)
        for label,body in [('nested-padding',f'{src},0,5.48\npadded.edl,{j.seconds(off)},9.48\n'),
                           ('visible-padding',f'{src},0,5.48\n{src},{j.seconds(j.B-off)},{j.seconds(j.D-j.B+off)}\n')]:
            out=j.play(w,j.edl(w,body),av=av);emit(label,av=av,**j.diff(ref,out),tonesMs=j.scorer(w,out))
        a,b=261120,506880  # both 25 fps picture AND 1024-sample AAC packet boundaries
        out=j.play(w,j.edl(w,j.segments(src,a,b)),av=av);emit('joint-grid-aligned',av=av,**j.diff(ideal(raw,a,b),out,a))
    g=j.graph(); split=split_video(src,base)
    out=j.play(w,j.edl(w,split+'!no_clip\n'+ap+src+',0,20\n'),'--lavfi-complex='+g,av=True)
    emit('continuous-audio-only-partition-no-clip',**j.diff(ref,out),tonesMs=j.scorer(w,out))
    raw=j.play(w,src); g="[aid1]atrim=end_sample=960000,aselect='lt(t,5.48)+gte(t,10.52)',asetpts=N/SR/TB[ao]"
    out=j.play(w,src,'--lavfi-complex='+g);emit('aselect-blocks',**j.diff(ideal(raw),out),tonesMs=j.scorer(w,out))


def barcode_scorer():
    sys.path.insert(0,str(j.LAB))
    try: return runpy.run_path(str(j.LAB/'analyze-capture.py'))
    finally: sys.path.pop(0)


def barcode_capture(w, src, label, *opts):
    frames=w/'images';frames.mkdir()
    try:
        pcm=j.play(w,src,'--vo=image','--vf=scale=160:90','--vo-image-format=png',
                   '--vo-image-outdir=images',*opts,av=True)
        m=barcode_scorer(); manifest=m['image_manifest'](frames)
        buf=m['media'](frames,'ffmpeg','-protocol_whitelist','file,pipe','-f','concat','-safe','0','-i','pipe:0',
                       '-vf','scale=160:90:flags=area,format=gray,settb=1/25,setpts=N','-fps_mode','passthrough',
                       '-enc_time_base:v','filter','-threads:v','1','-f','rawvideo','pipe:1',readonly=True,data=manifest)
        step=160*90
        assert len(buf)%step==0
        ids=[m['read_barcode'](buf[n:n+step],500) for n in range(0,len(buf),step)]
        return pcm,ids
    finally: shutil.rmtree(frames)


def joint(w):
    vg='[vid1]split=2[u][v];[u]trim=end_frame=137,setpts=PTS-STARTPTS[m];[v]trim=start_frame=263,setpts=PTS-STARTPTS[n];[m][n]concat=n=2:v=1:a=0[vo]'
    src='source.mp4';ref=None
    for label,inp,opts in [('control',src,()),('EDL','fixture.mpv.edl',()),('continuous-source',src,('--lavfi-complex='+j.graph()+';'+vg,))]:
        out,ids=barcode_capture(w,inp,label,*opts)
        if ref is None:ref=ideal(out)
        want=list(range(500)) if label=='control' else [*range(137),*range(263,500)]
        audio = {} if label == 'control' else j.diff(ref, out)
        tones = j.scorer(w, out)
        emit('joint-'+label,frames=len(ids),marked=sum(i is not None and 137<=i<263 for i in ids),
             exactPictures=ids==want,tonesMs=tones,**audio)
        if label == 'EDL' and not (audio['exact'] and ids == want and tones['880'] == 0):
            raise RuntimeError('Joint AAC EDL result differs from the research result')


def identities(w):
    source = write_identifiable_movie(w, j.D)
    src = 'identifiable.mp4'
    raw, control_ids = barcode_capture(w, src, 'identifiable-control')
    if raw != source or control_ids != list(range(500)):
        raise RuntimeError('Invalid joint FLAC control: did not reproduce all source samples and pictures')
    scale = j.RATE//FPS
    a, b = (i*scale for i in IDENTIFIABLE_CUT)
    rng = random.Random(75)
    many = []
    i = 0
    while i < 499:
        z = min(i+rng.randrange(3,9),500)
        many.append((i*scale, z*scale))
        i = z+rng.randrange(1,6)
    for label,pairs in [('single', [(0,a),(b,j.D)]), ('many', many)]:
        want = kept_pcm(raw, pairs)
        ids = [i for k,z in pairs for i in range(k//scale,z//scale)]

        def body(offset):
            starts = ((k+offset if k else 0, z+offset) for k,z in pairs)
            return ''.join(f'{src},{j.seconds(k)},{j.seconds(z-k)}\n' for k,z in starts)

        base = body(0)
        # Experimental FLAC-only EDL: bias nonzero source boundaries without moving
        # the integer frame/sample grid the cut asks for. Never use this for AAC.
        split = (split_video(src,body(-.25))
                 + '!delay_open,media_type=audio,codec=flac,samplerate=48000\n' + body(.25))
        for candidate,manifest in [('stock', base), ('split-quarter', split)]:
            out,seen = barcode_capture(w,j.edl(w,manifest),label)
            audio = j.diff(want,out,pairs[0][1])
            passed = seen == ids and audio['exact']
            record = dict(verdict='PASS' if passed else 'FAIL', rangeCount=len(pairs),
                          wantedPictures=len(ids), observedPictures=len(seen), picturesExact=seen==ids,
                          wrongPictureIDs=sorted(set(i for i in seen if i is not None)-set(ids)),
                          missingPictureIDs=sorted(set(ids)-set(seen)))
            leak = ''
            if label == 'single':
                witness = marked_witness(raw, out, b-1)
                record.update(markedSourceSample=b-1, markedSampleAtOutput=witness)
                if witness is not None:
                    leak = f'; marked source {b-1} at output {witness}'
            emit('source-ids-'+label+'-'+candidate,**record,**audio)
            print(f'{record["verdict"]}: FLAC {label} {candidate}: {len(seen)}/{len(ids)} pictures '
                  f'({"exact" if seen == ids else "not exact"}); {summary(audio)}{leak}', file=sys.stderr, flush=True)
            if label == 'single' and candidate == 'stock' and not (
                    seen == ids and audio['unequalFrames'] == 1 and audio['firstUnequal'] == a-1
                    and witness == a-1 and audio['delta'] == -1):
                raise RuntimeError('Stock FLAC negative control differs from the research result')
            if candidate == 'split-quarter' and not passed:
                raise RuntimeError(f'Split FLAC {label} control differs from the research result')
    # A small offset in *milliseconds* keeps some marked samples and discards winners, even if a tone scorer misses them.
    base=j.segments(src,a,b);part=split_video(src,base)+'!delay_open,media_type=audio,codec=flac,samplerate=48000\n'
    for shift in (-48,48):
        out=j.play(w,j.edl(w,part+j.segments(src,a+shift,b+shift)),av=True)
        emit('millisecond-offset',offsetSamples=shift,**j.diff(ideal(raw,a,b),out,a))


def timing(w):
    src='long.mp4';j.ff(w,'-stream_loop','15','-i','source.mp4','-f','lavfi','-i','sine=f=735:r=48000:d=304',
        '-t','304','-map','0:v','-map','1:a','-ac','2','-c:a','aac','-b:a','192k','-c:v','copy','-movflags','+faststart',src)
    a,b,d=(n*48000 for n in (2,302,304));base=j.segments(src,a,b,d)
    vg='[vid1]split=2[u][v];[u]trim=end_frame=50,setpts=PTS-STARTPTS[m];[v]trim=start_frame=7550,setpts=PTS-STARTPTS[n];[m][n]concat=n=2:v=1:a=0[vo]'
    g=j.graph(a,b,d).replace('[ao]',',ashowinfo[ao]')
    for case in ('EDL','audio','both'):
        opts=['--lavfi-complex='+g];inputname=src
        if case=='EDL':inputname=j.edl(w,base);opts=['--af=lavfi=[ashowinfo]']
        if case=='audio':inputname=j.edl(w,split_video(src,base)+'!no_clip\n!delay_open,media_type=audio,codec=aac,samplerate=48000\nlong.mp4,0,304\n')
        if case=='both':opts=['--lavfi-complex='+g+';'+vg]
        now=time.monotonic();j.container(w,j.MPV,'mpv',['--no-config','--load-scripts=no','--no-input-terminal','--really-quiet',
            '--vo=null','--ao=null','--framedrop=no','--audio-samplerate=48000','--audio-format=s16','--audio-channels=stereo',
            '--log-file=time.log','--msg-level=all=v,ffmpeg=debug',*opts,inputname]);wall=time.monotonic()-now
        log=(w/'time.log').read_text();rows=[]
        for lt,pt,ns in re.findall(r'^\[\s*([\d.]+)\].*ashowinfo.*?pts_time:([\d.e+\-]+).*?nb_samples:(\d+)',log,re.M):
            rows.append((float(lt),float(pt),int(ns)))
        before=next(x for x in reversed(rows) if x[1]<2);after=next(x for x in rows if x[1]>=2)
        emit('five-minute-'+case,sourceBytes=(w/src).stat().st_size,wallS=round(wall,4),
             prefixLastBlock=before,suffixFirstBlock=after,readyGapMs=round((after[0]-before[0])*1000,2),
             avDesyncWarning='Audio/Video desynchronisation' in log,
             nullAudioEndOrUnderrunWallTimes=[float(t) for t in re.findall(r'^\[\s*([\d.]+)\].*ao/null.*audio end or underrun',log,re.M)])
