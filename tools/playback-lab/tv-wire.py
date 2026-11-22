#!/usr/bin/env python3
"""TV lab loopback relay. Forward bytes unchanged; emit only bounded PlaybackInfo facts."""
import collections
import json
import re
import select
import socket
import socketserver
import sys
import zlib


LIMIT = 128 * 1024


class Messages:
    def __init__(self, start, finish):
        self.start, self.finish = start, finish
        self.buf = bytearray()
        self.state = 'head'
        self.left = 0
        self.meta = None
        self.body = bytearray()

    def feed(self, data):
        if self.state == 'disabled':
            return
        self.buf.extend(data)
        try:
            self.parse()
        except (ValueError, IndexError, KeyError, TypeError, AttributeError):
            # Observation errors must not affect the traffic.
            self.state = 'disabled'
            self.buf.clear()
            self.body.clear()

    def complete(self):
        if self.meta is not None:
            self.finish(self.meta, self.body)
        self.body.clear()
        self.state = 'head'

    def parse(self):
        while self.buf:
            if self.state in ('head', 'chunk', 'trailer'):
                sep = b'\r\n\r\n' if self.state == 'head' else b'\r\n'
                end = self.buf.find(sep)
                if end < 0:
                    if len(self.buf) > LIMIT:
                        raise ValueError('HTTP metadata too large')
                    return
                line = bytes(self.buf[:end])
                del self.buf[:end+len(sep)]
                if self.state == 'trailer':
                    if not line:
                        self.complete()
                elif self.state == 'chunk':
                    self.left = int(line.split(b';', 1)[0], 16)
                    self.state = 'chunk-data' if self.left else 'trailer'
                else:
                    first, *rows = line.split(b'\r\n')
                    headers = {k.lower(): v.strip() for k, v in (r.split(b':', 1) for r in rows)}
                    self.meta, no_body = self.start(first, headers)
                    self.left = int(headers.get(b'content-length', b'0'))
                    if no_body:
                        self.complete()
                    elif b'chunked' in headers.get(b'transfer-encoding', b'').lower():
                        self.state = 'chunk'
                    elif self.left > 0:
                        self.state = 'fixed'
                    else:
                        self.complete()
            elif self.state == 'chunk-end':
                if len(self.buf) < 2:
                    return
                if self.buf[:2] != b'\r\n':
                    raise ValueError('malformed chunk')
                del self.buf[:2]
                self.state = 'chunk'
            else:
                n = min(self.left, len(self.buf))
                if self.meta is not None and len(self.body) < LIMIT:
                    self.body.extend(self.buf[:min(n, LIMIT-len(self.body))])
                del self.buf[:n]
                self.left -= n
                if self.left == 0:
                    if self.state == 'fixed':
                        self.complete()
                    else:
                        self.state = 'chunk-end'


def terse(value):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.+ ,/-]{1,100}', value) else None


def payload(body, encoding):
    try:
        if encoding.lower()==b'gzip':
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            body=decoder.decompress(body,LIMIT)
            if not decoder.eof:return {}
        elif encoding.lower() not in (b'', b'identity'):
            return {}
        value = json.loads(body)
        return {k.lower():v for k,v in value.items()} if isinstance(value, dict) else {}
    except (ValueError, zlib.error):
        return {}


def route(first, ids):
    verb, url, *_ = first.split()
    path, _, query = url.decode('ascii').partition('?')
    match = re.search(r'/Items/([0-9a-fA-F-]+)/PlaybackInfo$', path)
    if verb == b'POST' and match:
        source = ids.get(match[1].replace('-', '').lower())
        if source:
            return {'route':source, 'queryPresent':bool(query)}
    return None


def observe(left, right, ids):
    sent = collections.deque(maxlen=32)

    def request_start(first, heads):
        meta = route(first, ids)
        if meta is None:
            sent.append((first.startswith(b'HEAD '), None))
        return (meta, heads.get(b'content-encoding', b'')) if meta else None, False

    def request_finish(meta, body):
        facts, encoding = meta
        dto = payload(body,encoding)
        p = {k.lower():v for k,v in (dto.get('deviceprofile') or {}).items()}
        direct=[{k.lower():v for k,v in profile.items()} for profile in p.get('directplayprofiles') or []]
        video={fmt for r in direct if str(r.get('type')).lower()=='video' for fmt in (r.get('container') or '').split(',')}
        facts.update(profile=terse(p.get('name')), containerConditions=len(p.get('containerprofiles') or []),
                     codecConditions=len(p.get('codecprofiles') or []), transcodeProfiles=len(p.get('transcodingprofiles') or []),
                     videoMp4='mp4' in video, videoMov='mov' in video)
        for key in ('audioStreamIndex', 'subtitleStreamIndex', 'maxAudioChannels', 'maxStreamingBitrate', 'enableDirectPlay', 'enableDirectStream', 'enableTranscoding'):
            value = dto.get(key.lower())
            facts[key] = value if isinstance(value, (int, bool)) else None
        selected = dto.get('mediasourceid')
        facts['selectedSource'] = (ids.get(selected.replace('-', '').lower(), 'other') if isinstance(selected,str) else None)
        sent.append((False, facts))

    def response_start(first, heads):
        status = int(first.split()[1])
        if 100 <= status < 200:
            return None, True
        head, facts = sent.popleft() if sent else (False, None)
        if facts is not None:
            facts.update(status=status, reason=terse(heads.get(b'x-cleanyfin-reason', b'').decode('ascii')))
        return ((facts, heads.get(b'content-encoding', b'')) if facts else None, head or status in (204,304))

    def response_finish(meta, body):
        facts, encoding = meta
        dto = payload(body,encoding)
        facts['jsonObserved']=bool(dto)
        facts['error'] = terse(dto.get('errorcode'))
        sources = dto.get('mediasources') or []
        facts['sourceCount'] = len(sources) if dto else None
        if len(sources) == 1 and isinstance(sources[0], dict):
            s = {k.lower():v for k,v in sources[0].items()}
            facts['media'] = dict(container=terse(s.get('container')), protocol=terse(s.get('protocol')),
                                  directPlay=s.get('supportsdirectplay') is True, transcodeUrl=bool(s.get('transcodingurl')),
                                  transcode=s.get('supportstranscoding') is True, remote=s.get('isremote') is True)
        print(json.dumps(facts), flush=True)

    links = {left:(right,Messages(request_start,request_finish)),
             right:(left,Messages(response_start,response_finish))}
    while links:
        readable, _, _ = select.select(list(links), [], [])
        for src in readable:
            dst, parser = links[src]
            data = src.recv(65536)
            if not data:
                dst.shutdown(socket.SHUT_WR)
                del links[src]
            else:
                parser.feed(data)
                dst.sendall(data)


class Relay(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.settimeout(45)
                upstream.connect(self.server.path)
                self.request.settimeout(45)
                observe(self.request, upstream, self.server.ids)
        except OSError:
            pass


if __name__ == '__main__':
    path, original, pointer = sys.argv[1:]
    with socketserver.ThreadingTCPServer(('127.0.0.1',18096), Relay) as server:
        server.path = path
        server.ids = {original:'original', pointer:'pointer'}
        server.serve_forever()
