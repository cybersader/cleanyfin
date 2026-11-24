#!/usr/bin/env python3
"""Build a synthetic fragmented MP4 and a reviewed test plan in a NEW directory."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from shutil import copyfile

from hard_fixture_lab import IMAGE  # one pinned media image; see JELLYFIN there
if len(sys.argv) != 2:
    sys.exit('usage: python3 tools/playback-lab/web-fixture.py NEW_OUTPUT_DIRECTORY')
out = Path(sys.argv[1]).resolve()
out.mkdir(mode=0o700, exist_ok=False)
# At 48 kHz, 3.2 s is exactly 150 AAC packets and 80 video frames.
# Use separate browser tests for arbitrary, non-keyframe-aligned boundaries.
subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
    '-v', f'{out}:/out:Z', '--entrypoint', '/usr/lib/jellyfin-ffmpeg/ffmpeg', IMAGE,
    '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i',
    "color=blue:s=320x180:r=25:d=9.6,drawbox=color=red:t=fill:enable='gte(n,80)*lt(n,160)',drawbox=color=lime:t=fill:enable='gte(n,160)'",
    '-f', 'lavfi', '-i', r'aevalsrc=0.25*sin(2*PI*t*if(lt(t\,3.2)\,440\,if(lt(t\,6.4)\,880\,660))):s=48000:d=9.6',
    '-c:v', 'libx264', '-profile:v', 'baseline', '-level:v', '3.0', '-bf', '0', '-g', '80', '-keyint_min', '80', '-sc_threshold', '0',
    '-c:a', 'aac', '-movflags', '+frag_keyframe+empty_moov+default_base_moof', '/out/Fixture.mp4'], check=True)
# Encoder priming shifts the actual presentation timestamps. Use the muxed file,
# not the nominal lavfi timeline, when mapping a seek across the splice.
probe = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none',
    '-v', f'{out}:/out:ro,Z', '--entrypoint', '/usr/lib/jellyfin-ffmpeg/ffprobe', IMAGE,
    '-v', 'error', '-select_streams', 'v:0', '-show_packets', '-show_entries',
    'packet=pts_time,flags', '-of', 'json', '/out/Fixture.mp4'])
keys = [float(p['pts_time']) for p in json.loads(probe)['packets'] if 'K' in p['flags']]
if len(keys) != 3 or abs(keys[2] - keys[1] - 3.2) > 0.00001:
    sys.exit('Unexpected keyframe layout: refusing to create plan')
digest = hashlib.sha256((out/'Fixture.mp4').read_bytes()).hexdigest()
# The packaging index belongs to the fixture, not to the requested edit. Do not
# round an arbitrary user timestamp to whichever keyframe happens to be closest.
(out/'fixture-index.json').write_text(json.dumps({'sha256': digest, 'keyframes': keys}))
(out/'plan.json').write_text(json.dumps({'sha256': digest, 'cut': keys[1:]}))
copyfile(Path(__file__).with_name('web-player.html'), out/'index.html')
for asset in ['web-player.js', 'web-check.js']:
    copyfile(Path(__file__).with_name(asset), out/asset)
print(out)
