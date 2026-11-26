"""Offline regressions: no discovery and no physical endpoints (even for the optional SDK smoke)."""
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import adb_lab as a


class Policy(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.lab = a.AdbLab(state=d.name)
        self.lab.public = b'synthetic-key cleanyfin@lab'
        (self.lab.state/'blocked-hosts').write_text('10.88.77.66\n')

    def witness(self):
        (self.lab.state/'witness').write_bytes(self.lab._receipt())

    def test_no_inferred_or_legacy_endpoint(self):
        for address in ('', '1.2.3.4:33333', '127.0.0.1:39999',
                        '10.0.1.9:5555', '10.0.1.9', '10.0.1.9:65536', '10.0.1.9:0',
                        'name.local:32222', '10.0.1.9:80', '169.254.1.1:33333',
                        '10.0.1.9:44444;id', '10.001.2.3:33333'):
            with self.subTest(address=address), self.assertRaises(a.LabError):
                a.endpoint(address)
        self.assertEqual(a.endpoint('10.1.2.3:41000'), ('10.1.2.3', 41000))

    def test_machine_local_exclusions_fail_closed(self):
        self.witness()
        with patch.object(self.lab, '_pair_at') as transport:
            with self.assertRaises(a.LabError): self.lab.pair('10.88.77.66:42000', '123456')
            for value in ('invalid', ''):
                (self.lab.state/'blocked-hosts').write_text(value)
                with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:42000', '123456')
            (self.lab.state/'blocked-hosts').unlink()
            with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:42000', '123456')
            transport.assert_not_called()

    def test_missing_changed_or_linked_witness_denied_before_network(self):
        with patch.object(self.lab, '_relay') as relay:
            with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:41000', '123456')
            self.witness()
            self.lab.public = b'another-key cleanyfin@lab'
            with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:41000', '123456')
            (self.lab.state/'witness').unlink()
            (self.lab.state/'witness').symlink_to('/dev/null')
            with self.assertRaises(a.LabError): self.lab.connect('10.1.2.3:41000')
            relay.assert_not_called()

    def test_secret_never_in_argv_and_only_success_binds_host(self):
        self.witness()
        with patch.object(self.lab, '_relay', return_value='127.0.0.1:15039') as relay, patch.object(self.lab, '_command') as call:
            with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:41000', '12345x')
            relay.assert_not_called()
            call.return_value = b'Failed: Wrong password'
            with self.assertRaises(a.LabError): self.lab.pair('10.1.2.3:41000', '012345')
            self.assertFalse((self.lab.state/'paired-host').exists())
            call.return_value = b'Enter pairing code: Successfully paired to 127.0.0.1:15039 [guid=synthetic]'
            self.lab.pair('10.1.2.3:41000', '012345')
            call.assert_called_with('pair', '127.0.0.1:15039', data=b'012345\n')
            self.assertNotIn(b'10.1.2.3', (self.lab.state/'paired-host').read_bytes())
            relay.reset_mock()
            with self.assertRaises(a.LabError): self.lab.connect('10.1.2.4:41001')
            relay.assert_not_called()

    def test_identity_mismatch_blocks_probes_and_disconnects(self):
        self.witness()
        (self.lab.state/'paired-host').write_text(a._digest(b'10.1.2.3'))
        with ExitStack() as self.lab.stack, patch.object(self.lab, '_relay', return_value='loopback'), patch.object(self.lab, '_command', side_effect=[b'connected to loopback', b'not-onn', b'disconnected']) as call:
            with self.assertRaises(a.LabError): self.lab.connect('10.1.2.3:41000')
            with self.assertRaises(a.LabError): self.lab.probe('codecs')
            self.lab.stack.close()
            call.assert_called_with('disconnect', 'loopback')

    def test_shell_arguments_are_not_input(self):
        self.lab.connected, self.lab.target = True, ['-s', 'loopback']
        with patch.object(self.lab, '_command') as run:
            for s in ('', 'model;id', 'settings put global x y', 'shell', 'dumpsys activity', 'pull', 'install', 'logcat', 'input'):
                with self.assertRaises(a.LabError): self.lab.probe(s)
            run.assert_not_called()
            self.lab.probe('surround')
            run.assert_called_once_with('-s', 'loopback', 'shell', 'settings', 'get', 'global', 'encoded_surround_output')

    def test_reject_foreign_key_and_permissive_dir(self):
        d = self.lab.state/'dir'
        d.mkdir(mode=0o755)
        d.chmod(0o755)
        with self.assertRaises(a.LabError): a._private_dir(d)
        f = self.lab.state/'key'
        f.write_bytes(b'synthetic')
        f.chmod(0o600)
        os.link(f, self.lab.state/'link')
        with self.assertRaises(a.LabError): a._file(f, secret=True)


@unittest.skipUnless(a.ADB.is_file() and a._digest(a.ADB.read_bytes()) == a.ADB_SHA
                     and all(shutil.which(x) for x in ('bwrap', 'unshare', 'setpriv', 'nsenter', 'ss', 'ip', 'socat')),
                     'requires the pinned Linux Android lab')
class SandboxSmoke(unittest.TestCase):
    def test_default_keys_and_server_are_not_used_and_only_explicit_relay_is_reachable(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'ADB_SERVER_SOCKET':'tcp:127.0.0.1:1', 'ADB_VENDOR_KEYS':'/bad', 'LOGNAME':'not-the-lab-user', 'HOSTNAME':'not-the-lab-host'}):
            state = Path(d)/'state'
            with a.AdbLab(state=state) as lab, socket.socket() as listener:
                self.assertEqual(lab.public.split()[1:], [a.LABEL.encode()])
                self.assertEqual(lab._command('devices').split(), b'List of devices attached'.split())
                pub = lab.public
                with self.assertRaises(a.LabError):
                    a._run(lab.bin + ['pubkey', str(Path.home()/'.android/adbkey')])
                listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(6)
                def respond():
                    s, _ = listener.accept()
                    with s: s.sendall(b'only this fixture\n')
                thread = threading.Thread(target=respond, daemon=True)
                thread.start()
                dest = lab._relay(lab.stack, '127.0.0.1', listener.getsockname()[1], 15040)
                self.assertEqual(a._run(lab.ns + ['socat', '-', f'TCP4:{dest}']), b'only this fixture\n')
                thread.join(timeout=6)
            with a.AdbLab(state=state) as again:
                self.assertEqual(again.public, pub)
                self.assertFalse(again.connected)


if __name__ == '__main__':
    unittest.main()
