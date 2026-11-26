"""Direct, non-retaining local-witness client for the Gimbalatch v1 API."""
import http.client
import json
import os
from pathlib import Path
import re
import stat


MAX_JPEG = 2 * 1024 * 1024
MAX_JSON = 128 * 1024
CREDENTIALS = '.config/gimbalatch/clients/local-witness.json'


class GimbalatchError(RuntimeError):
    """An operator-facing error; never contains raw service or credential data."""


def _json_object(data):
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _credentials(path):
    data = None
    try:
        # Never follow a symlink or block on a non-regular file. Check the opened fd.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600):
                data = handle.read(4097)
    except OSError:
        pass
    config = _json_object(data) if data is not None and len(data) <= 4096 else None
    if config is None:
        raise GimbalatchError('Gimbalatch needs a private mode-0600 local-witness credential file')
    if 'gateway_token' not in config:
        raise GimbalatchError('Gimbalatch local-witness credentials are missing gateway_token')
    origin, token, login = (config.get(key) for key in ('origin', 'gateway_token', 'serve_login'))
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/?', origin) if isinstance(origin, str) else None
    if not match or not 1 <= int(match[1]) <= 65535:
        raise GimbalatchError('Gimbalatch credentials must specify a direct HTTP origin on 127.0.0.1')
    if (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]{32,}=*', token)
            or not isinstance(login, str) or not re.fullmatch(r'[!-~]+', login)):
        raise GimbalatchError('Gimbalatch local-witness credentials are invalid')
    return int(match[1]), token, login


def _refusal(action, status, body):
    payload = _json_object(body) or {}
    # Use known reasons only for dispatch; no untrusted text is ever relayed.
    reason = payload.get('error')
    if status == 403:
        if action == 'bind' and reason == 'no unclaimed owner grant for this recipient':
            return 'waiting for owner approval (no pending local-witness grant, or it expired)'
        if reason == 'gateway identity required':
            return 'local-witness authentication refused; check the installed credentials'
        if reason == 'approved preset must be selected in this session':
            return 'capture requires an approved preset selected in this session; this client does not aim'
        if reason in ('session expired during binding', 'grant ended before wake',
                      'grant ended before framing setup', 'session ended before dispatch',
                      'session ended before capture', 'session ended; frame withheld'):
            return 'owner grant expired or was revoked; do not retry automatically'
        return 'operation refused: wrong scope or the owner grant expired or was revoked'
    if status == 409:
        return 'camera already bound or cleanup uncertain; ask the owner to verify before trying again'
    if status == 412:
        return 'camera framing or tracking safety check failed; ask the owner to inspect'
    if status == 429:
        return 'session rate or count limit reached; no automatic retry'
    if status >= 500:
        return 'service failure; outcome unknown; do not retry; ask the owner to inspect'
    return f'unexpected HTTP status {status}; outcome unknown; do not retry'


class GimbalatchBackend:
    """One explicit bind per instance, with probe, in-memory frame, and end only."""

    def __init__(self):
        self._attempted = False
        self._session = None
        self._identity = None

    def _post(self, action, *, jpeg=False):
        port, token, login = self._identity
        headers = {'Authorization': f'Bearer {token}', 'Tailscale-User-Login': login,
                   'Content-Type': 'application/json'}
        if self._session is not None:
            headers['X-Camera-Session'] = self._session
        # HTTPConnection neither follows redirects nor consults proxy environment variables.
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        failed = False
        try:
            connection.request('POST', f'/v1/{action}', b'{}', headers)
            with connection.getresponse() as response:
                status = response.status
                content_type = response.headers.get_content_type()
                size = response.length
                limit = MAX_JPEG if status == 200 and jpeg else MAX_JSON
                data = response.read(limit + 1) if size is None or size <= limit else b''
        except ConnectionRefusedError:
            failed = 'service is down or not listening; no automatic retry'
        except (OSError, http.client.HTTPException, ValueError):
            failed = 'service unreachable or response lost; outcome unknown; do not retry; ask the owner to inspect'
        finally:
            connection.close()
        if failed:
            raise GimbalatchError(f'Gimbalatch {action}: {failed}')
        if status != 200:
            raise GimbalatchError(f'Gimbalatch {action}: {_refusal(action, status, data)}')
        if len(data) > limit or (size is not None and len(data) != size):
            raise GimbalatchError(f'Gimbalatch {action}: invalid response size; outcome unknown; do not retry')
        if jpeg:
            if content_type == 'image/jpeg' and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                return data
        elif content_type == 'application/json':
            payload = _json_object(data)
            if payload is not None:
                return payload
        raise GimbalatchError(f'Gimbalatch {action}: invalid response; outcome unknown; do not retry')

    def bind(self):
        if self._attempted:
            raise GimbalatchError('Gimbalatch bind already attempted; do not replay it')
        self._attempted = True
        self._identity = _credentials(Path.home() / CREDENTIALS)
        try:
            handle = self._post('bind').get('session_handle')
            if (not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', handle)
                    or self._identity[1] in handle):
                raise GimbalatchError('Gimbalatch bind: invalid session response; ask the owner to revoke before retrying')
        except BaseException:
            self._identity = None
            raise
        self._session = handle

    def _require_bound(self):
        if self._session is None:
            raise GimbalatchError('Gimbalatch camera session is not bound')

    def probe(self):
        self._require_bound()
        controls = self._post('probe').get('controls')
        # Accept only the v1 numeric control schema, never arbitrary peer text.
        if not isinstance(controls, dict) or any(
                name not in {'pan', 'tilt', 'zoom', 'exposure'} or not isinstance(spec, dict)
                or set(spec) != {'min', 'max', 'step', 'driver_value'}
                or any(type(value) is not int or not -(2**31) <= value < 2**31 for value in spec.values())
                for name, spec in controls.items()):
            raise GimbalatchError('Gimbalatch probe: invalid controls response')
        return controls

    def frame(self):
        self._require_bound()
        return self._post('frame', jpeg=True)

    def record(self, preset, seconds):
        """Reserved for a separately authorized, versioned recording API."""
        raise GimbalatchError('Gimbalatch record not available')

    def release(self):
        if self._session is not None:
            try:
                if self._post('end').get('session') != 'ended':
                    raise GimbalatchError('Gimbalatch end: release not confirmed; ask the owner to inspect')
            finally:
                self._session = self._identity = None

    def __enter__(self):
        self.bind()
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.release()
        except GimbalatchError as release_error:
            if exc is None:
                raise
            if isinstance(exc, GimbalatchError):
                raise GimbalatchError(f'{exc}; release not confirmed: {release_error}') from None
            # Preserve interrupts and non-camera failures; don't retry cleanup.
        return False
