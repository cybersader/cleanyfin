#!/usr/bin/env python3
"""Screen camera witness for the synthetic Cleanyfin hard playback fixture."""
import argparse
from fractions import Fraction
from itertools import groupby
import json
import math
from pathlib import Path
import subprocess
import sys

# The owner invokes this CLI with python -I, which excludes its own directory.
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from camera_picture import (calibrate_camera, ff, local_file, read_picture,
                            streams, write_calibration_clip)
from hard_fixture_lab import FPS

LABEL = 'screen camera witness'
AUDIO_RATE = 8000
AUDIO_WINDOW_MS = 100


def _ceil_ms(delta):
    return math.ceil(delta*1_000_000)/1000  # Don't round a blind interval down.


def summarize_picture(ids, times, *, total, start, end, source_fps=FPS):
    if len(ids) != len(times) or not ids or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('One strictly increasing timestamp is required per camera frame')
    readable = [(time, ident) for time, ident in zip(times, ids) if ident is not None]
    seq = [ident for _, ident in readable]
    seen = set(seq)
    keep = set(range(total))-set(range(start, end))
    marked = seen-keep
    intervals = list(zip(readable, readable[1:]))
    gaps = [b[0]-a[0] for a, b in intervals]
    largest = None
    if gaps:
        (before, first), (after, last) = intervals[gaps.index(max(gaps))]
        largest = dict(beforeFrame=first, afterFrame=last, fromPtsS=float(before), toPtsS=float(after))
    raw_gaps = [b-a for a, b in zip(times, times[1:])]
    frame_duration = Fraction(1, source_fps)
    endpoints = bool(gaps) and seq[0] == 0 and seq[-1] == total-1
    monotonic = all(a <= b for a, b in zip(seq, seq[1:]))
    kept = len(seen & keep)
    # Repeat readings are observations. Unreadable frames do not fill gaps.
    runs = [i for i, _ in groupby(seq)]
    joins = [b[0]-a[0] for a, b in intervals if (a[1], b[1]) == (start-1, end)]
    outcome = 'marked_seen' if marked else ('bounded_no_marked_seen' if endpoints and monotonic else 'inconclusive')
    return dict(outcome=outcome, framesSeen=sorted(seen), seenInRunOrder=runs,
                markedFramesSeen=sorted(marked), markedCameraReadings=sum(i in marked for i in seq),
                keptFramesSeen=kept, keptFramesExpected=len(keep), missingKeptFrames=sorted(keep-seen),
                keptFrameCoverage=kept/len(keep), cameraFrames=len(ids), unreadableCameraFrames=ids.count(None),
                monotonic=monotonic, sourceEndpointsSeen=endpoints,
                largestGapMs=_ceil_ms(max(gaps)) if gaps else None, largestGap=largest,
                largestRawCameraGapMs=_ceil_ms(max(raw_gaps)) if raw_gaps else None,
                joinGapMs=_ceil_ms(max(joins)) if joins else None,
                coverageBound=dict(nominalSourceFrameMs=_ceil_ms(frame_duration),
                    observedCameraFps=round((len(times)-1)/float(times[-1]-times[0]), 3) if raw_gaps else None,
                    maxWholeNominalDwellsPerBlindInterval=max(g//frame_duration for g in gaps) if gaps else None,
                    maxWholeNominalDwellsAcrossBlindIntervals=sum(g//frame_duration for g in gaps) if gaps else None,
                    bracketing='Readable source 0 and end' if endpoints else 'Source endpoints not bracketed',
                    limit='Zero marked reads is not zero marked presentations. A leak can hide between readable '
                          'samples; the largest timestamp gap is the observational limit. Whole-dwell bounds '
                          'apply only inside readable endpoints, assuming instantaneous samples and full nominal '
                          'source-frame dwell. Shorter display holds, rolling shutter, subtle blends, and '
                          'unobserved lead/tail are not bounded. Even 30 camera fps at 25 source fps can miss frames.'))


def _tone_windows(path):
    import numpy as np
    if not streams(path, 'a:0', 'sample_rate'):
        return None
    raw = ff('ffmpeg', '-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:a:0',
             '-ar', AUDIO_RATE, '-ac', 1, '-f', 'f32le', 'pipe:1')
    n = AUDIO_RATE*AUDIO_WINDOW_MS//1000
    wave = np.frombuffer(raw, dtype='<f4')
    blocks = wave[:len(wave)//n*n].reshape(-1, n)
    if not len(blocks):
        return None
    taper = np.hanning(n)
    bins = np.array([440, 660, 880])
    basis = np.exp(-2j*np.pi*np.arange(n)[:, None]*bins/AUDIO_RATE)*taper[:, None]
    peaks = np.abs(blocks @ basis)*2/taper.sum()
    rms = np.sqrt(np.mean(blocks**2, axis=1))
    # Reject related harmonics and unrelated broadband energy.
    dominant = (peaks[:, 2] >= .35*rms) & (peaks[:, 2] >= 1.6*peaks[:, :2].max(axis=1))
    return peaks, dominant


def _tone_report(video, uncut):
    import numpy as np
    result = dict(kind='webcam microphone tone corroboration only', frequencyHz=880,
                  windowMs=AUDIO_WINDOW_MS, outcome='uncontrolled',
                  limit='Acoustic corroboration only; not sample evidence. Microphone gain, room response, '
                        'and short or weak leaks can hide the tone.')
    if uncut is None:
        return dict(result, reason='No uncut microphone control')
    a, b = _tone_windows(local_file(uncut)), _tone_windows(local_file(video))
    if a is None or b is None:
        return dict(result, reason='Recording or uncut control lacks usable microphone audio')
    peaks, dominant = a
    positive = peaks[dominant, 2]
    def kept_present(which, values):
        return int(np.count_nonzero((values[:, which] >= .004) &
                                    (values[:, which] >= 1.6*values[:, 2]))) >= 3
    if (len(positive) < 5 or np.median(positive) < .008 or not all(kept_present(i, peaks) for i in (0, 1))):
        return dict(result, reason='Uncut take did not resolve the 880 Hz positive and both kept-tone controls')
    floor = float(np.quantile(peaks[~dominant, 2], .90))
    cutoff = max(.004, 4*floor, .20*float(np.median(positive)))
    positives = int(np.count_nonzero(positive >= cutoff))
    if positives < 5:
        return dict(result, reason='Uncut 880 Hz tone did not clear its measured background')
    peaks, dominant = b
    hits = np.flatnonzero(dominant & (peaks[:, 2] >= cutoff)).tolist()
    result.update(thresholdPeakFullScale=round(cutoff, 6), uncutPositiveWindows=positives,
                  detectionStartMs=[i*AUDIO_WINDOW_MS for i in hits])
    if hits:
        return dict(result, outcome='tone_observed')
    if not all(kept_present(i, peaks) for i in (0, 1)):
        return dict(result, reason='Filtered microphone take did not resolve both kept tones; absence not interpretable')
    return dict(result, outcome='tone_not_observed_above_threshold')


def score_camera(video: Path, calibration_video: Path, *, uncut_video: Path | None = None,
                 plan: Path | None = None) -> dict:
    """Score a camera recording; raise on bad calibration or unusable video geometry."""
    if plan is None:
        total, start, end = 500, 137, 263
    else:
        from runpy import run_path
        total, start, end = run_path(str(Path(__file__).with_name('analyze-capture.py')))['read_plan'](Path(plan))
    grid, calibration = calibrate_camera(calibration_video)
    ids, times = read_picture(video, grid, calibration, total)
    picture = summarize_picture(ids, times, total=total, start=start, end=end)
    control = None
    if uncut_video is not None:
        uncut_ids, uncut_times = read_picture(uncut_video, grid, calibration, total)
        control = summarize_picture(uncut_ids, uncut_times, total=total, start=start, end=end)
        control['allSourceFramesSeen'] = (len(control['framesSeen']) == total and control['monotonic'])
        if picture['outcome'] == 'bounded_no_marked_seen' and not control['allSourceFramesSeen']:
            picture.update(outcome='inconclusive', reason='Uncut camera take did not witness the entire source in order')
    elif picture['outcome'] == 'bounded_no_marked_seen':
        picture['reason'] = 'No uncut picture control; descriptive witness only'
    tone = (_tone_report(video, uncut_video) if (start, end) == (137, 263) else
            dict(kind='webcam microphone tone corroboration only', outcome='uncontrolled',
                 reason='The 880 Hz negative test only applies to the standard hard-fixture cut'))
    return dict(evidence=LABEL, calibration=calibration, picture=picture, uncutPictureControl=control, tone=tone)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--preflight', action='store_true', help='list UVC modes without changing controls or streaming')
    actions.add_argument('--make-calibration', type=Path, metavar='CLIP.mp4')
    actions.add_argument('--dry-run', '--video', type=Path, metavar='RECORDING', help='score existing files; open no devices')
    parser.add_argument('--camera', help='preflight selection: USB VID:PID or a unique product/video name substring')
    parser.add_argument('--calibration', type=Path, metavar='CALIBRATION_RECORDING')
    parser.add_argument('--uncut', type=Path, metavar='UNCUT_CONTROL_RECORDING')
    parser.add_argument('--plan', type=Path, metavar='PLAN.json')
    args = parser.parse_args(argv)
    if args.camera and not args.preflight:
        parser.error('--camera is only available with --preflight')
    try:
        if args.preflight:
            from camera_uvc import preflight
            result = preflight(args.camera)
        elif args.make_calibration:
            write_calibration_clip(args.make_calibration)
            result = dict(evidence=LABEL, calibrationClip=str(args.make_calibration))
        else:
            if not args.calibration:
                parser.error('--dry-run requires --calibration')
            result = score_camera(args.dry_run, args.calibration, uncut_video=args.uncut, plan=args.plan)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired, EOFError,
            ImportError) as exc:
        print(json.dumps(dict(evidence=LABEL, refused=str(exc))))
        return 2
    print(json.dumps(result, indent=2))
    if 'picture' in result:
        return 0 if result['picture']['outcome'] == 'bounded_no_marked_seen' else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
