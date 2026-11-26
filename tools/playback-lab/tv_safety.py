"""Household release concerns side effects, not whether the renderer passed R24."""
from contextlib import ExitStack
import json
import re
import time
from tv_update import Build, check_upgrade


class PlannedSafetyStop(Exception):pass


def rehearse(h,tv,obs):
    """Make and interrupt a real, short recording of the synthetic lab UI; no movie."""
    if tv.emulator is None or not tv.transport.guest or tv.found.get('emulator') is not True:
        raise ValueError('safety rehearsal requires a private emulator')
    info=obs['idleCapture']={}
    class SimulatedStop(Exception):pass
    try:
        with ExitStack() as take:
            video,_=tv.capture.begin(h,tv,take,'safety-idle',8,info,sound=True,display=True)
            if video is None:raise ValueError('no owned safety recording was created')
            time.sleep(2)
            raise SimulatedStop()
    except SimulatedStop:
        obs['interruptedRecordingRemoved']=bool(info.get('videoRemoved') is True and video.is_file() and video.stat().st_size>0)
        video.unlink(missing_ok=True)
        raise PlannedSafetyStop() from None  # unwind the complete family transaction, not just the recording


def safety_proof(row,digest):
    """Only an isolated safety receipt. Measurement reports neither arm nor veto a box."""
    def yes(obj,*keys):return all(obj.get(k) is True for k in keys)
    def hashes(obj,keys):return isinstance(obj,dict) and set(obj)==set(keys) and all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in obj.values())
    if not (row.get('mode')=='family-safety-only' and row.get('captureHarnessSha256')==digest and not row.get('blocker')
            and row.get('device',{}).get('emulator') is True and row.get('unexpectedResidue')==[] and row.get('ownedContainersRemaining')==0
            and yes(row,'dryRun','restored','scratchRemoved','privateAdbExited','privateNamespaceExited','emulatorExited')):return False
    f=row.get('familyUpdate',{});r=row.get('internalRestore',{});o=row.get('observations',{});before=f.get('beforeUI');settings=f.get('labSettingsBefore')
    try:
        old=Build(**f['beforeBuild']);new=Build(**f['afterUpdateBuild']);check_upgrade(old,new,authorized=True)
    except (KeyError,ValueError,TypeError):return False
    return bool(yes(f,'authenticatedSyntheticSession','syntheticBaselineSeeded','inPlaceUpdateReported','liveLabProfileVerified','restoreAttempted','exactLabDeleteActivated','labAbsentAfter','visibleStateRestored')
        and hashes(before,('servers','active','user')) and before['servers']==f.get('afterUpdate',{}).get('servers')==f.get('afterRestore',{}).get('servers')
        and f['afterUpdateBuild']==f.get('afterRestoreBuild') and hashes(settings,('backend','options')) and settings==f.get('labSettingsAfter')
        and o.get('verifiedSettings',{}).get('backend')=='MPV' and o.get('verifiedSettings',{}).get('mpvOptions')
        and yes(o,'interruptedRecordingRemoved','familyTransactionUnwound') and yes(o.get('idleCapture',{}),'freshProjectionCallback','videoRemoved')
        and r.get('wholphinAbsentBefore') is False and yes(r,'combinedCleanupVerified','screenrecordAbsentBefore','helperAbsentBefore','stop','revoke-audio','remove','helperAbsentAfter','screenrecordRemoved'))


def prior_safety(runs,digest):
    for path in sorted(runs.glob('tv-*/summary.json'),reverse=True):
        try:
            if safety_proof(json.loads(path.read_bytes()),digest):return path.parent.name
        except (ValueError,TypeError,OSError,AttributeError):continue
    raise ValueError('No current signed-update, consent and full restoration rehearsal; no device contacted')
