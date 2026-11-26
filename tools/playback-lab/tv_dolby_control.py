"""Account for Dolby decoder pre-roll on the edition's clock, without padding or tail trimming."""
from fractions import Fraction
import hashlib

import format_fixtures as ff
import format_measure as fm
from hard_fixture_lab import media


def clock(probe,origin,video_frames):
    try:
        stream,=probe['streams'];tb=Fraction(stream['time_base']);decoded=probe['frames']
        if int(stream['sample_rate'])!=48000 or not decoded or tb<=0:raise ValueError()
        begin=Fraction(int(decoded[0]['pts']))*tb*48000;end=begin
        for frame in decoded:
            n=int(frame['nb_samples'])
            if n<=0 or Fraction(int(frame['pts']))*tb*48000!=end:raise ValueError()
            end+=n
        if begin.denominator!=1 or not begin<=origin<end<=origin+video_frames:raise ValueError()
        if origin+video_frames-end>=max(int(frame['nb_samples']) for frame in decoded):raise ValueError()  # never accept a missing whole coded packet
    except (KeyError,TypeError,IndexError,ArithmeticError,ValueError) as exc:
        raise ValueError('No contiguous integral Dolby decoder clock covering video zero') from exc
    return int(begin),int(end-begin),int(origin-begin)


def verify(whole,prefix,reference,decoded_frames,skip):
    if not (0<=skip<decoded_frames and len(whole)==4*decoded_frames and len(prefix)==4*skip
            and len(reference)==4*(decoded_frames-skip) and whole==prefix+reference):
        raise ValueError('Full uncut decode differs from the independently decoded pre-roll plus reference')


def read(lab,matrix):
    name=lab.r24_layout+'.'+ff.LAYOUTS[lab.r24_layout]['container'];plan=fm.plan_for(matrix.probe_json(lab.fixtures,name))
    origin=(plan['videoAnchorMs']+plan['zeroMs'])*48;video_frames=fm.FRAMES*fm.SAMPLES_PER_FRAME
    probe=ff.probe(lab.fixtures,name,'-select_streams','a:0','-show_entries','frame=pts,nb_samples:stream=sample_rate,time_base')
    begin,decoded_frames,skip=clock(probe,origin,video_frames)
    args=(lab.fixtures,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+name,'-map','0:a:0');tail=('-ac','2','-ar','48000','-f','s16le','pipe:1')
    whole=media(*args,*tail,readonly=True)
    prefix=media(*args,'-af',f'atrim=end_sample={skip}',*tail,readonly=True) if skip else b''
    reference=media(*args,'-af',f'aresample=48000:first_pts={origin},atrim=end_sample={video_frames}',*tail,readonly=True)
    verify(whole,prefix,reference,decoded_frames,skip)
    facts=dict(referenceFrames=len(reference)//4,expectedReferenceFrames=decoded_frames-skip,pictureClockFrames=video_frames,
               firstDecodedSample=begin,referenceOrigin=origin,decodedFrames=decoded_frames,discardedBeforeOrigin=skip,
               prefixAndReferenceVerified=True,prefixSha256=hashlib.sha256(prefix).hexdigest(),referenceSha256=hashlib.sha256(reference).hexdigest())
    return reference,whole,facts


def playback_mix_reference(lab,decoded_frames):
    """Preserve the already-verified whole decoder clock, including any priming."""
    import numpy as np
    from tv_sound import android_mix
    name=lab.r24_layout+'.'+ff.LAYOUTS[lab.r24_layout]['container']
    raw=media(lab.fixtures,'ffmpeg','-copyts','-cons_noisegen','1','-i','/lab/'+name,'-map','0:a:0',
              '-ch_layout','5.1(side)','-ar','48000','-f','f32le','pipe:1',readonly=True)
    if len(raw)!=decoded_frames*6*4:raise ValueError('unexpected six-channel decoder length')
    return android_mix(np.frombuffer(raw,'<f4').reshape(-1,6)),hashlib.sha256(raw).hexdigest()
