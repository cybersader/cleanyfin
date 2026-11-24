"""The private relay must preserve bytes and retain only known fixture events."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import json
import socket
import unittest

spec = spec_from_file_location('tv_wire', Path(__file__).with_name('tv-wire.py'))
tv_wire = module_from_spec(spec)
spec.loader.exec_module(tv_wire)


class TvWireTests(unittest.TestCase):
    def test_progress_and_stop_observe_only_bounded_fixture_facts(self):
        known = '0123456789abcdef0123456789abcdef'
        other = 'ffffffffffffffffffffffffffffffff'
        replies = b'HTTP/1.1 204 No Content\r\n\r\n'

        def request(route, item, ticks, paused):
            body = json.dumps(dict(ItemId=item, PositionTicks=ticks, IsPaused=paused,
                                   Extra='never retain fixture body data')).encode()
            return (f'POST /jellyfin/Sessions/Playing/{route}?do_not_retain=1 HTTP/1.1\r\n'
                    f'Authorization: fixture-only-marker\r\nContent-Length: {len(body)}\r\n\r\n').encode() + body

        def read_exact(peer, expected):
            remaining = len(expected)
            captured = bytearray()
            while remaining:
                part = peer.recv(remaining)
                if not part:
                    break
                captured.extend(part)
                remaining -= len(part)
            self.assertEqual(captured, expected)

        with ExitStack() as stack:
            client, relay_left = map(stack.enter_context, socket.socketpair())
            relay_right, server = map(stack.enter_context, socket.socketpair())
            for peer in (client, relay_left, relay_right, server):
                peer.settimeout(2)
            log = stack.enter_context(redirect_stdout(StringIO()))
            worker = stack.enter_context(ThreadPoolExecutor(1))
            result = worker.submit(tv_wire.observe, relay_left, relay_right, {known: 'pointer'})
            try:
                for packet in (request('Progress', known, 64_000_000, False),
                               request('Stopped', other, 10_000_000, False),
                               request('Stopped', known, 210_000_000, True)):
                    client.sendall(packet)
                    read_exact(server, packet)
                    server.sendall(replies)
                    read_exact(client, replies)
            finally:
                client.shutdown(socket.SHUT_WR)
                server.shutdown(socket.SHUT_WR)
            result.result(timeout=3)
        self.assertEqual([json.loads(line) for line in log.getvalue().splitlines()], [
            dict(route='presentation-event', event='progress', item='pointer',
                 positionTicks=64_000_000, isPaused=False, status=204, reason=None),
            dict(route='presentation-event', event='stopped', item='pointer',
                 positionTicks=None, isPaused=True, status=204, reason=None),
        ])


if __name__ == '__main__':
    unittest.main()
