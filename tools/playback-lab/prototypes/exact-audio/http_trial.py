"""Optional control: real HTTP EDL and source on loopback inside a no-network namespace."""
import json
import os
from pathlib import Path
import subprocess
import time

import join_lab as lab


def experiment(w):
    image = subprocess.check_output(['docker','image','inspect','--format={{.Id}}','nginx:alpine'], text=True).strip()
    # Mount only files generated for this trial, never the repo or a server config.
    public = w/'public'; public.mkdir()
    (public/'source.mp4').hardlink_to(w/'source.mp4')
    url = 'http://127.0.0.1:18080/source.mp4'
    esc = '%'+str(len(url.encode()))+'%'+url
    (public/'list.edl').write_text('# mpv EDL v0\n'+lab.segments(esc))
    conf = w/'nginx.conf'; conf.write_text('''
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 32; }
http { access_log /dev/stdout; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy;
fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi;
server { listen 127.0.0.1:18080; root /clips; }
}
''')
    cid = subprocess.check_output(['docker','run','-d','--rm','--pull=never','--network=none',
        '--user',f'{os.getuid()}:{os.getgid()}','-v',f'{public}:/clips:ro,z','-v',f'{conf}:/lab.conf:ro,z',
        '--entrypoint=nginx',image,'-c','/lab.conf','-g','daemon off;'], text=True).strip()
    try:
        for _ in range(25):
            r=subprocess.run(['docker','exec',cid,'wget','-q','--spider','http://127.0.0.1:18080/list.edl'],check=False)
            if not r.returncode: break
            time.sleep(0.1)
        else: raise RuntimeError('fixture HTTP server unavailable')
        def fetch(src,opts=(),av=True):
            out=w/'http.raw';out.unlink(missing_ok=True)
            p=subprocess.run(['docker','run','--rm','--pull=never','--network=container:'+cid,
                '--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp','-v',f'{w}:/lab:z','-w','/lab',
                '--entrypoint=mpv',lab.MPV,'--no-config','--load-scripts=no','--audio-file-auto=no','--sub-auto=no',
                '--vo=null' if av else '--no-video','--untimed=yes','--framedrop=no','--hwdec=no','--ao=pcm',
                '--ao-pcm-waveheader=no','--audio-samplerate=48000','--audio-format=s16','--audio-channels=stereo',
                '--ao-pcm-file=http.raw','--no-input-terminal','--really-quiet',*opts,'--',src],timeout=60,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if p.returncode or not out.exists():return None
            return out.read_bytes()
        ref0=fetch(url);ref=ref0[:4*lab.A]+ref0[4*lab.B:4*lab.D]
        actual=fetch('http://127.0.0.1:18080/list.edl');
        if actual is None: raise RuntimeError('HTTP EDL produced no PCM')
        print(json.dumps(dict(test='HTTP-EDL',nginxImage=image,**lab.diff(ref,actual),tonesMs=lab.scorer(w,actual))),flush=True)
        # Negative control: does requesting a forward-only HTTP reader really give
        # EDL decode-only preroll? Never assume a failed seek yields correct media.
        sequential=('--stream-lavf-o=seekable=0','--demuxer-seekable-cache=no')
        for label,opts,av in [('stock',(),False),('no-seek-cache',(sequential[1],),False),
                              ('forward-reader',(sequential[0],),False),('forward-only',sequential,False),
                              ('forward-only-av',sequential,True)]:
            original=fetch(url,opts,av)
            out=fetch('http://127.0.0.1:18080/list.edl',(*opts,'--log-file=http.log','--msg-level=all=trace'),av)
            row={'test':'HTTP-'+label,'av':av,'usable':original is not None and out is not None}
            if row['usable']:
                row.update(lab.diff(original[:4*lab.A]+original[4*lab.B:4*lab.D],out),tonesMs=lab.scorer(w,out))
                log=(w/'http.log').read_text().split('switch to segment 1')[-1]
                row['firstLavfAudioAfterSwitch']=next((l.split('pts=')[1].split()[0] for l in log.splitlines() if '[lavf] append packet to audio:' in l),None)
                row['seekRefused']='Cannot seek in this file' in log
            print(json.dumps(row),flush=True)
        import seek_control
        for label,opts in [('ordinary',()),('forward-only',sequential)]:
            seek_control.run(w,cid,'http://127.0.0.1:18080/list.edl',opts,label)
        access=subprocess.check_output(['docker','logs',cid],stderr=subprocess.STDOUT,text=True)
        print(json.dumps(dict(test='HTTP-requests',edlGet=access.count('GET /list.edl'),sourceGet=access.count('GET /source.mp4'),partialContent=access.count(' 206 '))),flush=True)
    finally:
        subprocess.run(['docker','rm','-f',cid],stdout=subprocess.DEVNULL,check=True)
