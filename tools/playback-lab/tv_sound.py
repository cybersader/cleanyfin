"""Source-position witness for the captured stereo mix, not an AO packet trace.

Each ear is searched independently. The index proposes positions, never verdicts:
verify the full waveform (not the fingerprint) and the uninterrupted kept path.
No picture clock, stored edited media, alignment of individual seams, or learnt
negative threshold. Only bit-zero recorder padding outside playback is removed.
"""
import base64
import zlib
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.spatial import cKDTree
from tv_pcm import pcm

RATE = 48000
WINDOW, HOP = 240, 120  # 5 ms, 50% overlap; 7.5 ms contains a full window at any phase
RESOLUTION = 480  # 10 ms empirical reporting floor, NOT the 7.5 ms geometric floor
VERSION = 'tv-spread-v1'
# A position ID is deliberately easier than a clearance verdict. Unexplained
# audible energy may coexist with a perfectly recognizable kept block.
MAX_REL_RMS = .085
FEATURE = np.rint(np.linspace(2, WINDOW-4, 14)).astype(int)


def source_pcm(n, seed=0x43544631):
    """5.1(side): same aperiodic, band-limited-ish baseband on every full-range
    lane, with different positive gains. LFE is silent (never the witness).
    Linear interpolation between random chips avoids sensitivity to sub-sample phase.
    """
    from format_fixtures import splitmix64
    chips = (splitmix64(seed, n//8+2) >> np.uint64(32)).astype(float) / 2**31 - 1
    wave = np.interp(np.arange(n)/8, np.arange(len(chips)), chips)
    return (wave[:, None] * [.21, .19, .16, 0, .13, .11]).astype('<f4')


def normalize(a):
    return a / np.maximum(1., np.linalg.norm(a, axis=-1, keepdims=True))


class Index:
    """On a 4-sample grid; retrieve three candidates and refine their phase.
    The held-out control tests the same global search, not known expected lags.
    """
    def __init__(self, signal):
        self.x = np.ascontiguousarray(signal, dtype=float)
        self.windows = sliding_window_view(self.x, WINDOW)
        # Codec priming and end padding are not stationary random code. Keep
        # native-phase candidates on both flanks; never silently crop a pre-roll.
        size=len(self.windows); edge=min(2*WINDOW,size)
        self.grid=np.unique(np.r_[np.arange(0,size,4),np.arange(edge),np.arange(size-edge,size)])
        features = self.x[self.grid[:,None]+FEATURE]
        self.tree = cKDTree(normalize(features))

    def match(self, blocks, times):
        _, ix = self.tree.query(normalize(blocks[:, FEATURE]), k=3)
        starts = np.clip(self.grid[ix][:, :, None] + np.arange(-3,4), 0, len(self.windows)-1).reshape(len(blocks), -1)
        # Full-window, positive-gain normalized correlation at integer lags.
        x = self.windows[starts]; dot = np.einsum('bkw,bw->bk', x, blocks)
        xx = np.einsum('bkw,bkw->bk', x, x)
        scores = dot / np.sqrt(np.maximum(1, xx * np.sum(blocks**2, axis=1)[:,None]))
        row=np.arange(len(blocks)); best=scores.argmax(axis=1)
        anchors=np.flatnonzero(scores[row,best]>.985)
        if len(anchors) and np.any(scores[row,best]<.975):
            # A global fingerprint can miss near half-sample phase. A nearby
            # independently matched window may propose another lag, never an ID.
            k=anchors[np.abs(times[:,None]-times[anchors]).argmin(axis=1)]
            mid=starts[k,best[k]]+times-times[k]
            extra=np.clip(mid[:,None]+np.arange(-3,4),0,len(self.windows)-1)
            x=self.windows[extra]; d=np.einsum('bkw,bw->bk',x,blocks); power=np.einsum('bkw,bkw->bk',x,x)
            starts=np.c_[starts,extra]; dot=np.c_[dot,d]; xx=np.c_[xx,power]
            scores=np.c_[scores,d/np.sqrt(np.maximum(1,power*np.sum(blocks**2,axis=1)[:,None]))]
            best=scores.argmax(axis=1)
        pos=starts[row,best]
        # A remote near-tie is not a source ID. Adjacent samples of one peak are not competitors.
        ambiguous = np.any((np.abs(starts-pos[:,None])>4) & (scores>=scores[row,best,None]-.02), axis=1)
        ref = self.windows[pos]; gain = dot[row,best]/np.maximum(1,xx[row,best]); residual = blocks - gain[:,None]*ref
        # Model a fractional sample with the adjacent reference. Positive
        # interpolation weights only; no competing waveform is fitted.
        phase=np.zeros(len(pos)); slope=np.zeros(len(pos))
        for step in (-1,1):
            nb=self.windows[np.clip(pos+step,0,len(self.windows)-1)]
            delta=nb-ref
            rr=np.einsum('bw,bw->b',ref,ref); rd=np.einsum('bw,bw->b',ref,delta)
            dd=np.einsum('bw,bw->b',delta,delta); ry=np.einsum('bw,bw->b',ref,blocks)
            dy=np.einsum('bw,bw->b',delta,blocks); det=np.maximum(1.,rr*dd-rd*rd)
            g=(dd*ry-rd*dy)/det; h=(rr*dy-rd*ry)/det
            alt=blocks-g[:,None]*ref-h[:,None]*delta
            better=(h>=0)&(h<=g)&(np.sum(alt**2,axis=1)<np.sum(residual**2,axis=1))
            residual=np.where(better[:,None],alt,residual)
            phase=np.where(better,step*h/np.maximum(g,1e-9),phase)
        # Within-window clock drift is measurable even across 5 ms. Fit a
        # positive gain and affine sampling phase on this ONE candidate. Bounds
        # below keep this a clock model, not a free per-sample waveform fit.
        t=np.linspace(-1.,1.,WINDOW); native=pos[:,None]+np.arange(WINDOW)
        for _ in range(5):
            u=np.clip(native+phase[:,None]+slope[:,None]*t,0,len(self.x)-1)
            k=np.minimum(u.astype(int),len(self.x)-2); d=self.x[k+1]-self.x[k]
            v=self.x[k]+(u-k)*d
            mat=np.stack([v,d,d*t],axis=2)
            gram=np.einsum('bwi,bwj->bij',mat,mat)+np.eye(3)*1e-6
            rhs=np.einsum('bwi,bw->bi',mat,blocks)
            fit=np.linalg.solve(gram,rhs[...,None])[...,0]
            g=np.maximum(fit[:,0],1e-8); alt=blocks-v*g[:,None]
            better=np.sum(alt**2,axis=1)<np.sum(residual**2,axis=1)
            residual=np.where(better[:,None],alt,residual)
            phase=np.clip(phase+fit[:,1]/g,-1.,1.)
            slope=np.clip(slope+fit[:,2]/g,-WINDOW*.0005,WINDOW*.0005)
        rel = np.linalg.norm(residual, axis=1)/np.maximum(1,np.linalg.norm(blocks,axis=1))
        ok = (~ambiguous) & (rel <= MAX_REL_RMS) & (gain>0) & (np.max(np.abs(blocks),axis=1)>=32)
        return pos, ok, rel, np.max(np.abs(residual),axis=1)


def android_mix(six):
    """Hypothesis from the measured channel control, NOT authority about a TV.
    Float reference in s16 LSB units, before per-ear gain/quantization. Validate
    on two complete uncut takes on the actual route. Never fit a movie edit.
    """
    if six.ndim!=2 or six.shape[1]!=6:raise ValueError('expected decoded 5.1(side)')
    c=2**-.5
    return (six.astype(float) @ np.array([[1,0],[0,1],[c,c],[.5,.5],[c,0],[0,c]]))*32768


class Witness:
    def __init__(self, reference):
        self.src = pcm(reference) if isinstance(reference,bytes) else np.asarray(reference)
        if self.src.ndim!=2 or self.src.shape[1]!=2:raise ValueError('expected stereo reference')
        self.idx = [Index(x) for x in self.src.T]

    def score(self, raw, mark=None, start=0, preroll=0):
        """start is expected filtered-timeline sample (None: diagnostic only).
        Evaluate both the source and the single legal stitched boundary. No
        synthetic candidate is written to disk. A split-ear leak is a positive.
        """
        a,b = (0,0) if mark is None else mark
        n = len(self.src); b=min(b,n)
        if not 0<=a<=b<=n:raise ValueError('mark outside reference')
        row = dict(detector=VERSION, resolutionMs=1000*RESOLUTION/RATE, blockFrames=WINDOW,
                   hopFrames=HOP, outcome='unmeasured', blockOutcome='unmeasured', clear=False,
                   markedSoundDetections=None, unidentifiedBlocks=None, blocks=0)
        if not raw or len(raw)%4:return dict(row, reason='empty or partial stereo transfer')
        y = pcm(raw); nz = np.flatnonzero(np.any(y,axis=1))
        if not len(nz) or nz[-1]-nz[0]+1<WINDOW:return dict(row,reason='no complete active block')
        lo,hi = int(nz[0]),int(nz[-1])+1; y = y[lo:hi]
        times = np.unique(np.r_[np.arange(0,len(y)-WINDOW+1,HOP),len(y)-WINDOW]); blocks=len(times)
        left=max(0,a-WINDOW); right=min(n,b+WINDOW)
        seam = np.concatenate((self.src[left:a],self.src[b:right]))
        joins = [Index(x) for x in seam.T] if a and b>a and b<n and len(seam)>=WINDOW+8 else None
        detected=np.zeros(blocks,bool); paths=[]; worst=0.; exact=True
        for ch, index in enumerate(self.idx):
            path=np.full(blocks,-1); view=sliding_window_view(y[:,ch],WINDOW)
            for j in range(0,blocks,48):  # bounded working set even for a full 90 s transfer
                sl=slice(j,j+48); obs=view[times[sl]].astype(float)
                pos,ok,rel,peak=index.match(obs,times[sl])
                marked = ok & (b>a) & (pos>=a) & (pos+WINDOW<=b)
                kept = ok & ((pos+WINDOW<=a)|(pos>=b)); flat=np.where(pos>=b,pos-b+a,pos)
                # A genuine cut may split a block anywhere, including a single
                # sample from one flank. Evaluate this competing legal hypothesis.
                if joins and np.any(~kept | ((flat>=a-WINDOW-4)&(flat<=a+4))):
                    jp,jok,jrel,jpeak=joins[ch].match(obs,times[sl]); jp+=left
                    prefer=jok & (~kept | (jrel<rel))
                    # If a positive and an equally good kept explanation coexist, abstain.
                    tie=marked & jok
                    marked &= ~tie; prefer &= ~tie
                    flat=np.where(prefer,jp,flat); peak=np.where(prefer,jpeak,peak)
                    kept |= prefer
                detected[sl] |= marked
                path[sl]=np.where(marked,-pos-2,np.where(kept,flat,-1))
                if np.any(kept):worst=max(worst,float(peak[kept].max()))
                exact &= bool(np.all(peak[kept]<=1.0))
            paths.append(path)
        # Don't certify a short excerpt, duplicated interval, a hole in the tap,
        # or different left/right landings. Permit only local quantization of lag
        # and <=0.1% cumulative rate error; never fit away an edit.
        p,q=paths; known=(p>=0)&(q>=0)&(np.abs(p-q)<=1)
        unknown=int((~(known|detected)).sum()); hits=int(detected.sum()); length=n-b+a
        # An uncut capture may begin at decoder priming or video zero. Nothing
        # recorded is discarded here. Only an exactly digital-zero source flank
        # is exempt, symmetrically with the recorder's bit-zero idle padding.
        expected=np.concatenate((self.src[:a],self.src[b:])) if mark else self.src
        def edge_ok(actual,target,available,yblock,refblock):
            if abs(actual-target)<=2:return True
            left,right=sorted((actual,target))
            if available<right-left or right-left>WINDOW or left<0 or right>length:return False
            # Digital-zero recorder padding can include a sub-LSB codec flank.
            # It is accounted for against the same decoded programme, not a
            # threshold used to trim the recorded waveform. Never discard a
            # nonzero recorded sample or forgive missing active programme here.
            gain=(np.max(np.abs(yblock.astype(float)),axis=0)+1)/np.maximum(1.,np.max(np.abs(refblock),axis=0))
            return bool(np.max(np.abs(expected[left:right])*gain)<=1.)
        def covers():
            if not known.all() or start is None:return False
            begin=int(p[0]); end=int(p[-1])+WINDOW
            if end>length or begin<0:return False
            return (any(edge_ok(begin,v,lo,y[:WINDOW],expected[begin:begin+WINDOW]) for v in {start,start+preroll}) and
                    edge_ok(end,length,len(raw)//4-hi,y[-WINDOW:],expected[end-WINDOW:end]))
        continuity=bool(covers() and
                        np.all(np.abs(np.diff(p)-np.diff(times))<=2) and
                        np.all(np.abs(p-p[0]-times)<=2+.001*times))
        # A mark shorter than the calibrated resolution cannot get a negative
        # verdict even if nominal correlation looks perfect.
        eligible=not mark or b-a>=RESOLUTION
        block='fail' if hits else 'pass' if continuity and eligible else 'unmeasured'
        outcome='unmeasured' if block=='pass' and not exact else block
        # A compact auditable map for every active block in each ear (no audio).
        # Decode zlib/base64 to LE int32 deltas, cumsum: -1 unknown, -(source+2)
        # marked, otherwise the sample index on the ideal concatenated kept path.
        trace=[base64.b64encode(zlib.compress(np.diff(v,prepend=0).astype('<i4').tobytes())).decode('ascii') for v in paths]
        return dict(row,traceDeltaZlibLR=trace,sourceMarkFrames=[a,b],activeFrames=len(y),blocks=blocks,zeroPaddingFrames=[lo,len(raw)//4-hi],
                    markedSoundDetections=hits,unidentifiedBlocks=unknown,keptBlocks=int((known&~detected).sum()),
                    continuousCoverage=continuity, maxKeptResidualLSB=round(worst,3),
                    blockOutcome=block,outcome=outcome,clear=outcome=='pass',
                    reason=('identified marked block' if hits else 'unidentified block' if unknown else
                            'missing/unsupported playback coverage' if not continuity else 'mark below resolution' if not eligible else
                            'unexplained mix residual; audibility not established' if not exact else 'all blocks identified; residual <=1 capture LSB'))


def route_challenge(witness, controls, mark):
    """Replay one independently mapped block per *uncut capture* through the
    mark-aware classifier. A positive well inside this mark and a kept negative
    from a different take must both resolve, on this route and this edition.
    """
    result={'passed':False}; a,b=mark
    if b-a<RESOLUTION:return dict(result,reason='mark below calibrated resolution')
    for label,want in [('positive',True),('held-out',False)]:
        raw,control=controls[label]
        if not control.get('soundCalibrated'):return dict(result,reason='incomplete uncut controls')
        row=control['soundControl']; left,right=[np.frombuffer(zlib.decompress(base64.b64decode(t)),'<i4').cumsum() for t in row['traceDeltaZlibLR']]
        lo=np.minimum(left,right);hi=np.maximum(left,right)+WINDOW
        good=(lo>=a)&(hi<=b) if want else (hi<=a)|(lo>=b)
        which=np.flatnonzero(good)
        if not len(which):return dict(result,reason='no independently located control block')
        i=int(which[len(which)//2]); t=min(i*HOP,row['activeFrames']-WINDOW)+row['zeroPaddingFrames'][0]
        check=witness.score(raw[t*4:(t+WINDOW)*4],mark,None)
        ok=check['markedSoundDetections']==int(want) and check['unidentifiedBlocks']==0
        result[label]={'captureFrame':t,'sourceFrameLR':[int(left[i]),int(right[i])],'expectedMarked':want,'ok':ok}
        if not ok:return dict(result,reason='route challenge did not identify the expected material')
    return dict(result,passed=True)
