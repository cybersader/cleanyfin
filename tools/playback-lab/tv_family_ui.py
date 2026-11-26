"""Opt-in preservation rehearsal, stock Wholphin controls only; no app-private storage."""
from collections import Counter
import re
import time

from tv_device import APP
from tv_update import Profile


def labels(nodes):
    return [n.get('text') for n in nodes if n.get('package')==APP and n.get('text')]


def identify_profile(names,page):
    # The avatar can contribute one visible initial in addition to the full
    # user/server labels. Verify both names independently on the user picker.
    if page.count('Select User')!=1:raise ValueError('ambiguous signed-in profile')
    i=page.index('Select User')+1;server=page[i] if i<len(page) else None
    if not server or server not in names:raise ValueError('ambiguous signed-in profile')
    others=set(names)-{server};candidates=[]
    for user in others:
        if user not in page[i+1:]:continue
        extras=others-{user}
        if not extras or all(len(v)==1 and v.casefold() in (user[:1].casefold(),server[:1].casefold()) for v in extras):candidates.append(user)
    if len(candidates)!=1:raise ValueError('ambiguous signed-in profile')
    return server,candidates[0]


def exact_user_card(nodes,server,user):
    app=[n for n in nodes if n.get('package')==APP];page=labels(app)
    if page.count('Select User')!=1:raise ValueError('ambiguous signed-in profile')
    i=page.index('Select User')
    if i+1>=len(page) or page[i+1]!=server or page[i+2:].count(user)!=1 or user in ('Add User','Switch servers'):raise ValueError('ambiguous signed-in profile')
    caption=next(n for n in app if n.get('text')==user);rect=lambda n:tuple(map(int,re.findall(r'-?\d+',n.get('bounds',''))));c=rect(caption);hits=[]
    for n in app:
        r=rect(n)
        if n.get('focusable')!='true' or n.get('clickable')!='true' or len(r)!=4 or len(c)!=4:continue
        l,t,w,b=r;inside=caption in tuple(n.iter());badge=labels(n.iter())
        if not inside and not (l<=(c[0]+c[2])/2<=w and b<=c[1]<b+(b-t)/2 and
                               (not badge or all(x.casefold()==user[:1].casefold() for x in badge))):continue
        hits.append(n)
    if len(hits)!=1:raise ValueError('ambiguous signed-in profile')
    return hits[0]


def focus_step(ui,nodes,target):
    focus=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
    if focus is target:return True
    if focus is None:raise ValueError('picker has no focused card')
    rect=lambda n:list(map(int,re.findall(r'-?\d+',n.get('bounds',''))))
    x,y,r,b=rect(target);l,t,w,h=rect(focus);cx=(x+r)//2;cy=(y+b)//2
    if l<=cx<=w and t<=cy<=h:raise ValueError('ambiguous picker focus')
    ui.key('DPAD_UP' if cy<t else 'DPAD_DOWN' if cy>h else 'DPAD_LEFT' if cx<l else 'DPAD_RIGHT');return False


def picker_cards(nodes):
    # Require one complete non-scrolling stock server picker. Never infer that
    # Add Server is the last card or claim hidden servers were enumerated.
    app=[n for n in nodes if n.get('package')==APP]
    if labels(app).count('Select Server')!=1 or any(n.get('scrollable')=='true' for n in app):raise ValueError('server picker not wholly visible')
    cards={};added=0
    for n in app:
        if n.get('focusable')!='true':continue
        values=labels(n.iter());desc=[c.get('content-desc') for c in n.iter()]
        if 'Add Server' in values+desc:added+=1;continue
        if not values and n.get('clickable')!='true':continue
        titles=[x for x in values if not x.startswith(('http://','https://'))]
        if len(titles)>1:titles=[x for x in titles if len(x)>1]
        if len(titles)==1 and len(titles[0])==1:
            # Wholphin's focusable tile contains only the avatar. Its full name is
            # a separate visible caption beneath the tile in the same column.
            rect=lambda e:tuple(map(int,re.findall(r'-?\d+',e.get('bounds',''))))
            box=rect(n);captions=[]
            if len(box)==4:
                l,t,r,b=box
                for e in app:
                    v=e.get('text','');xy=rect(e)
                    if len(xy)==4 and v and v[:1].casefold()==titles[0].casefold() and l<=(xy[0]+xy[2])/2<=r and b<=xy[1]<b+(b-t)/2:captions.append(v)
            if len(captions)!=1:raise ValueError('ambiguous server card')
            titles=captions
        if len(titles)!=1 or titles[0] in cards:raise ValueError('ambiguous server card')
        cards[titles[0]]=n
    if added!=1 or not 1<=len(cards)<=12:raise ValueError('server picker not wholly visible')
    return cards


class FamilyUI:
    def __init__(self,screen):self.ui=screen;self.tv=screen.tv;self.lab_name=None;self.lab_user=None

    def restart(self):
        self.tv.adb('shell','am','force-stop',APP);self.tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity',timeout=20)
        # A cold start may briefly show only a splash screen. Focus-only moves
        # cannot sign in or choose a user, and are bounded by elapsed time.
        end=time.monotonic()+35
        while time.monotonic()<end:
            if self.lab_user:
                page=labels(self.ui.tree())
                if 'Select Server' in page:self.select_known_profile(self.lab_name,self.lab_user);return
                if 'Select User' in page:self.select_user(self.lab_name,self.lab_user);return
            if self.ui.home_drawer():return
        raise ValueError('authenticated Home not verified')

    def focus_profile(self):
        self.restart();s=self.ui
        for _ in range(5):
            if s.get('Settings') is not None:break
            s.key('DPAD_LEFT')
        for _ in range(18):
            ns=s.tree();focus=next((n for n in ns if n.get('package')==APP and n.get('focused')=='true'),None)
            names=labels(focus.iter()) if focus is not None else []
            # Do not activate arbitrary media cards with two visible labels. Only a row in the
            # expanded navigation drawer above its Home item can be the application's profile.
            home=next((n for n in ns if n.get('package')==APP and n.get('text')=='Home'),None)
            rect=lambda n:[int(i) for i in re.findall(r'-?\d+',n.get('bounds',''))] if n is not None else []
            f,b=rect(focus),rect(home)
            if getattr(self.tv,'emulator',None) is not None and self.tv.found.get('emulator'):
                trace=self.tv.family.receipt.setdefault('syntheticDrawerShapes',[])
                if len(trace)<30:
                    # Structural facts plus fixed seeded labels only, never arbitrary text.
                    known={'one','Synthetic Household','Home','Settings','Search','Favorites'}
                    entry={'focusRect':f,'homeTextRect':b,'subtreeLabels':len(names),'seedLabels':sorted(set(names)&known)}
                    if not trace or trace[-1]!=entry:trace.append(entry)
            search=next((n for n in ns if n.get('package')==APP and n.get('text')=='Search'),None)
            top=rect(search)
            if (2<=len(names)<=3 and len(set(names))>=2 and len(f)==len(b)==len(top)==4 and
                f[0]<=b[0]<f[2] and f[3]<=top[1]<b[1]):
                s.key('DPAD_CENTER');s.wait_label('Select User');page=labels(s.tree())
                return identify_profile(names,page)
            s.key('DPAD_UP')
        raise ValueError('cannot read back an authenticated active profile')

    def servers_page(self):
        self.ui.activate('Switch servers');self.ui.wait_label('Select Server')
        for _ in range(12):self.ui.key('DPAD_LEFT')

    def server_entries(self,select=None,delete=False):
        s=self.ui
        for _ in range(16):
            nodes=s.tree()
            if getattr(self.tv,'emulator',None) is not None and self.tv.found.get('emulator'):
                known={'Add Server','Select Server','Synthetic Household','S'}
                self.tv.family.receipt['syntheticPickerCaptions']=[{'rect':n.get('bounds'),'label':n.get('text')} for n in nodes if n.get('package')==APP and n.get('text') in known][:20]
                self.tv.family.receipt['syntheticPickerShapes']=[{'rect':n.get('bounds'),'click':n.get('clickable'),
                    'longClick':n.get('long-clickable'),'scroll':n.get('scrollable'),
                    'labelCount':len(labels(n.iter())),'seedLabels':sorted(set(labels(n.iter()))&known)} for n in nodes
                    if n.get('package')==APP and (n.get('focusable')=='true' or n.get('scrollable')=='true')][:22]
            cards=picker_cards(nodes)
            if select is None:return tuple(sorted(cards))
            if select not in cards:raise ValueError('expected server is not listed')
            if not focus_step(s,nodes,cards[select]):continue
            if not delete:s.key('DPAD_CENTER');return
            if not self.lab_name or select!=self.lab_name:raise ValueError('refusing to remove a non-lab server')
            before=Counter(labels(nodes));self.tv.adb('shell','input','keyevent','--longpress','DPAD_CENTER');s.wait_label('Delete')
            popup=Counter(labels(s.tree()));expected=Counter([select,'Switch servers','Delete'])
            # Stock DialogPopup may replace the accessibility window entirely.
            # In either representation require its full exact lab title plus BOTH actions.
            if popup!=expected and popup!=before+expected:raise ValueError('cannot identify exact server popup')
            s.dpad('Delete',cap=4);return
        raise ValueError('server list cannot be completely enumerated')

    def select_known_profile(self,server,user):
        # Opening Switch servers deliberately leaves the signed-in Home. Relaunching
        # from this picker stays on Select Server. Re-enter only the independently
        # identified original server and already-signed-in user; never authenticate.
        self.ui.wait_label('Select Server')
        for _ in range(12):self.ui.key('DPAD_LEFT')
        self.server_entries(server);self.ui.wait_label('Select User');self.select_user(server,user)

    def select_user(self,server,user):
        for _ in range(16):
            nodes=self.ui.tree();target=exact_user_card(nodes,server,user)
            if focus_step(self.ui,nodes,target):self.ui.key('DPAD_CENTER');break
        else:raise ValueError('ambiguous signed-in profile')
        end=time.monotonic()+28
        while time.monotonic()<end:
            if self.ui.home_drawer():return
        raise ValueError('authenticated Home not verified')

    def verify_lab_home(self,user):
        if not self.lab_name or not user:raise ValueError('synthetic session lacks its verified user label')
        self.lab_user=user
        if self.focus_profile()!=(self.lab_name,user):raise ValueError('active profile is not the signed-in lab session')
        self.select_user(self.lab_name,user)

    def read_backend(self):
        self.restart();self.ui.enter_advanced();return self.ui.focus_backend()

    def change_backend(self,expected,target):
        if {expected,target}!={'MPV','ExoPlayer'} or self.read_backend()!=expected:raise ValueError('cannot verify backend before change')
        self.ui.key('DPAD_CENTER');self.ui.wait_label('Prefer MPV');self.ui.dpad(target,cap=5)
        if self.read_backend()!=target:raise ValueError('backend change not verified')

    def read_settings(self):
        self.restart();s=self.ui;s.enter_advanced();backend=s.focus_backend()
        s.dpad('Edit mpv.conf');s.wait_label('Save');edits=[n for n in s.tree() if n.get('package')==APP and n.get('class')=='android.widget.EditText']
        if len(edits)!=1:raise ValueError('saved mpv options not uniquely readable')
        return backend,edits[0].get('text','')

    def snapshot(self):
        try:
            phase='active profile';active,user=self.focus_profile()
            phase='server selection';self.servers_page()
            phase='server inventory';servers=self.server_entries()
            phase='return from server inventory';self.select_known_profile(active,user)
            phase='visible state'
            return Profile(servers,active,user).validate()
        except Exception as exc:
            # No screen text, names or personal settings in exceptions or receipts.
            codes={'authenticated Home not verified','ambiguous signed-in profile','cannot read back an authenticated active profile',
                   'server picker not wholly visible','ambiguous server card','expected server is not listed','server list has no focused card',
                   'cannot identify exact server popup','duplicate server labels','server list cannot be completely enumerated'}
            detail=str(exc) if str(exc) in codes else type(exc).__name__
            raise ValueError('Stock UI did not verify '+phase+' ('+detail+')') from None

    def inventory(self):
        # After the update never select a family server or profile to manufacture proof.
        self.tv.adb('shell','am','force-stop',APP)
        self.tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity',timeout=20)
        end=time.monotonic()+30
        while time.monotonic()<end:
            page=labels(self.ui.tree())
            if 'Select Server' in page:return self.server_entries()
            if 'Select User' in page:self.servers_page();return self.server_entries()
            if 'Home' in page:self.focus_profile();self.servers_page();return self.server_entries()
            self.ui.key('DPAD_LEFT')  # focus only
        raise ValueError('stock server inventory unavailable')

    def prepare_lab(self,name,original):
        if not re.fullmatch('Cleanyfin Lab [a-f0-9]{10}',name) or name in original.servers:raise ValueError('lab server name is not unique')
        if self.inventory()!=original.servers:raise ValueError('server list changed before lab setup')
        self.lab_name=name

    def restore(self,original,baseline,original_backend):
        try:
            if original_backend is not None:
                server,user=self.focus_profile()
                if server!=self.lab_name:raise ValueError('not in owned lab profile')
                self.servers_page();self.select_known_profile(server,user)
                try:
                    if baseline is not None:
                        backend,options=self.read_settings()
                        if backend!='MPV':raise ValueError('lab options not accessible')
                        if options!=baseline.options:
                            self.restart();self.ui.enter_advanced();self.ui.save_mpv(baseline.options)
                        if self.read_settings()!=('MPV',baseline.options):raise ValueError('changed player options not restored')
                finally:
                    backend=self.read_backend()
                    if backend!=original_backend:self.change_backend(backend,original_backend)
                    if self.read_backend()!=original_backend:raise ValueError('changed player backend not restored')
                if baseline is not None:self.tv.family.receipt['labSettingsAfter']=baseline.proof(self.tv.family.salt)
        finally:
            servers=self.inventory()
            if set(servers)==set(original.servers)|{self.lab_name}:
                self.server_entries(self.lab_name,delete=True);self.ui.wait_label('Select Server')
            if self.server_entries()!=original.servers:raise ValueError('unexpected server inventory after lab cleanup')
            self.tv.family.receipt['labAbsentAfter']=True
