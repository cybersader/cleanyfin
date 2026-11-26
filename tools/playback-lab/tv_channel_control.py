"""Independent channel-ID source and held-out validation of a capture mix. Synthetic only."""
import hashlib
import numpy as np
from scipy.signal import correlate
from hard_fixture_lab import media

NAME='Projection channel identification.mkv'
RATE=48000


def generate(directory,codec):
    # Train and test on disjoint passes of 6 isolated chirps. LFE stays below 100 Hz.
    expr=[]
    for ch in range(6):
        terms=[]
        for k in range(2):
            start=1+ch+k*6; hz=(41+9*k if ch==3 else 311+ch*109+k*71);sweep=(32 if ch==3 else 280+ch*19)
            t=f'(t-{start})';terms.append(f'if(between(t,{start},{start+.8}),{.10+.045*k}*sin(2*PI*({hz}*{t}+{sweep}*{t}*{t}/2)),0)')
        expr.append('+'.join(terms))
    media(directory,'ffmpeg','-f','lavfi','-i','color=c=gray:s=320x180:r=25:d=14','-f','lavfi','-i',
          "aevalsrc='"+'|'.join(expr)+"':s=48000:c=5.1(side):d=14",'-c:v','libx264','-threads:v','2','-c:a',codec,'-b:a','448k','/lab/'+NAME)


def decode(directory,name):
    return media(directory,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+name,'-map','0:a:0','-ar','48000','-f','f32le','pipe:1',readonly=True)


def samples(raw,ch,enc):
    if not 2<=ch<=6 or enc not in (2,4) or not raw or len(raw)%(ch*(4 if enc==4 else 2)):raise ValueError('Incomplete capture sample frames')
    x=np.frombuffer(raw,dtype='<f4' if enc==4 else '<i2').reshape(-1,ch)
    if not np.isfinite(x).all():raise ValueError('Invalid capture sample values')
    return x


def calibration(ref,raw,ch,enc):
    x=samples(ref,6,4);y=samples(raw,ch,enc).astype(float)/(32768 if enc==2 else 1);passes=[]
    # Get the capture clock independently from audible ID pulses, allowing for
    # missing LFE. Recheck every lane near that clock, including quiet lanes.
    for k in range(2):
        offsets=[];pulses=[]
        for c in range(6):
            at=int(RATE*(1.15+c+k*6));w=x[at:at+24000,c].astype(float);power=float(w@w)
            if power<.01:raise ValueError('No decoded channel identification tone')
            # Estimate the common clock at 6 kHz (all identification tones are below 2 kHz).
            scores=[]
            for j in range(ch):
                z=y[::8,j];v=w[::8];e=np.r_[0.,np.cumsum(z*z)];wp=v@v
                scores.append(abs(correlate(z,v,mode='valid',method='fft'))/np.sqrt(np.maximum(wp*.0025,e[len(v):]-e[:-len(v)])*wp))
            magnitude=np.max(scores,axis=0);i=int(magnitude.argmax());pulses.append((at,w,power))
            if magnitude[i]>.85:offsets.append(i*8-at)
        if len(offsets)<2:raise ValueError('No independently identifiable calibration channel pulses')
        center=int(np.median(offsets));rows=[];fits=[];lags=[];held=[]
        for c,(at,w,power) in enumerate(pulses):
            lo=max(0,at+center-9600);hi=min(len(y),at+center+9600+len(w))
            if hi-lo<len(w):raise ValueError('Calibration pulse outside capture')
            hits=np.array([correlate(y[lo:hi,j],w,mode='valid',method='fft')/power for j in range(ch)])
            scores=[]
            for j in range(ch):
                z=y[lo:hi,j];e=np.r_[0.,np.cumsum(z*z)];scores.append(abs(hits[j])*np.sqrt(power/np.maximum(power*.0025,e[len(w):]-e[:-len(w)])))
            mag=np.max(scores,axis=0);i=int(mag.argmax()) if mag.max()>.85 else int(np.clip(at+center-lo,0,len(mag)-1))
            weights=hits[:,i];actual=y[lo+i:lo+i+len(w)]
            fits.append(float(np.sqrt(np.mean((actual-w[:,None]*weights)**2))));rows.append(weights);lags.append(lo+i-at)
            if k:held.append(float(np.sqrt(np.mean((actual-w[:,None]*passes[0][0][c])**2))))
        passes.append((np.array(rows),fits,lags,held))
    matrix,train,offsets,_=passes[0];held,_,offsets2,residual=passes[1];worst=float(np.max(np.abs(held-matrix)))
    result={'trainingCoefficients':matrix.round(8).tolist(),'heldOutCoefficients':held.round(8).tolist(),
            'trainingResidualRms':train,'heldOutResidualRms':residual,'heldOutCoefficientMaxDelta':worst,
            'trainingOffsetsFrames':offsets,'heldOutOffsetsFrames':offsets2,
            'validated':worst<.015 and max(train+residual)<.002,'independentHeldOutPass':True}
    if enc==2:
        # Identify integer conversion on active samples, not on the silent lanes. Coefficients
        # are already frozen. Select on pass one; require the same rule to predict pass two.
        counts=[]
        for k,lags in enumerate((offsets,offsets2)):
            hits={rule:0 for rule in QUANTIZERS};total=0
            for c,lag in enumerate(lags):
                at=int(RATE*(1.15+c+k*6));v=x[at:at+24000].astype(float)@matrix*32768
                actual=np.rint(y[at+lag:at+lag+24000]*32768);active=abs(v)>=2
                for rule,fun in QUANTIZERS.items():hits[rule]+=int(np.count_nonzero(active & (np.clip(fun(v),-32768,32767)==actual)))
                total+=int(np.count_nonzero(active))
            counts.append({name:h/max(total,1) for name,h in hits.items()})
        ordered=sorted(counts[0],key=counts[0].get,reverse=True);winner=ordered[0]
        valid=counts[0][winner]>.99 and counts[1][winner]>.99 and counts[0][winner]-counts[0][ordered[1]]>.02
        result.update(quantization=winner,quantizerExactFractions=counts,quantizerVerified=valid)
        result['validated'] &= valid
    return matrix,result


QUANTIZERS={'nearest-even':np.rint,'toward-zero':np.trunc,'floor':np.floor}


def predict(raw,matrix,enc,quantizer='nearest-even'):
    mixed=samples(raw,6,4).astype(float)@matrix
    return (np.clip(QUANTIZERS[quantizer](mixed*32768),-32768,32767).astype('<i2') if enc==2 else mixed.astype('<f4')).tobytes()


def observation(ref,raw,ch,enc):
    width=ch*(4 if enc==4 else 2);x=samples(ref,ch,enc);y=samples(raw,ch,enc);anchor=ref[4800*width:4896*width];hit=raw.find(anchor)
    at=hit-4800*width;full=bool(at>=0 and at%width==0 and raw.find(anchor,hit+1)<0 and raw[at:at+len(ref)]==ref)
    out={'referenceFrames':len(x),'captureFrames':len(y),'nonSilentFrames':int(np.count_nonzero(np.any(y!=0,axis=1))),
         'referenceSha256':hashlib.sha256(ref).hexdigest(),'captureSha256':hashlib.sha256(raw).hexdigest(),
         'fullReferencePresent':raw.find(ref)>=0,'outcome':'unmeasured','differenceUnits':'normalized float' if enc==4 else 's16 LSB'}
    if full:
        outside=int(np.count_nonzero(np.any(y[:at//width]!=0,axis=1))+np.count_nonzero(np.any(y[at//width+len(x):]!=0,axis=1)))
        out.update(outcome='deviation' if outside else 'bit-exact',outsideNonzeroFrames=outside,mismatchedValues=0);return out
    if len(y)<24000 or not out['nonSilentFrames']:return out
    # Fit no parameters here. Locate three source windows without requiring a player EOF event.
    marks=[];signal=y[:,0].astype(float);e=np.r_[0.,np.cumsum(signal**2)];n=24000
    for at in (0,len(x)//2,len(x)-n):
        w=x[at:at+n,0].astype(float);power=float(w@w)
        if power==0:return out
        scores=correlate(signal,w,mode='valid',method='fft')/np.sqrt(np.maximum(power*1e-6,e[n:]-e[:-n])*power)
        best=int(np.argmax(scores));peak=float(scores[best]);scores[max(0,best-95):best+96]=0
        m={'sourceFrame':at,'captureOffsetFrames':best-at,'correlation':peak,'competingPeak':float(scores.max())}
        if peak>.9 and peak-m['competingPeak']>.005:
            d=y[best:best+n].astype(float)-x[at:at+n];m.update(mismatchedValues=int(np.count_nonzero(d)),maxAbsoluteDifference=float(np.max(abs(d))))
        marks.append(m)
    out['landmarks']=marks;offsets=[m['captureOffsetFrames'] for m in marks]
    if all('mismatchedValues' in m for m in marks) and len(set(offsets))==1 and 0<=offsets[0]<=len(y)-len(x):
        d=y[offsets[0]:offsets[0]+len(x)].astype(float)-x;out.update(outcome='deviation',mismatchedValues=int(np.count_nonzero(d)),maxAbsoluteDifference=float(np.max(abs(d))))
    return out
