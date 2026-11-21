#!/usr/bin/env python3
"""Replay the Jellyfin Android TV 0.20.0 segment-type filter. Disposable loopback lab only."""
import argparse
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # Never forward lab credentials elsewhere.


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', required=True, type=int)
parser.add_argument('--item', required=True, help='32-hex ID of a fixture with a known Unknown segment')
args = parser.parse_args()
if not 1 <= args.port <= 65535 or not re.fullmatch('[0-9a-fA-F]{32}', args.item):
    parser.error('Expected a valid loopback port and 32-hex item ID')
base = f'http://127.0.0.1:{args.port}'
opener = build_opener(NoRedirect())
headers = {'Authorization': 'MediaBrowser Client="Cleanyfin Lab", Device="Fixture", DeviceId="android-query", Version="1"', 'Content-Type': 'application/json'}
with opener.open(Request(base+'/Users/AuthenticateByName', data=b'{"Username":"Viewer","Pw":""}', headers=headers), timeout=10) as response:
    token = json.load(response)['AccessToken']


def query(types):
    url = base+'/MediaSegments/'+args.item+'?'+urlencode({'includeSegmentTypes': types}, doseq=True)
    with opener.open(Request(url, headers={'X-Emby-Token': token}), timeout=10) as response:
        return json.load(response)['Items']


try:
    # Pinned from MediaSegmentRepository.SupportedTypes; this probes the API,
    # not the television's installed binary or its rendered output.
    marks = query(['Unknown'])
    if not marks or any(m['Type'] != 'Unknown' for m in marks):
        raise SystemExit('INVALID FIXTURE: the positive control did not return the seeded Unknown marks')
    android = query(['Intro', 'Outro', 'Preview', 'Recap', 'Commercial'])
    ids = {m['Id'] for m in marks}
    returned = sum(m['Id'] in ids for m in android)
    print(f'Explicit Unknown query: {len(marks)} Cleanyfin-style mark(s). Android TV 0.20.0 query: {returned} of those marks.')
    if returned:
        raise SystemExit('Result differs from the expected type-filter behavior; recheck the client contract.')
finally:
    with opener.open(Request(base+'/Sessions/Logout', data=b'', headers={'X-Emby-Token':token}), timeout=10):
        pass
