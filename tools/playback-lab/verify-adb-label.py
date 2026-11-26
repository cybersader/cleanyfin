#!/usr/bin/env python3
"""Prove what Android actually received. No physical endpoint or user AVD is accepted."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import re
import secrets
import subprocess as sp
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from adb_lab import ADB, AdbLab, LABEL, LabError


def main():
    spec = importlib.util.spec_from_file_location('tv_harness', HERE/'tv-harness.py')
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    with ExitStack() as stack:
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='cf-lab-proof-')))
        lab = stack.enter_context(AdbLab())
        (lab.state/'witness').unlink(missing_ok=True)
        tv = harness.TV(work, ADB.parent.parent, stack, llvmpipe_threads=2)
        # Separate neutral bootstrap key, never the key under test. The clone
        # must learn the latter from TLS pairing, not emulator key injection.
        for k in list(tv.env):
            if k.startswith(('ADB_', 'ANDROID_ADB_', 'CLEANYFIN_TV_')) or k in ('ANDROID_SERIAL', 'ANDROID_SDK_HOME', 'ANDROID_PREFS_ROOT'):
                tv.env.pop(k)
        tv.env.update(LOGNAME='fixture', USER='fixture', HOSTNAME='fixture', ADB_USB='0',
                      ADB_MDNS='0', ADB_MDNS_AUTO_CONNECT='0', ADB_LOCAL_TRANSPORT_MAX_PORT='0')
        key = tv.home/'.android/adbkey'
        tv.call(ADB, 'keygen', key)
        if tv.call(ADB, 'pubkey', key).strip().split()[-1:] != [b'fixture@fixture']:
            raise LabError('Clone bootstrap identity check failed.')
        print('Booting the harness-owned API 36 clone (offline).', flush=True)
        tv.boot()
        def shell(*args):
            return tv.adb('shell', *args, timeout=8)
        if shell('getprop', 'ro.boot.qemu').strip() != b'1' or shell('getprop', 'ro.build.version.sdk').strip() != b'36':
            raise LabError('Not the expected disposable emulator.')
        if lab.public.split()[0] in shell('dumpsys', 'adb'):
            raise LabError('Invalid baseline: clone already knew the proof key.')
        code = f'{secrets.randbelow(1000000):06d}'
        # API 36 IAdbManager.enablePairingByQrCode. This selects a synthetic
        # password; the native pairing server and peer-key handling are the
        # same as enablePairingByPairingCode. Only used in this disposable AVD.
        def listeners():
            # This clone is offline. Diff listening sockets locally, not by
            # probing ports. Ignore loopback-only services and reject ambiguity.
            try:
                text = shell('ss', '-ltn')
            except sp.CalledProcessError:
                text = shell('toybox', 'netstat', '-ltn')
            return {int(m[1]) for m in re.finditer(rb'(?m)\s(?:0\.0\.0\.0|\*|\[::\]|::):(\d+)\s', text)}
        before = listeners()
        reply = shell('service', 'call', 'adb', '9', 's16', 'cleanyfin-proof', 's16', code)
        if not re.search(rb'Parcel\(\s*00000000\b', reply):
            raise LabError('Guest rejected the test-only pairing request.')
        def port_from_guest():
            log = tv.adb('logcat', '-b', 'system', '-d', '-s', 'AdbDebuggingManager:I', '*:S')
            line = next((ln for ln in log.splitlines() if b'Registered pairing service' in ln and b'cleanyfin-proof' in ln), b'')
            m = re.search(rb'\bport\s*[:=]\s*(\d+)', line, re.I)
            if m and 0 < int(m[1]) <= 65535:
                return int(m[1])
            if b'adbwifi guid was not set' in log or b'Unable to start pairing server' in log:
                raise LabError('Guest wireless pairing server unavailable.')
            added = listeners() - before
            return added.pop() if len(added) == 1 else None
        try:
            port = harness.eventually(port_from_guest, 20, 'No unambiguous guest pairing listener.')
        except RuntimeError:
            # Only structural diagnostics; never dump logs, keys, GUIDs or codes.
            logs = tv.adb('logcat', '-b', 'all', '-d', '-s', 'AdbDebuggingManager:V', '*:S')
            facts = (bool(shell('getprop', 'persist.adb.wifi.guid').strip()), len(listeners()-before),
                     b'Registered pairing service' in logs, b'Failed to register pairing service' in logs)
            raise LabError('No pairing listener: guid=%s new-listeners=%d registered=%s registration-failed=%s' % facts) from None
        relay = int(tv.adb('forward', 'tcp:0', f'tcp:{port}').strip())
        lab._pair_at('127.0.0.1', relay, code, outer=tv.prefix)
        # The keystore is Android's record of the peer it actually authenticated,
        # not an inference from keygen or a locally rewritten adbkey.pub file.
        exact = re.compile(rb'(?:^|[=\s"])' + re.escape(lab.public) + rb'(?:[\r\n"]|$)', re.M)
        harness.eventually(lambda: exact.search(shell('dumpsys', 'adb')), 15,
                           'Android did not persist the exact neutral peer key.')
        (lab.state/'witness').write_bytes(lab._receipt())
        print(f'PASS: fresh Google TV received and stored the exact dedicated key labelled {LABEL}.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, sp.SubprocessError, RuntimeError) as exc:
        # Subprocess errors can include arguments. Never display those.
        sys.exit(str(exc) if isinstance(exc, RuntimeError) else 'Disposable witness command failed.')
