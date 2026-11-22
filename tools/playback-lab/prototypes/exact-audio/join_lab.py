#!/usr/bin/env python3
"""Compare real mpv PCM on a generated original with an exact source-index splice."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(LAB))
from exact_audio import RATE, compare

MPV = 'cleanyfin-edl-lab:mpv'
MEDIA = 'jellyfin/jellyfin@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db'
A, B, D = 263040, 504960, 960000  # stereo frame indices, not interleaved channel values


def container(w, image, tool, args):
    p = subprocess.run(['docker', 'run', '--rm', '--pull=never', '--network=none',
                        '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp',
                        '-v', f'{w}:/lab:Z', '-w', '/lab', '--entrypoint', tool, image, *args],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
    if p.returncode:
        raise RuntimeError(p.stdout[-3000:].decode(errors='replace'))
    return p.stdout


def play(w, source, *options, av=False, fmt='s16'):
    out = w / 'capture.raw'
    out.unlink(missing_ok=True)
    log = container(w, MPV, 'mpv', ['--no-config', '--load-scripts=no', '--audio-file-auto=no',
        '--sub-auto=no', '--hwdec=no', '--vo=null' if av else '--no-video', '--untimed=yes',
        '--framedrop=no', '--hr-seek=yes', '--ao=pcm', '--ao-pcm-waveheader=no',
        '--ao-pcm-file=capture.raw', '--audio-samplerate=48000', '--audio-channels=stereo',
        '--audio-format='+fmt, '--no-input-terminal', '--really-quiet', *options, '--', source])
    if not out.exists():
        raise RuntimeError('no PCM: '+repr(log[-300:]))
    return out.read_bytes()


def seconds(n):
    return f'{n/RATE:.12f}'.rstrip('0').rstrip('.') or '0'


def segments(src, a=A, b=B, d=D):
    return f'{src},0,{seconds(a)}\n{src},{seconds(b)},{seconds(d-b)}\n'


def edl(w, body):
    (w/'test.edl').write_text('# mpv EDL v0\n'+body)
    return 'test.edl'


def diff(expected, actual, join=A):
    return compare(expected, actual, join)


def scorer(w, pcm):
    with wave.open(str(w/'score.wav'),'wb') as out:
        out.setparams((2,2,RATE,0,'NONE','not compressed')); out.writeframes(pcm)
    r=subprocess.run([sys.executable,'-B',str(LAB/'analyze-capture.py'),'--plan',str(w/'plan.json'),
                      '--audio',str(w/'score.wav'),'--json'],capture_output=True,text=True,timeout=60)
    row=json.loads(r.stdout.splitlines()[-1])['audio']
    return {f:row['tonesHz'][f]['presentMs'] for f in ('440','880','660')}


def ff(w, *args):
    return container(w, MEDIA, '/usr/lib/jellyfin-ffmpeg/ffmpeg', ['-hide_banner','-loglevel','error','-nostdin','-y',*args])


def variants(w):
    # All variants come from the same synthetic tone equation (no lossy cascade).
    tone=f'0.25*sin(2*PI*if(lt(n,{A}),440*n,if(lt(n,{B}),880*(n-{A}),660*(n-{B})))/48000)'
    for codec, opts in [('flac',['-strict','-2','-sample_fmt','s16']),('ac3',['-b:a','384k']),('eac3',['-b:a','384k']),
                        ('libopus',['-b:a','128k']),('dca',['-strict','-2','-b:a','768k']),('truehd',['-strict','-2'])]:
        ff(w,'-i','source.mp4','-f','lavfi','-i',f"aevalsrc='{tone}|{tone}':s=48000:c=stereo:d=20",'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a',codec,*opts,'-movflags','+faststart',codec+'.mp4')


def graph(a=A,b=B,d=D, inp='aid1'):
    return (f'[{inp}]asplit=2[p][s];[p]atrim=end_sample={a},asetpts=PTS-STARTPTS[x];'
            f'[s]atrim=start_sample={b}:end_sample={d},asetpts=PTS-STARTPTS[y];'
            '[x][y]concat=n=2:v=0:a=1,asetpts=N/SR/TB[ao]')


def tests(w, codec, sc=False):
    src = 'source.mp4' if codec=='aac' else codec+'.mp4'
    for av in (False,True):
        raw = play(w,src,av=av)
        ref = raw[:4*A]+raw[4*B:4*D]
        if sc and av:
            print(json.dumps(dict(codec=codec,test='uninterrupted-and-ideal-tone',controlFrames=len(raw)//4,
                  control=scorer(w,raw),ideal=scorer(w,ref))),flush=True)
        if len(raw)<4*D: print(f'{codec}: control ends {D-len(raw)//4} frames before video; compare available PCM',file=sys.stderr)
        def test(name, body=None, opts=(), a=A,b=B, score=False):
            inputname=edl(w,body) if body is not None else src
            out=play(w,inputname,*opts,av=av)
            row=dict(codec=codec, av=av, diagnostic=not av, test=name, controlFrames=len(raw)//4,
                     **diff(raw[:a*4]+raw[4*b:4*D],out,a))
            if sc and score: row['tonesMs']=scorer(w,out)
            print(json.dumps(row,separators=(',',':')),flush=True)
        base=segments(src)
        test('EDL',base,score=True)
        if not av:
            test('continuous-atrim',opts=('--lavfi-complex='+graph(),),score=True)
        if codec in ('ac3','eac3'):
            opt='--ad-lavc-o=cons_noisegen=1'
            configured=play(w,src,opt,av=av)
            out=play(w,edl(w,base),opt,av=av)
            row=dict(test='deterministic-dolby',codec=codec,av=av,diagnostic=not av,
                     controlChangedVsStock=diff(raw,configured),
                     **diff(configured[:4*A]+configured[4*B:4*D],out))
            if sc: row['tonesMs']=scorer(w,out)
            print(json.dumps(row),flush=True)
        if codec=='aac':
            if sc and not av: print(json.dumps(dict(test='uncut-tone-control',tonesMs=scorer(w,raw))),flush=True)
            for label, opt in [('hr-offset','--hr-seek-demuxer-offset=1'),('gapless','--gapless-audio=yes'),('no-initial-sync','--initial-audio-sync=no'),('cache','--cache=yes'),('shared-mov-seek','--demuxer-lavf-o=seek_streams_individually=0'),('single-thread','--ad-lavc-threads=1')]:
                test(label,base,(opt,))
            # Both boundaries on a packet grid; deliberately a different plan, not a fix.
            a,b=(256*1024,496*1024)
            test('aligned-diagnostic',segments(src,a,b),a=a,b=b)
            split='!delay_open,media_type=video,codec=h264,w=1280,h=720,fps=25\n'+base+'!new_stream\n!delay_open,media_type=audio,codec=aac,samplerate=48000\n'
            test('split-same',split+base)
            if av: test('split-continuous-atrim',split+src+',0,20\n',('--lavfi-complex='+graph(),),score=True)
            test('unknown-entry-preroll',base.replace('\n',',preroll=1\n'))


def main():
    sys.path.insert(0,str(HERE))  # -I excludes even this trusted script's directory
    import controls
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--score',action='store_true',help='include existing 5-ms tone scorer on the codec matrix')
    p.add_argument('--suite',choices=['all','codecs','trace','selection','joint','identities','timing','http'],default='all')
    p.add_argument('--http',action='store_true',help='include optional nginx HTTP trial in --suite all')
    args=p.parse_args()
    for img in (MPV,MEDIA):
        dg=subprocess.check_output(['docker','image','inspect','--format={{.Id}}',img],text=True).strip()
        print(json.dumps(dict(test='image',name=img,id=dg)),flush=True)
    with tempfile.TemporaryDirectory(prefix='exact-audio-') as temp:
        w=Path(temp)/'fixture'
        subprocess.run([sys.executable,'-B',str(LAB/'hard-fixture.py'),str(w)],check=True,stdout=sys.stderr)
        print(json.dumps(dict(test='fixture',sha256=hashlib.sha256((w/'source.mp4').read_bytes()).hexdigest(),
            mpv=container(w,MPV,'mpv',['--version']).decode().splitlines())),flush=True)
        if args.suite in ('all','codecs'):
            variants(w)
            for codec in ('aac','flac','ac3','eac3','libopus','dca','truehd'): tests(w,codec,args.score)
        for name in ('trace','selection','joint','identities','timing'):
            if args.suite in ('all',name):getattr(controls,name)(w)
        if args.suite=='http' or (args.suite=='all' and args.http):
            import http_trial
            http_trial.experiment(w)

if __name__=='__main__': main()
