#!/usr/bin/env python3
"""Run the synthetic browser experiment without a Jellyfin account or a media library."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='cleanyfin-browser-') as temp:
        site = Path(temp) / 'site'
        subprocess.run([sys.executable, Path(__file__).with_name('web-fixture.py'), site], check=True)
        with ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(site))) as server:
            print(f'Open http://127.0.0.1:{server.server_port} and choose Run playback checks. Ctrl-C to stop.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
