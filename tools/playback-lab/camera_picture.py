"""Optical calibration and conservative barcode decoding for the hard fixture."""
from contextlib import closing
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess

from hard_fixture_lab import BITS, CELL, FPS, HEIGHT, SYNC, WIDTH, X, Y

# Clockwise marker centres in source coordinates, not the actual screen corners.
MARKERS = ((80, 80, '0xffff00'), (1200, 80, '0xff00ff'),
           (1200, 640, '0x00ffff'), (80, 640, '0x00ff00'))
MIN_CONTRAST = 72
CAL_MARGIN = .11
READ_MARGIN = .07


class CalibrationError(ValueError):
    """No sufficiently resolved, fully visible calibration pattern."""


def ff(tool, *args):
    prefix = ['-hide_banner', '-loglevel', 'error', '-nostdin'] if tool == 'ffmpeg' else ['-v', 'error']
    return subprocess.check_output([tool, *prefix, *map(str, args)], stdin=subprocess.DEVNULL)


def local_file(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f'Expected a regular local recording: {path}')
    return path


def probe(path, selector, *args):
    return json.loads(ff('ffprobe', '-protocol_whitelist', 'file,pipe', '-select_streams', selector,
                         *args, '-of', 'json', path))


def streams(path, selector, fields):
    return probe(path, selector, '-show_streams', '-show_entries', 'stream='+fields).get('streams', [])


def _video_info(path):
    hits = streams(path, 'v:0', 'width,height,time_base')
    if not hits or not all(hits[0].get(k, 0) > 0 for k in ('width', 'height')):
        raise ValueError('Recording has no usable video stream')
    return hits[0]


def _frames(path, info, *, calibration=False):
    import numpy as np
    w, h = info['width'], info['height']
    ow, oh = min(w, 1280), max(1, round(h * min(w, 1280) / w))
    pix = 'rgb24' if calibration else 'gray'
    filt = f'scale={ow}:{oh}:flags=area,format={pix}'
    if calibration:
        filt += ',fps=8'  # Only the static validation phases may be subsampled.
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-protocol_whitelist', 'file,pipe', '-noautorotate', '-threads', '2',
               '-i', str(path), '-filter_threads', '1', '-map', '0:v:0', '-an',
               '-vf', filt, *(['-t', '12'] if calibration else []),
               '-fps_mode', 'passthrough', '-threads:v', '1', '-f', 'rawvideo', 'pipe:1']
    shape = (oh, ow, 3) if calibration else (oh, ow)
    size = math.prod(shape)
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE) as proc:
        try:
            while raw := proc.stdout.read(size):
                if len(raw) != size:
                    raise ValueError('Partial decoded camera frame')
                yield np.frombuffer(raw, dtype=np.uint8).reshape(shape)
            if proc.wait():
                raise ValueError('Could not decode camera video')
        finally:
            if proc.poll() is None:
                # Close the pipe first so a blocked producer also sees EOF.
                proc.stdout.close()
                proc.terminate()


def _times(path, info):
    pts = probe(path, 'v:0', '-show_frames', '-show_entries', 'frame=best_effort_timestamp').get('frames', [])
    if not pts or any('best_effort_timestamp' not in p for p in pts):
        raise ValueError('Camera recording lacks frame timestamps')
    base = Fraction(info['time_base'])
    times = [int(p['best_effort_timestamp'])*base for p in pts]
    if base <= 0 or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('Camera timestamps must be strictly increasing')
    return times


def write_calibration_clip(path: Path) -> None:
    """Generate a six-second synthetic calibration fixture to play on the TV box."""
    path = Path(path).resolve()
    if path.suffix.lower() != '.mp4':
        raise ValueError('Calibration clip path must end in .mp4')
    filters = [f'color=c=0x404040:s={WIDTH}x{HEIGHT}:r={FPS}:d=6']
    for x, y, color in MARKERS:
        filters.append(f'drawbox=x={x-36}:y={y-36}:w=72:h=72:color={color}:t=fill')
    filters.append(f'drawbox=x={X}:y={Y}:w={CELL*(len(SYNC)+BITS)}:h={CELL*2}:color=black:t=fill')
    for row in range(2):
        for col in range(len(SYNC)+BITS):
            box = f'drawbox=x={X+col*CELL}:y={Y+row*CELL}:w={CELL}:h={CELL}:color=white:t=fill'
            if col < len(SYNC):
                if SYNC[col] ^ row:
                    filters.append(box)
            else:
                # Each data bit is witnessed in both states; sync stays fixed.
                filters.append(f"{box}:enable='eq(mod(floor(n/13),2),{1-row})'")
    encoders = ff('ffmpeg', '-encoders').decode().split()
    codec = 'libx264' if 'libx264' in encoders else 'libopenh264'
    if codec not in encoders:
        raise ValueError('FFmpeg needs a software H.264 encoder (libx264 or libopenh264)')
    quality = ['-crf', '14'] if codec == 'libx264' else ['-b:v', '2M']
    ff('ffmpeg', '-n', '-f', 'lavfi', '-i', ','.join(filters), '-c:v', codec, *quality,
       '-threads:v', '2', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path)


def _marker_centres(rgb):
    import numpy as np
    h, w, _ = rgb.shape
    r, g, b = rgb.transpose(2, 0, 1).astype(np.int16)
    points = []
    for high, low in (((r, g), (b,)), ((r, b), (g,)), ((g, b), (r,)), ((g,), (r, b))):
        hi, lo = np.minimum.reduce(high), np.maximum.reduce(low)
        ys, xs = np.nonzero((hi >= 130) & (lo <= 110) & (hi-lo >= 65))
        if len(xs) < 36:
            raise CalibrationError('Missing or undersized corner marker')
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        area = (x1-x0+1)*(y1-y0+1)
        if (x0 <= 1 or y0 <= 1 or x1 >= w-2 or y1 >= h-2 or
                len(xs)/area < .42 or area > w*h/20 or min(x1-x0, y1-y0) < 5):
            raise CalibrationError('Corner marker is clipped, distorted, or not isolated')
        points.append((xs.mean(), ys.mean()))
    return points


def _project(matrix, points):
    import numpy as np
    coords = np.asarray(points)
    projected = np.concatenate([coords, np.ones((*coords.shape[:-1], 1))], axis=-1) @ matrix.T
    if (projected[..., 2] <= 0).any():
        raise CalibrationError('Degenerate screen perspective')
    return projected[..., :2]/projected[..., 2:]


def _sampling_grid(points, shape):
    import numpy as np
    # Solve source -> camera homography and rectify only the sampled barcode.
    lhs, rhs = [], []
    for (x, y, _), (u, v) in zip(MARKERS, points):
        lhs.extend([[x, y, 1, 0, 0, 0, -u*x, -u*y], [0, 0, 0, x, y, 1, -v*x, -v*y]])
        rhs.extend([u, v])
    try:
        matrix = np.append(np.linalg.solve(lhs, rhs), 1).reshape(3, 3)
    except np.linalg.LinAlgError as exc:
        raise CalibrationError('Degenerate corner geometry') from exc
    quad = _project(matrix, [(0, 0), (WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)])
    edges = np.roll(quad, -1, axis=0)-quad
    turns = edges[:, 0]*np.roll(edges[:, 1], -1)-edges[:, 1]*np.roll(edges[:, 0], -1)
    h, w = shape
    if ((turns <= 0).any() or (quad < 2).any() or (quad[:, 0] > w-2).any() or (quad[:, 1] > h-2).any()):
        raise CalibrationError('Estimated screen quad is not fully in view')
    grid = [[[(X+(col+dx)*CELL, Y+(row+dy)*CELL)
              for dy in (.24, .37, .50, .63, .76) for dx in (.24, .37, .50, .63, .76)]
             for col in range(len(SYNC)+BITS)] for row in range(2)]
    grid = _project(matrix, grid)
    for edge in (grid[:, :, 4]-grid[:, :, 0], grid[:, :, 20]-grid[:, :, 0]):
        if (np.linalg.norm(edge, axis=-1) < 6).any():
            raise CalibrationError('Barcode cells are too small; move closer or zoom in')
    indices = np.rint(grid).astype(int)
    return (indices[..., 1], indices[..., 0]), quad


def read_cells(values, *, contrast_floor=MIN_CONTRAST, margin=READ_MARGIN):
    """Require every interior sample to be close to a black/white sync reference."""
    import numpy as np
    expected = np.array([SYNC, tuple(1-b for b in SYNC)], dtype=bool)
    sync = values[:, :len(SYNC)]
    dark, light = np.median(sync[~expected]), np.median(sync[expected])
    contrast = float(light-dark)
    if contrast < contrast_floor:
        return None
    normalized = (values-dark)/contrast
    low, high = normalized.min(axis=2), normalized.max(axis=2)
    dark_cells = (low >= -.35) & (high <= .25-margin)
    light_cells = (low >= .75+margin) & (high <= 1.35)
    if (not (dark_cells | light_cells).all() or (dark_cells[0] == dark_cells[1]).any()
            or not np.array_equal(light_cells[:, :len(SYNC)], expected)):
        return None
    ident = sum(int(b) << i for i, b in enumerate(light_cells[0, len(SYNC):]))
    slack = np.where(dark_cells, np.minimum(.25-high, low+.35), np.minimum(low-.75, 1.35-high))
    return ident, contrast, float(slack.min())


def calibrate_camera(calibration_video: Path):
    """Validate both phases; return private rectification plus public diagnostics."""
    import numpy as np
    path = local_file(calibration_video)
    info = _video_info(path)
    grid, quad, shape = None, None, None
    counts, contrast, margin = {0: 0, (1 << BITS)-1: 0}, [], []
    last_error = 'No frames in the calibration recording'
    with closing(_frames(path, info, calibration=True)) as frames:
        for rgb in frames:
            try:
                current_grid, current_quad = _sampling_grid(_marker_centres(rgb), rgb.shape[:2])
            except CalibrationError as exc:
                last_error = str(exc)
                continue
            if grid is None:
                grid, quad, shape = current_grid, current_quad, rgb.shape[:2]
            # A moving camera invalidates geometry, even if its wide bits decode.
            if abs(quad-current_quad).max() > 3:
                raise CalibrationError('Screen moved during calibration; fix the mount or disable subject tracking')
            values = rgb[grid] @ np.array([.299, .587, .114])
            reading = read_cells(values, margin=CAL_MARGIN)
            if reading is not None and reading[0] in counts:
                ident, gap, slack = reading
                counts[ident] += 1
                contrast.append(gap)
                margin.append(slack)
    if min(counts.values()) < 2:
        detail = last_error if grid is None else 'both dark/light phases did not clear every bit margin twice'
        raise CalibrationError(f'Calibration refused: {detail}')
    return grid, dict(recordingSize=[info['width'], info['height']], scanSize=[shape[1], shape[0]],
                      screenQuadPx=quad.round(2).tolist(), phaseReadings=counts,
                      minSyncContrast=round(min(contrast), 3), minBitMargin=round(min(margin), 4),
                      requiredBitMargin=CAL_MARGIN)


def read_picture(video, grid, calibration, total):
    path = local_file(video)
    info = _video_info(path)
    if [info['width'], info['height']] != calibration['recordingSize']:
        raise CalibrationError('Witness take dimensions differ from calibration; recalibrate in the same mode')
    times = _times(path, info)
    ids = []
    with closing(_frames(path, info)) as frames:
        for frame in frames:
            reading = read_cells(frame[grid], contrast_floor=max(MIN_CONTRAST, .60*calibration['minSyncContrast']))
            ids.append(reading[0] if reading is not None and reading[0] < total else None)
    if len(ids) != len(times):
        raise ValueError('Camera timestamp count does not match decoded frames')
    return ids, times
