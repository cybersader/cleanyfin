"""Drive a paused desktop test mpv over a scratch IPC socket. No player script required."""
import json
import os
import shutil
import socket
import subprocess
import time

import controls
import join_lab as j


def run(w, namespace, url, opts, label):
    frames=w/'seek-images';frames.mkdir()
    sockpath=w/'seek.sock';sockpath.unlink(missing_ok=True)
    proc=subprocess.check_output(['docker','run','-d','--rm','--pull=never','--network=container:'+namespace,
        '--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp','-v',f'{w}:/lab:z','-w','/lab','--entrypoint=mpv',j.MPV,
        '--no-config','--load-scripts=no','--sub-auto=no','--audio-file-auto=no','--pause','--idle','--keep-open',
        '--hwdec=no','--framedrop=no','--no-input-terminal','--really-quiet','--vo=image','--vo-image-format=png',
        '--vf=scale=160:90','--vo-image-outdir=seek-images','--ao=null','--input-ipc-server=/lab/seek.sock',*opts],text=True).strip()
    result=[]; saved=[]
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            # Avoid the Unix pathname length limit on long worktree paths.
            fd=os.open(w,os.O_RDONLY|os.O_DIRECTORY)
            try:
                for _ in range(100):
                    try:s.connect(f'/proc/self/fd/{fd}/seek.sock');break
                    except (FileNotFoundError,ConnectionRefusedError):time.sleep(.05)
                else:raise RuntimeError('test socket did not become ready')
            finally:os.close(fd)
            s.settimeout(6)
            with s.makefile('rb') as stream:
                for index,(target,cmd) in enumerate([(0,['loadfile',url]),(7,['seek',7,'absolute+exact']),(2,['seek',2,'absolute+exact'])]):
                    old=set(frames.glob('*.png'));s.sendall((json.dumps({'command':cmd,'request_id':index+1})+'\n').encode());events=[]
                    try:
                        deadline=time.monotonic()+6
                        while time.monotonic()<deadline:
                            line=stream.readline()
                            if not line:break
                            e=json.loads(line)
                            if 'event' in e:events.append(e['event'])
                            if e.get('event') in ('playback-restart','end-file'):break
                    except (OSError,TimeoutError):pass
                    fresh=sorted(set(frames.glob('*.png'))-old);saved.append(fresh[-1] if fresh else None)
                    result.append(dict(editedSecond=target,expectedSourceFrame={0:0,7:301,2:50}[target],events=events))
                    if not fresh:break
    finally:
        subprocess.run(['docker','rm','-f',proc],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        sockpath.unlink(missing_ok=True)
    try:
        scorer=controls.barcode_scorer()
        for row,frame in zip(result,saved):
            if frame is None:row['sourceFrame']=None;continue
            pixels=scorer['media'](frame,'ffmpeg','-i','/lab','-vf','scale=160:90:flags=area,format=gray','-frames:v','1',
                                   '-threads:v','1','-f','rawvideo','pipe:1',readonly=True)
            row['sourceFrame']=scorer['read_barcode'](pixels,500)
        controls.emit('HTTP-seek-'+label,landings=result)
    finally:shutil.rmtree(frames)
