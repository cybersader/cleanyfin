"""Physical release interlock. No network, keys, physical target or Android guest."""
from contextlib import ExitStack, redirect_stderr
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from adb_lab import AdbLab,LabError
from tv_internal import harness_digest
from tv_safety import prior_safety,safety_proof
from tv_update import APP,OFFICIAL_CERT,TARGET

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('test_safety_harness',HERE/'tv-harness.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)


def fixture():
    yes=lambda keys:dict.fromkeys(keys.split(),True)
    before={'code':60,'version':'1.0.9','certificate':OFFICIAL_CERT,'package':APP};after=dict(before,code=63,version=TARGET)
    hashes={'servers':'a'*64,'active':'b'*64,'user':'c'*64};settings={'backend':'d'*64,'options':'e'*64}
    return dict(mode='family-safety-only',captureHarnessSha256='1'*64,device={'emulator':True},unexpectedResidue=[],ownedContainersRemaining=0,
        **yes('dryRun restored scratchRemoved privateAdbExited privateNamespaceExited emulatorExited defaultSinkUnchanged defaultSourceUnchanged ownedHostAudioAbsent'),familyUpdate=dict(
        **yes('authenticatedSyntheticSession syntheticBaselineSeeded inPlaceUpdateReported liveLabProfileVerified restoreAttempted exactLabDeleteActivated labAbsentAfter visibleStateRestored'),
        beforeBuild=before,afterUpdateBuild=after,afterRestoreBuild=after,beforeUI=hashes,afterUpdate={'servers':hashes['servers']},afterRestore={'servers':hashes['servers']},labSettingsBefore=settings,labSettingsAfter=settings),
        observations=dict(verifiedSettings={'backend':'MPV','mpvOptions':['hwdec=no']},interruptedRecordingRemoved=True,familyTransactionUnwound=True,idleCapture=yes('freshProjectionCallback videoRemoved')),
        internalRestore=dict(wholphinAbsentBefore=False,**yes('combinedCleanupVerified screenrecordAbsentBefore helperAbsentBefore stop revoke-audio remove helperAbsentAfter screenrecordRemoved')))


class SafetyTests(unittest.TestCase):
    def test_each_missing_fact_blocks(self):
        row=fixture();self.assertTrue(safety_proof(row,'1'*64));self.assertFalse(safety_proof(row,'2'*64))
        for parent in ('','familyUpdate','internalRestore','observations','observations.idleCapture'):
            fields=row
            for s in filter(None,parent.split('.')):fields=fields[s]
            for key in fields:
                with self.subTest(parent=parent,key=key):
                    bad=deepcopy(row);sub=bad
                    for s in filter(None,parent.split('.')):sub=sub[s]
                    del sub[key];self.assertFalse(safety_proof(bad,'1'*64))
        for parent,field,bad in [('afterUpdate','servers','9'*64),('labSettingsAfter','options','9'*64),('afterRestoreBuild','version','other'),('beforeBuild','certificate','unrecognized')]:
            row=fixture();row['familyUpdate'][parent]=dict(row['familyUpdate'][parent],**{field:bad});self.assertFalse(safety_proof(row,'1'*64))

    def test_measurement_limits_neither_arm_nor_veto(self):
        with tempfile.TemporaryDirectory() as w:
            runs=Path(w);m=runs/'tv-z';m.mkdir();d='1'*64
            measurement=dict(fixture(),mode='device-combined',blocker='bookmark seek not witnessed',observations={'procedureCompleted':False,'controls':{'held-out':{'coverageComplete':False}},'positions':[{'takes':{'start':{'bothTapsObserved':False,'sound':{'reason':'no AO packet trace'}}}}]})
            (m/'summary.json').write_text(json.dumps(measurement))
            with self.assertRaises(ValueError):prior_safety(runs,d)
            s=runs/'tv-a';s.mkdir();(s/'summary.json').write_text(json.dumps(fixture()));self.assertEqual(prior_safety(runs,d),'tv-a')
            with self.assertRaises(ValueError):prior_safety(runs,'2'*64)

    def test_interlocks_precede_any_pairing_prompt_or_relay(self):
        args=['h','--device','--r24','--existing-wholphin','update','--no-capture','--frame-rate-match','leave']
        with tempfile.TemporaryDirectory() as w,patch.dict('os.environ',CLEANYFIN_NUGET_FEED=w),patch.object(sys,'argv',args),patch('tv_safety.prior_safety',side_effect=ValueError('no proof')),patch('adb_lab.AdbLab.__enter__') as opened,redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):h.main()
            opened.assert_not_called()
        opts=SimpleNamespace(no_capture=True,existing_wholphin='update',dry_run_emulator=False)
        with tempfile.TemporaryDirectory() as w,ExitStack() as stack,patch('tv_device.connection') as prompt,patch('adb_lab.AdbLab.__exit__',return_value=False),patch('adb_lab.AdbLab.__enter__') as enter:
            root=Path(w);lab=AdbLab(state=root);lab.public=b'test cleanyfin@lab';enter.return_value=lab
            (root/'witness').write_bytes(lab._receipt());lab._relay=Mock()
            for stale in (None,'2'*64):
                if stale:(root/'witness-tv').write_bytes(lab._receipt(harness=stale))
                with self.assertRaises(LabError):h.device_session(opts,root,root,stack,{'captureHarnessSha256':harness_digest(HERE),'emulatorProof':{'familySafety':'private'}},[])
            prompt.assert_not_called();lab._relay.assert_not_called()
            digest=harness_digest(HERE);(root/'witness-tv').write_bytes(lab._receipt(harness=digest));lab._witnessed(harness=digest)
            lab.public=b'changed cleanyfin@lab'
            with self.assertRaises(LabError):lab._witnessed(harness=digest)
