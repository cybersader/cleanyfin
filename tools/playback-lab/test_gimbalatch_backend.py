from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import logging
from pathlib import Path
import socket
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch

import drive_look as drive
from gimbalatch_backend import CREDENTIALS, GimbalatchBackend, GimbalatchError, MAX_JPEG


TOKEN = 'synthetic-test-bearer-never-a-real-credential'
LOGIN = 'fake-owner@example.invalid'
HANDLE = 'synthetic-opaque-session-handle'
JPEG = b'\xff\xd8synthetic-jpeg-payload\xff\xd9'
CONTROLS = {'pan': {'min': -100, 'max': 100, 'step': 1, 'driver_value': 0}}


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        server.calls.append((self.path, self.rfile.read(int(self.headers['Content-Length'])), dict(self.headers)))
        status, content_type, body = server.replies.pop(0)
        if status is None:  # Bind may have been consumed despite a lost response.
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if status == 307:
            self.send_header('Location', '/unexpected-redirect-target')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if len(body) <= MAX_JPEG:
            self.wfile.write(body)


class GimbalatchTest(unittest.TestCase):
    def setUp(self):
        server = self.server = HTTPServer(('127.0.0.1', 0), FakeHandler)
        self.addCleanup(server.server_close)
        self.assertNotEqual(server.server_port, 8769)
        server.calls, server.replies = [], []
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        folder = Path(self.enter_tempdir())
        home = patch('gimbalatch_backend.Path.home', return_value=folder)
        home.start()
        self.addCleanup(home.stop)
        self.creds = folder / CREDENTIALS
        self.creds.parent.mkdir(parents=True)
        self.origin = f'http://127.0.0.1:{server.server_port}'
        self.credentials()

    def enter_tempdir(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return temp.name

    def credentials(self, **overrides):
        self.creds.write_text(json.dumps(dict(origin=self.origin, gateway_token=TOKEN, serve_login=LOGIN) | overrides))
        self.creds.chmod(0o600)

    def reply(self, body, status=200, content_type='application/json'):
        self.server.replies.append((status, content_type, body))

    def bound(self):
        self.reply({'session_handle': HANDLE, 'tracking': 'not changed; probe only'})

    def ended(self):
        self.reply({'session': 'ended'})

    def assert_routes(self, *actions):
        self.assertEqual([path for path, _, _ in self.server.calls], [f'/v1/{a}' for a in actions])
        for action, (_, body, headers) in zip(actions, self.server.calls):
            self.assertEqual(body, b'{}')
            self.assertEqual(headers['Content-Type'], 'application/json')
            self.assertEqual(headers['Authorization'], f'Bearer {TOKEN}')
            self.assertEqual(headers['Tailscale-User-Login'], LOGIN)
            self.assertNotIn('Origin', headers)
            self.assertEqual(headers.get('X-Camera-Session'), None if action == 'bind' else HANDLE)

    def run_cli(self, *args):
        std, err = io.StringIO(), io.StringIO()
        with redirect_stdout(std), redirect_stderr(err):
            status = drive.main(['--camera', 'gimbalatch', *args])
        return status, std.getvalue(), err.getvalue()

    def test_read_only_probe_cli_binds_probes_once_and_releases(self):
        self.bound()
        self.reply({'controls': CONTROLS})
        self.ended()
        with patch.dict('os.environ', {'HTTP_PROXY': 'http://does-not-exist.invalid:99'}):
            status, output, errors = self.run_cli('--probe')
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output), {'camera': 'gimbalatch', 'controls': CONTROLS})
        self.assert_routes('bind', 'probe', 'end')

    def test_frame_smoke_does_not_retain_or_print_pixels(self):
        self.bound()
        self.reply(JPEG, content_type='image/jpeg')
        self.ended()
        out = self.creds.parent / 'must-not-exist'
        status, output, errors = self.run_cli('--capture', '--out', str(out))
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output), {'camera': 'gimbalatch', 'frame_bytes': len(JPEG),
                                              'images_retained': False})
        self.assertFalse(out.exists())
        self.assert_routes('bind', 'frame', 'end')

    def test_navigation_uses_frame_in_memory_and_releases_before_adb_screen(self):
        self.bound()
        self.reply(JPEG, content_type='image/jpeg')
        self.ended()
        out = self.creds.parent / 'must-not-exist'
        events = []

        def capture(cmd, *, input=None):
            if cmd[0] == 'ffmpeg':
                self.assertEqual(input, JPEG)
                self.assertIn('crop=30:40:10:20:exact=1', cmd)
                self.assert_routes('bind', 'frame', 'end')
                events.append('crop')
            elif cmd[3] == 'shell':
                self.assert_routes('bind')
                events.append('key')
            else:
                events.append('screen')
            return drive.DryRun().grab()

        with patch.object(drive, 'capture', side_effect=capture):
            status, output, errors = self.run_cli('BACK', '--target', '192.0.2.1:30000', '--crop', '10,20,30,40',
                                                  '--out', str(out), '--settle', '0')
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(events, ['key', 'crop', 'screen'])
        self.assertIs(json.loads(output)['images_retained'], False)
        self.assertFalse(out.exists())
        self.assert_routes('bind', 'frame', 'end')

    def test_no_pending_grant_waits_for_owner_without_sending_tv_key(self):
        self.reply({'error': 'no unclaimed owner grant for this recipient'}, 403)
        with patch.object(drive, 'capture') as capture:
            status, output, errors = self.run_cli('BACK', '--target', '192.0.2.1:30000', '--crop', '0,0,100,100')
        self.assertEqual((status, output), (2, ''))
        self.assertIn('waiting for owner approval', errors)
        capture.assert_not_called()
        self.assert_routes('bind')

    def test_expiry_and_wrong_scope_are_explained_without_inventing_distinction(self):
        for reason, message in [('session ended; frame withheld', 'expired or was revoked'),
                                ('active scoped camera session required', 'wrong scope or the owner grant expired'),
                                ('approved preset must be selected in this session', 'this client does not aim')]:
            with self.subTest(reason=reason):
                self.server.calls.clear()
                self.bound()
                self.reply({'error': reason}, 403)
                self.ended()
                status, output, errors = self.run_cli('--capture')
                self.assertEqual((status, output), (2, ''))
                self.assertIn(message, errors)
                self.assert_routes('bind', 'frame', 'end')

    def test_bind_auth_conflict_precondition_rate_limit_and_uncertain_failure(self):
        for status, reason, expected in [(403, 'gateway identity required', 'authentication refused'),
                                         (409, 'already bound', 'cleanup uncertain'),
                                         (412, 'driver position changed', 'tracking safety check failed'),
                                         (429, 'rate or count', 'rate or count limit'),
                                         (503, 'unknown', 'outcome unknown; do not retry')]:
            with self.subTest(status=status):
                self.server.calls.clear()
                self.reply({'error': reason}, status)
                code, output, error = self.run_cli('--probe')
                self.assertEqual((code, output), (2, ''))
                self.assertIn(expected, error)
                self.assert_routes('bind')

    def test_service_down(self):
        # An exclusively bound but unlistening loopback socket gives ECONNREFUSED.
        with socket.socket() as unlistening:
            unlistening.bind(('127.0.0.1', 0))
            port = unlistening.getsockname()[1]
            self.assertNotEqual(port, 8769)
            self.credentials(origin=f'http://127.0.0.1:{port}')
            status, output, errors = self.run_cli('--probe')
        self.assertEqual((status, output), (2, ''))
        self.assertIn('service is down or not listening', errors)
        self.assert_routes()

    def test_lost_bind_is_not_replayed(self):
        self.reply({}, None)
        backend = GimbalatchBackend()
        with self.assertRaisesRegex(GimbalatchError, 'outcome unknown; do not retry'):
            backend.bind()
        with self.assertRaisesRegex(GimbalatchError, 'already attempted'):
            backend.bind()
        backend.release()
        self.assert_routes('bind')

    def test_redirect_is_not_followed_and_credentials_require_private_loopback_origin(self):
        self.reply({'error': TOKEN}, 307)
        self.assertEqual(self.run_cli('--probe')[0], 2)
        self.credentials(origin=f'{self.origin}@example.invalid/')
        self.assertIn('direct HTTP origin', self.run_cli('--probe')[2])
        self.credentials()
        self.creds.chmod(0o644)
        self.assertIn('mode-0600', self.run_cli('--probe')[2])
        self.assert_routes('bind')

    def test_missing_gateway_token_refuses_before_network(self):
        self.creds.write_text(json.dumps({'origin': self.origin, 'serve_login': LOGIN}))
        status, output, errors = self.run_cli('--probe')
        self.assertEqual((status, output), (2, ''))
        self.assertIn('missing gateway_token', errors)
        self.assert_routes()

    def test_malformed_or_oversize_frames_terminate_without_retry(self):
        for body, message in [(b'\xff\xd8incomplete', 'invalid response'),
                              (b'\xff\xd8'+b'x'*MAX_JPEG+b'\xff\xd9', 'invalid response size')]:
            with self.subTest(size=len(body)):
                self.server.calls.clear()
                self.bound()
                self.reply(body, content_type='image/jpeg')
                self.ended()
                status, output, errors = self.run_cli('--capture')
                self.assertEqual((status, output), (2, ''))
                self.assertIn(message, errors)
                self.assert_routes('bind', 'frame', 'end')

    def test_release_failure_reported_without_replay(self):
        self.bound()
        self.reply({'controls': CONTROLS})
        self.reply({'error': 'park uncertain; owner inspection required'}, 503)
        status, output, errors = self.run_cli('--probe')
        self.assertEqual((status, output), (2, ''))
        self.assertIn('Gimbalatch end: service failure', errors)
        self.assert_routes('bind', 'probe', 'end')

    def test_token_and_handle_never_in_cli_output_logs_or_exception_trace(self):
        log, std, err = io.StringIO(), io.StringIO(), io.StringIO()
        logger = logging.getLogger()
        sink = logging.StreamHandler(log)
        logger.addHandler(sink)
        try:
            self.bound()
            self.reply({'controls': {TOKEN: {'value': HANDLE}}})
            self.ended()
            status, output, errors = self.run_cli('--probe')
            self.assertEqual((status, output), (2, ''))
            self.assertIn('invalid controls response', errors)
            self.reply({'error': f'{TOKEN} {HANDLE}'}, 403)
            with redirect_stdout(std), redirect_stderr(err), self.assertRaises(GimbalatchError) as raised:
                GimbalatchBackend().bind()
            text = output + errors + std.getvalue() + err.getvalue() + log.getvalue()
            text += repr(raised.exception) + ''.join(traceback.format_exception(raised.exception))
            self.assertIsNone(raised.exception.__context__)
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(HANDLE, text)
        finally:
            logger.removeHandler(sink)
        self.assert_routes('bind', 'probe', 'end', 'bind')

    def test_record_placeholder_refuses_without_requesting_a_retained_clip(self):
        # A future receipt-shaped field is not a v1 capability advertisement.
        self.reply({'session_handle': HANDLE, 'record': {'path': '/synthetic/clip.mp4',
                    'receipt': {'duration': 30, 'bytes': 1024, 'sha256': '0'*64}}})
        self.ended()
        with GimbalatchBackend() as backend, self.assertRaisesRegex(GimbalatchError, 'record not available'):
            backend.record('monitor', 30)
        self.assert_routes('bind', 'end')


if __name__ == '__main__':
    unittest.main()
