#!/usr/bin/env python3
"""Read-only wireless ADB, with a separate key and loopback-only server.

with AdbLab() as lab:
    lab.pair(pair_endpoint, code)  # once, after the disposable emulator witness
    lab.connect(debug_endpoint)   # same host; separate port from Settings
    model = lab.probe('model')    # bytes, closed vocabulary, no arbitrary shell
"""
import argparse
from contextlib import ExitStack
import fcntl
import getpass
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import signal
import stat
import subprocess as sp
import tempfile
import time

LABEL = 'cleanyfin@lab'
ADB_SHA = 'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01'
STATE = Path.home()/'.local/state/cleanyfin/adb-lab'
ADB = Path(os.environ.get('ANDROID_HOME') or Path.home()/'Android/Sdk')/'platform-tools/adb'
PRIVATE_PORT = 15038  # inside a new network namespace, never on the host
PROBES = {
    'model': ('getprop', 'ro.product.model'), 'device': ('getprop', 'ro.product.device'),
    'codecs': ('cat', '/vendor/etc/media_codecs.xml'),
    'audio-policy-xml': ('cat', '/vendor/etc/audio_policy_configuration.xml'),
    'surround': ('settings', 'get', 'global', 'encoded_surround_output'),
    'surround-formats': ('settings', 'get', 'global', 'encoded_surround_output_enabled_formats'),
    'display-size': ('wm', 'size'), 'latency': ('dumpsys', 'SurfaceFlinger', '--latency'),
    'recorder-help': ('screenrecord', '--help'),
}


class LabError(RuntimeError):
    """Safe to display; contains no address, key, pairing code or adb output."""


def endpoint(text):
    """One explicit RFC1918 IPv4 address and nonprivileged, nonlegacy port."""
    m = re.fullmatch(r'([0-9.]+):([0-9]{1,5})', text.strip())
    try:
        address, port = ipaddress.IPv4Address(m[1]), int(m[2])
    except (ValueError, TypeError):
        raise LabError('Use the IPv4 address and port shown on Wireless debugging.') from None
    if (not any(address in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
            or not 1024 <= port <= 65535 or port == 5555):
        raise LabError('Not an allowed wireless debugging endpoint.')
    return str(address), port


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077 or path.resolve() != path:
        raise LabError('Unsafe lab state directory.')


def _file(path, *, secret=False):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1 or (secret and s.st_mode & 0o077):
        raise LabError('Unsafe lab state file.')
    return path.read_bytes()


def _run(args, *, data=None, timeout=12):
    try:
        r = sp.run(args, input=data or b'', stdout=sp.PIPE, stderr=sp.DEVNULL,
                   env={'PATH': '/usr/bin:/bin'}, timeout=timeout, check=False)
    except (OSError, sp.TimeoutExpired):
        raise LabError('Private lab command unavailable or timed out.') from None
    if r.returncode:
        raise LabError('Private lab command failed.')
    return r.stdout


def _spawn(stack, argv):
    p = sp.Popen(['setpriv', '--pdeathsig', 'TERM', '--', *argv], stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                 start_new_session=True, env={'PATH': '/usr/bin:/bin'})
    def close():
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                break
            try:
                p.wait(timeout=2)
            except sp.TimeoutExpired:
                pass
        p.wait(timeout=3)
    stack.callback(close)
    return p


def _wait(pred, proc):
    end = time.monotonic() + 8
    while proc.poll() is None and time.monotonic() < end:
        if pred():
            return
        time.sleep(.05)
    raise LabError('Private lab process failed to start.')


class AdbLab:
    def __init__(self, *, state=STATE, adb=ADB):
        self.state, self.adb = Path(state).absolute(), Path(adb).resolve()
        self.android = self.state/'home'/'.android'
        self.connected = False

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self._start()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        self.connected = False
        return self.stack.__exit__(*exc)

    def _start(self):
        if _digest(self.adb.read_bytes()) != ADB_SHA:
            raise LabError('Unverified platform-tools executable; rerun platform qualification.')
        for p in (self.state, self.state/'home', self.android):
            _private_dir(p)
        fd = os.open(self.state/'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.stack.callback(os.close, fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LabError('Another lab session owns the key.') from None
        for p in self.android.iterdir():
            if p.name not in ('adbkey', 'adbkey.pub', 'adb_known_hosts.pb', f'adb.{PRIVATE_PORT}'):
                raise LabError('Unexpected file in private Android home.')
            _file(p, secret=p.name == 'adbkey')
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cf-adb-', dir='/tmp')))
        # Outlive the four-hour job plus restoration grace; ExitStack normally stops this keeper.
        ns = _spawn(self.stack, ['unshare', '-Urn', 'sh', '-ec', 'ip link set lo up; exec sleep 18000'])
        _wait(lambda: os.readlink(f'/proc/{ns.pid}/ns/net') != os.readlink('/proc/self/ns/net'), ns)
        self.ns = ['nsenter', '--preserve-credentials', '-t', str(ns.pid), '-U', '-n', '--']
        _wait(lambda: b'127.0.0.1' in _run(self.ns + ['ip', '-4', 'addr', 'show', 'lo']), ns)
        if _run(self.ns + ['ip', '-o', 'link', 'show']).count(b'\n') != 1:
            raise LabError('Expected a loopback-only namespace.')
        (self.tmp/'passwd').write_text('cleanyfin:x:0:0:Lab:/lab:/bin/false\n')
        sandbox = self.ns + ['bwrap', '--die-with-parent', '--unshare-uts', '--hostname', 'lab',
            '--unshare-pid', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
            '--ro-bind', '/usr/lib64', '/lib64', '--ro-bind', str(self.adb), '/adb',
            '--ro-bind', str(self.tmp/'passwd'), '/etc/passwd', '--bind', str(self.android), '/lab/.android',
            '--clearenv', '--setenv', 'HOME', '/lab', '--setenv', 'ANDROID_USER_HOME', '/lab/.android',
            '--setenv', 'USER', 'cleanyfin', '--setenv', 'LOGNAME', 'cleanyfin', '--setenv', 'HOSTNAME', 'lab']
        for key in ('ADB_USB', 'ADB_EMU', 'ADB_LOCAL_TRANSPORT_MAX_PORT', 'ADB_MDNS', 'ADB_MDNS_AUTO_CONNECT'):
            sandbox += ['--setenv', key, '0']
        self.bin = sandbox + ['--', '/adb']
        key = self.android/'adbkey'
        if not key.exists():
            _run(self.bin + ['keygen', '/lab/.android/adbkey'])
        _file(key, secret=True)
        self.public = _run(self.bin + ['pubkey', '/lab/.android/adbkey']).strip()
        if (not re.fullmatch(rb'[A-Za-z0-9+/=]+ '+LABEL.encode(), self.public)
                or _file(self.android/'adbkey.pub').strip() != self.public):
            raise LabError('Runtime key identity is not the expected neutral label.')
        self.server = _spawn(self.stack, self.bin + ['-L', f'tcp:{PRIVATE_PORT}', 'server', 'nodaemon'])
        _wait(lambda: bool(_run(self.ns + ['ss', '-Hlnt', f'sport = :{PRIVATE_PORT}'])), self.server)
        self.client = self.bin + ['-H', '127.0.0.1', '-P', str(PRIVATE_PORT)]

    def _command(self, *args, data=None):
        if self.server.poll() is not None:
            raise LabError('Private ADB server exited.')
        return _run(self.client + list(args), data=data, timeout=35)

    def _relay(self, stack, host, port, local, *, outer=()):
        # Only the outside relay has a route. No DNS, broadcast, scan or USB.
        path = self.tmp/f'{local}.sock'
        outside = _spawn(stack, [*outer, 'socat', f'UNIX-LISTEN:{path},fork,unlink-early,mode=0600',
                                 f'TCP4:{host}:{port},connect-timeout=6'])
        _wait(path.is_socket, outside)
        inside = _spawn(stack, self.ns + ['socat', f'TCP4-LISTEN:{local},bind=127.0.0.1,reuseaddr,fork',
                                          f'UNIX-CONNECT:{path}'])
        _wait(lambda: bool(_run(self.ns + ['ss', '-Hlnt', f'sport = :{local}'])), inside)
        return f'127.0.0.1:{local}'

    def _receipt(self, *, harness=None):
        binding=b'wireless-pair-v1\0' + ADB_SHA.encode() + self.public + Path(__file__).read_bytes()
        if harness is not None:
            if not re.fullmatch('[0-9a-f]{64}',harness):raise LabError('Invalid harness digest.')
            binding+=b'\0tv-harness\0'+harness.encode()
        return (_digest(binding)+'\n').encode()

    def _witnessed(self, *, harness=None):
        p = self.state/('witness' if harness is None else 'witness-tv')
        if not p.exists() or _file(p) != self._receipt(harness=harness):
            raise LabError('No matching emulator pairing witness for this tool and key.')

    def _pair_at(self, host, port, code, *, outer=()):
        if not re.fullmatch(r'[0-9]{6}', code):
            raise LabError('Expected the six-digit pairing code.')
        with ExitStack() as relays:
            dest = self._relay(relays, host, port, 15039, outer=outer)
            result = self._command('pair', dest, data=(code+'\n').encode())
            if b'Successfully paired to ' not in result:
                raise LabError('Pairing not accepted. Check the current code and pairing port.')

    def _destination(self, address):
        host, port = endpoint(address)
        # Deployment-specific exclusions stay in private state, not a public repo.
        policy = self.state/'blocked-hosts'
        if not policy.exists():
            raise LabError('Missing local device exclusions; no network access.')
        try:
            excluded = {str(ipaddress.IPv4Address(v)) for v in _file(policy).decode('ascii').splitlines() if v}
        except (ValueError, UnicodeError):
            raise LabError('Invalid local device exclusions.') from None
        if not excluded or host in excluded:
            raise LabError('Endpoint refused by the local device exclusions.')
        binding = self.state/'paired-host'
        if binding.exists() and _file(binding).strip() != _digest(host.encode()).encode():
            raise LabError('Different host from the existing pairing; refusing.')
        return host, port

    def pair(self, address, code):
        self._witnessed()
        host, port = self._destination(address)
        self._pair_at(host, port, code)
        (self.state/'paired-host').write_text(_digest(host.encode())+'\n')

    def connect(self, address):
        self._witnessed()
        host, port = self._destination(address)
        record = self.state/'paired-host'
        if not record.exists() or _file(record).strip() != _digest(host.encode()).encode():
            raise LabError('Pair this host first; different or unpaired host refused.')
        if self.connected:
            raise LabError('This session is already connected.')
        with ExitStack() as session:
            dest = self._relay(session, host, port, 15040)
            if not self._command('connect', dest).strip().startswith(b'connected to '):
                raise LabError('Connection not accepted. Use the separate debugging port.')
            session.callback(self._disconnect, dest)
            target = ['-s', dest]
            if (self._command(*target, 'shell', *PROBES['device']).strip() != b'jarvis2'
                    or self._command(*target, 'shell', *PROBES['model']).strip() != b'onn 4K Pro Streaming Device'):
                raise LabError('Target is not the qualified onn. 4K Pro model.')
            self.stack.enter_context(session.pop_all())
            self.target, self.connected = target, True

    def _disconnect(self, dest):
        try:
            self._command('disconnect', dest)
        except LabError:
            pass  # stack still kills the owned server, relay process groups and namespace

    def probe(self, name):
        if not self.connected or name not in PROBES:
            raise LabError('Probe unavailable or not on the read-only allowlist.')
        return self._command(*self.target, 'shell', *PROBES[name])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('pair', 'identify'))
    args = parser.parse_args()
    try:
        with AdbLab() as lab:
            lab._witnessed()
            if args.operation == 'pair':
                lab.pair(input('Pairing IP:port shown on the box: '), getpass.getpass('Six-digit pairing code: '))
                print(f'Paired. Check that Paired devices shows {LABEL}.')
            else:
                lab.connect(input('Debugging IP:port shown on the box: '))
                print('Verified onn. 4K Pro. Disconnecting.')
    except (LabError, OSError, EOFError) as exc:
        parser.exit(1, (str(exc) if isinstance(exc, LabError) else 'Lab unavailable.')+'\n')


if __name__ == '__main__':
    main()
