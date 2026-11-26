"""Stock Wholphin 1.1.2 transport. A dispatched intent/HTTP command is not a landing."""
from fractions import Fraction
from uuid import UUID


class TransportLimit(ValueError):pass


class StockTransport:
    def __init__(self,h,tv,lab,ui,scorer):self.h,self.tv,self.lab,self.ui,self.scorer=h,tv,lab,ui,scorer

    def wait(self,fn,limit,reason):
        try:return self.h.eventually(fn,limit,reason)
        except RuntimeError as exc:
            if str(exc)!=reason:raise
            raise TransportLimit(reason) from None

    def session(self,info,*,item=None,seek=False):
        lab=self.lab
        def poll():
            rows=[s for s in lab.api('/Sessions',user=lab.admin) if s.get('DeviceId')==lab.tv_device and s.get('UserId','').replace('-','')==lab.viewer and s.get('Client')==lab.client and s.get('ApplicationVersion')==lab.version]
            if item:rows=[s for s in rows if s.get('NowPlayingItem',{}).get('Id','').replace('-','')==item]
            f=info['session']={'matches':len(rows),'supportsRemoteControl':len(rows)==1 and rows[0].get('SupportsRemoteControl') is True,'canSeek':len(rows)==1 and rows[0].get('PlayState',{}).get('CanSeek') is True}
            return rows[0] if len(rows)==1 and (not seek or f['canSeek'] and f['supportsRemoteControl']) else None
        return self.wait(poll,12,'exact Wholphin session not ready for the requested operation')

    def start_at(self,ms,info):
        # IntentService.kt in v1.1.2 reads an Android long called position, in ms.
        # Its WebSocket capabilities flag alone does not prove PlayNow handling.
        self.h.play(self.tv,self.lab,self.lab.pointer,position_ms=ms);info['requestedStartMs']=ms
        self.session(info,item=self.lab.pointer)

    def seek_and_resume(self,target,mark,landings,info):
        h,tv,lab,ui=self.h,self.tv,self.lab,self.ui
        h.play(tv,lab,lab.pointer);s=self.session(info,item=lab.pointer,seek=True)
        if not ui.action('pause'):raise TransportLimit('stock pause unavailable')
        self.wait(ui.paused,8,'stock pause not acknowledged')
        for ms in (target+200 if mark.landing(Fraction(target+200,1000)) is not None else max(0,target-200),target):
            wanted=mark.landing(Fraction(ms,1000));before=h.shown_frame(tv,self.scorer)
            if wanted is None or before is None or before==wanted:raise TransportLimit('no distinct readable origin for seek')
            lab.api('/Sessions/'+s['Id']+'/Playing/Seek?seekPositionTicks='+str(ms*10000),'POST',user=lab.admin)
            self.wait(lambda:h.shown_frame(tv,self.scorer)==wanted,6,'stock seek did not reach the intended barcode');landings.append(wanted)
        if not ui.action('play'):raise TransportLimit('stock resume unavailable')

    def bookmark(self,mark,info):
        # Independent of remote seek. Stop actual playback at a visible kept frame;
        # do not write progress, use old ticks, or count a different session.
        h,tv,lab,ui=self.h,self.tv,self.lab,self.ui
        def ticks():return lab.api('/Users/'+lab.viewer+'/Items/'+lab.pointer,user=lab.one).get('UserData',{}).get('PlaybackPositionTicks',0)
        previous=ticks();h.play(tv,lab,lab.pointer);self.session(info,item=lab.pointer)
        def middle():
            f=h.shown_frame(tv,self.scorer)
            return f if f in mark.kept_frames and 1<=mark.frame_time(f)<=lab.r24_duration-2 else None
        self.wait(middle,14,'no mid-playback kept frame for bookmark')
        if not ui.action('pause'):raise TransportLimit('stock pause unavailable for bookmark')
        self.wait(ui.paused,8,'stock pause not acknowledged for bookmark');frame=middle()
        if frame is None:raise TransportLimit('no paused kept frame before bookmark stop')
        expected=mark.frame_time(frame)*10**7;info['pausedFrameBeforeStop']=frame;self.session(info,item=lab.pointer)
        ui.key('BACK');self.wait(lambda:not h.now(lab,lab.pointer),8,'stock player did not stop')
        def changed():
            t=ticks();return t if type(t) is int and t>0 and t!=previous and abs(t-expected)<=12000000 else None
        tick=self.wait(changed,10,'no new persisted bookmark corresponding to the stopped frame');info['bookmarkTicks']=tick
        tv.adb('shell','am','force-stop',h.APP);return tick

    def resume(self,info):
        h,tv,lab,ui=self.h,self.tv,self.lab,self.ui
        tv.adb('shell','am','start','-n',h.APP+'/.MainActivity','-a','android.intent.action.VIEW','--es','itemId',str(UUID(lab.pointer)),'--es','userId',str(UUID(lab.viewer)),'--es','serverId',str(UUID(lab.server_ref)))
        def unique():
            labels={n.get(k) for n in ui.tree() if n.get('package')==h.APP for k in ('text','content-desc') if n.get(k,'').casefold().split()[:1]==['resume']}
            return labels.pop() if len(labels)==1 else None
        ui.activate(self.wait(unique,15,'no unique stock Resume choice'));info['stockResumeActivated']=True
        self.session(info,item=lab.pointer)


def probe(h,tv,lab,ui,out):
    """One private mark, no control movies or recording takes. Never admission."""
    import format_measure as fm
    if tv.emulator is None or not tv.transport.guest or tv.found.get('emulator') is not True:raise ValueError('transport probe is private only')
    av=lab.r24_anchor;b=lab.r24_binding;mark=fm.Mark(dict(videoAnchorMs=av,durationMs=b['durationMs']),av+5480,av+10520)
    lab.arm(lab.item,b,av+5480,av+10520);lab.r24_duration=(b['durationMs']-5040)/1000
    stock=StockTransport(h,tv,lab,ui,h.module('analyze-capture'));out['admitted']=False;landings=[]
    def resume(info):stock.bookmark(mark,info);stock.resume(info)
    for name,operation in [('start',lambda i:stock.start_at(5480,i)),('seek-resume',lambda i:stock.seek_and_resume(5480,mark,landings,i)),('persisted-resume',resume)]:
        info=out.setdefault('transportProbe',{}).setdefault(name,{});before=h.handoff(lab)[0];tv.clear_log()
        try:
            operation(info)
            def presented():
                f=h.shown_frame(tv,stock.scorer);return (f,) if f is not None else None
            info['presentedFrame']=stock.wait(presented,8,'no readable fixture frame after operation')[0]
        except TransportLimit as exc:info['limit']=str(exc)
        finally:
            info['grantRouteDeltas']=[b-a for a,b in zip(before,h.handoff(lab)[0])];pid=tv.adb('shell','pidof',h.APP).decode().strip()
            if pid.isdecimal():
                runtime={};h.app_runtime(tv.adb('logcat','-d','--pid='+pid,'-v','tag').decode(errors='replace'),runtime);info['audioCodecs']=runtime.get('libmpvAudioCodecs',[])
            tv.adb('shell','am','force-stop',h.APP)
    out['pausedSeekLandings']=landings
