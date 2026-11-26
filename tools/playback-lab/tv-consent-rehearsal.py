#!/usr/bin/env python3
"""Observe the stock projection prompt on a fresh private guest, with no media or family data."""
from contextlib import ExitStack
from datetime import datetime, timezone
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from adb_lab_playback import Playback
from tv_combined import CombinedCapture
from tv_internal import Paths


def observe(verify_callback=False):
    spec=importlib.util.spec_from_file_location('tv_guest_harness',HERE/'tv-harness.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    report={'mode':'private-consent-callback' if verify_callback else 'private-consent-observation','consentClicked':False,'playback':False}
    out=HERE.parents[1]/'delivery/evidence/runs'/('tv-consent-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(5));out.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix='cleanyfin-consent-') as w,ExitStack() as stack:
            work=Path(w);sdk=Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk');cap=CombinedCapture(Paths(secrets.token_hex(12)))
            guest=h.TV(work,sdk,stack,'host',1536,2);guest.transport=Playback.guest_session(stack,work,sdk,family=True,receipt=report);guest.boot()
            tv=h.DeviceTV(work,sdk,stack,prefix=guest.prefix,env=guest.env,port=None,capture=cap,emulator=guest);tv.bind(guest.transport)
            if tv.adb('shell','getprop','sys.boot_completed').strip()!=b'1':raise ValueError('no private guest')
            tv.found['emulator']=True  # attach_guest already verified qemu=1, API 36 and x86_64 on this owned child.
            if verify_callback:
                # Only to supply the playback-capture UID. Do not launch or sign in to Wholphin.
                feed=Path(os.environ.get('CLEANYFIN_APK_FEED') or Path.home()/'.cache/cleanyfin-scratch/apks')
                old=feed/'v1.0.9/Wholphin-x86_64.apk'
                h.eventually(lambda:b'package:android' in tv.adb('shell','pm','list','packages').splitlines(),30,'package service not ready')
                if b'Success' not in tv.adb('install','--no-streaming',old,timeout=120):raise ValueError('pinned synthetic UID package not installed')
            tv.internal_state=report;cap.prepare(h,tv,stack);call=tv.transport.projection;call('start');ui=h.DeviceScreen(tv)
            def prompt():
                ns=[n for n in ui.tree() if n.get('package')=='com.android.systemui'];return ns if any(n.get('resource-id')=='android:id/button1' for n in ns) else None
            nodes=h.eventually(prompt,30,'stock prompt not observed')
            # This dedicated fresh guest has no server, account, credentials, or media; only stock system UI is recorded.
            report['systemUi']=[{k:n.get(k) for k in ('class','resource-id','text','content-desc','focused','enabled') if n.get(k)} for n in nodes]
            image=tv.adb('exec-out','screencap','-p',timeout=8)
            if not image.startswith(b'\x89PNG\r\n\x1a\n') or len(image)>10_000_000:raise ValueError('no bounded private screenshot')
            (out/'stock-prompt.png').write_bytes(image)
            if verify_callback:
                cap.approve_private(h,tv);report['consentClicked']=True
                ready=h.eventually(lambda:(r if (r:=call('status').decode().split())[:1]==['ready'] else None),20,'no fresh projection callback')
                if len(ready)!=8 or ready[2:5]!=['48000','2','2']:raise ValueError('capture not initialized after callback')
                report['freshProjectionCallback']=True;report['rateHz']=48000;report['channels']=2;call('finish')
                closed=h.eventually(lambda:(r if (r:=call('status').decode().split())[:1]==['closed'] else None),12,'capture not closed')
                if len(closed)!=9 or closed[1:-1]!=ready[1:]:raise ValueError('callback did not close the same capture')
                report['closed']=True
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
        report['blocker']=type(exc).__name__;raise
    finally:
        if 'work' in locals():report['scratchRemoved']=not work.exists()
        if 'guest' in locals():report['namespaceExited']=guest.ns.poll() is not None;report['emulatorExited']=not hasattr(guest,'emu') or guest.emu.poll() is not None
        (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(out)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-callback',action='store_true');observe(parser.parse_args().verify_callback)
