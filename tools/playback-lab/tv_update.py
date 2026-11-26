"""Wholphin in-place upgrade checks. Never clear, downgrade, uninstall, or back up app data.

The Android package executable and its signing certificate are public program code. User
settings may only come from Wholphin's visible controls, never its private files or database.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets

from tv_device import APP

OLD_SHA='ad5b37285b6ba12c1abdc713e35a492d4a505e0a6ec5112519b6c2f33b06ae14'
OFFICIAL_CERT='e54fda99b452214b3522deb5de14e2dddc06da8de5db1fa7591dce2d80af96a8'
TARGET='1.1.2-0-gc530f5c5'


@dataclass(frozen=True)
class Build:
    package: str
    version: str
    code: int
    certificate: str

    @classmethod
    def inspect(cls,apk,tools,run):
        # apksigner's successful exit verifies the signed bytes, not just the manifest.
        certs=re.findall(rb'^Signer #[0-9]+ certificate SHA-256 digest: ([a-f0-9]{64})$',
                         run(tools/'apksigner','verify','--print-certs',apk),re.M)
        pkg=re.search(rb"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'",
                      run(tools/'aapt2','dump','badging',apk),re.M)
        if len(certs)!=1 or not pkg:raise ValueError('cannot establish a unique signed APK identity')
        return cls(pkg[1].decode(),pkg[3].decode(),int(pkg[2]),certs[0].decode())


def check_upgrade(before,target,*,authorized):
    if not authorized:raise ValueError('family Wholphin requires the explicit update flag')
    if before.package!=APP or target.package!=APP:raise ValueError('package mismatch')
    if before.certificate!=target.certificate or target.certificate!=OFFICIAL_CERT:raise ValueError('signer mismatch; no reinstall fallback')
    if before.version not in ('1.0.9','1.0.9-0-g2d87d526') or before.code!=60:raise ValueError('installed app is not the approved 1.0.9 build')
    if target.version!=TARGET or target.code!=63 or target.code<=before.code:raise ValueError('not the approved strict version increase')


def check_update_target(found,*,rehearsal):
    # The family exception is narrower than the ordinary disposable Google TV path.
    # A similar box or a changed userspace needs a new verification, not a best guess.
    if rehearsal:
        if found.get('emulator') is True and found.get('abi')=='x86_64':return
    elif (found.get('emulator') is False and found.get('device')=='jarvis2' and found.get('sdk')=='34'
          and found.get('abiList')==['armeabi-v7a','armeabi'] and found.get('abi')=='armeabi-v7a'):return
    raise ValueError('family upgrade is restricted to the verified onn. userspace or its private rehearsal')


def installed_build(tv,work,tools,run):
    # Never read from /data/data, /data/user, shared preferences, profiles or a backup.
    paths=tv.adb('shell','pm','path',APP,timeout=12).decode().splitlines()
    bases=[line[8:] for line in paths if re.fullmatch(r'package:/data/app/(?:[\w~+=.-]+/)*'+re.escape(APP)+r'-[\w~+=.-]+/base\.apk',line)]
    if len(bases)!=1:raise ValueError('installed base APK path not uniquely verified')
    local=work/('installed-public-code-'+secrets.token_hex(8)+'.apk')
    try:
        tv.adb('pull',bases[0],local,timeout=60)
        return Build.inspect(local,tools,run)
    finally:local.unlink(missing_ok=True)


class Redacted:
    def proof(self,salt):
        return {k:hashlib.sha256(salt+json.dumps(v,ensure_ascii=False).encode()).hexdigest() for k,v in vars(self).items()}


@dataclass(frozen=True,repr=False)
class Profile(Redacted):
    servers: tuple[str,...]
    active: str
    user: str

    def validate(self):
        if not self.servers or len(self.servers)>12 or len(set(self.servers))!=len(self.servers) or self.active not in self.servers or not self.user:
            raise ValueError('incomplete or ambiguous signed-in server/user readback')
        return self


@dataclass(frozen=True,repr=False)
class PlayerSettings(Redacted):
    backend: str
    options: str

    def validate(self):
        if self.backend not in ('MPV','ExoPlayer') or not isinstance(self.options,str):raise ValueError('player settings unreadable')
        if self.options and (len(self.options)>2048 or not all(re.fullmatch(r'[a-zA-Z0-9:/._=-]+',l) for l in self.options.split('\n'))):
            raise ValueError('stock editor cannot safely restore these mpv options; nothing changed')
        return self


def private_labels(original,root):
    """Owner-local labels only. Never put this file or its contents in a Git receipt."""
    import os
    import tempfile
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.is_symlink() or root.stat().st_uid!=os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('private owner label directory is not owner-only')
    fd,path=tempfile.mkstemp(prefix='tv-before-',suffix='.json',dir=root)
    with os.fdopen(fd,'w') as f:json.dump({'serverLabel':original.active,'userLabel':original.user},f,ensure_ascii=False)
    return path


class Update:
    """A separate transaction from disposable installs: the interface contains no clear/uninstall."""
    def __init__(self,tv,ui,work,tools,run,receipt):
        self.tv,self.ui,self.work,self.tools,self.run,self.receipt=tv,ui,work,tools,run,receipt
        self.original=None;self.target=None;self.salt=secrets.token_bytes(32);self.lab_added=False

    def begin(self,apk,manifest,*,authorized):
        if not authorized:raise ValueError('family update not requested')
        old=installed_build(self.tv,self.work,self.tools,self.run)
        if manifest['wholphinVersion']!=old.version or manifest['packages'].get(APP)!=str(old.code):raise ValueError('installed binary disagrees with Android package manifest')
        self.target=Build.inspect(apk,self.tools,self.run)
        check_upgrade(old,self.target,authorized=authorized)
        self.original=self.ui.snapshot().validate();self.receipt.update(beforeBuild=vars(old),beforeUI=self.original.proof(self.salt))
        if getattr(self.tv,'emulator',False) is None:
            private_labels(self.original,Path.home()/'.local/state/cleanyfin/family-proof')
        # -r preserves app data. Never authorize -d, -g, uninstall, clear or a fresh install.
        if self.tv.adb('install','-r','--no-streaming',apk,timeout=240).strip().splitlines()[-1:]!=[b'Success']:
            raise ValueError('in-place update not confirmed; will not reinstall')
        self.receipt['inPlaceUpdateReported']=True
        self.receipt['expectedDifference']='family may need to re-select server/profile or sign in'
        self.check('afterUpdate')

    def check(self,phase):
        if installed_build(self.tv,self.work,self.tools,self.run)!=self.target:raise ValueError('installed build after update differs from verified target')
        self.receipt[phase+'Build']=vars(self.target)
        if self.ui.inventory()!=self.original.servers:raise ValueError('original server inventory not retained')
        self.receipt[phase]={'servers':self.original.proof(self.salt)['servers'],'profileAuthentication':'not tested'}

    def start_settings(self):
        # Called only after the lab server has independently witnessed its signed-in session.
        backend=self.ui.read_backend();self.lab_backend=backend
        if backend=='ExoPlayer':self.ui.change_backend(backend,'MPV')
        observed,options=self.ui.read_settings()
        if observed!='MPV':raise ValueError('lab MPV backend not verified')
        self.lab_settings=PlayerSettings(backend,options).validate()
        self.tv.transport.bind_original_options(self.lab_settings)
        self.receipt['labSettingsBefore']={k:self.lab_settings.proof(self.salt)[k] for k in ('backend','options')}
        self.ui.restart()

    def finish(self):
        if not self.receipt.get('inPlaceUpdateReported'):return
        if self.lab_added:self.ui.restore(self.original,getattr(self,'lab_settings',None),getattr(self,'lab_backend',None))
        self.check('afterRestore');self.receipt['visibleStateRestored']=True
