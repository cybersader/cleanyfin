"""Opt-in preservation rehearsal, stock Wholphin controls only; no app-private storage."""
from collections import Counter
import re
import time

from tv_device import APP
from tv_update import Visible


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


class FamilyUI:
    def __init__(self,screen):self.ui=screen;self.tv=screen.tv;self.lab_name=None

    def restart(self):
        self.tv.adb('shell','am','force-stop',APP);self.tv.adb('shell','am','start','-W','-n',APP+'/.MainActivity',timeout=20)
        # A cold start may briefly show only a splash screen. Focus-only moves
        # cannot sign in or choose a user, and are bounded by elapsed time.
        end=time.monotonic()+35
        while time.monotonic()<end:
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
        s=self.ui;seen=[]
        for _ in range(13):
            nodes=s.tree();focus=next((n for n in nodes if n.get('package')==APP and n.get('focused')=='true'),None)
            if focus is None:raise ValueError('server list has no focused card')
            if any(n.get('resource-id','').endswith('add_server') or n.get('content-desc')=='Add Server' for n in focus.iter()):
                if select is not None:raise ValueError('expected server is not listed')
                return tuple(sorted(seen))
            before=Counter(labels(nodes));self.tv.adb('shell','input','keyevent','--longpress','DPAD_CENTER');s.wait_label('Delete')
            # A generic Delete button or the selected card's initial is not enough. The
            # *new dialog* must introduce exactly the non-family lab name plus its actions.
            extra=list((Counter(labels(s.tree()))-before).elements());title=[x for x in extra if x not in ('Delete','Switch servers')]
            if len(title)!=1 or 'Delete' not in extra or 'Switch servers' not in extra:raise ValueError('cannot identify exact server popup')
            name=title[0]
            if name in seen:raise ValueError('duplicate server labels')
            seen.append(name)
            if name==select:
                if delete and (not self.lab_name or name!=self.lab_name):raise ValueError('refusing to remove a non-lab server')
                s.dpad('Delete' if delete else 'Switch servers',cap=4);return
            s.key('BACK');s.wait_label('Select Server');s.key('DPAD_RIGHT')
        raise ValueError('server list cannot be completely enumerated')

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
            phase='player settings';backend,options=self.read_settings()
            phase='signed-in Home';self.restart()
            phase='visible state'
            return Visible(servers,active,user,backend,options).validate()
        except Exception as exc:
            # No screen text, names or personal settings in exceptions or receipts.
            codes={'authenticated Home not verified','ambiguous signed-in profile','cannot read back an authenticated active profile',
                   'server list has no focused card','cannot identify exact server popup','duplicate server labels','server list cannot be completely enumerated'}
            detail=str(exc) if str(exc) in codes else type(exc).__name__
            raise ValueError('Stock UI did not verify '+phase+' ('+detail+')') from None

    def prepare_lab(self,name,original):
        if not name.startswith('Cleanyfin Lab ') or name in original.servers:raise ValueError('lab server name is not unique')
        if self.focus_profile()!=(original.active,original.user):raise ValueError('active profile changed before lab setup')
        self.servers_page();self.lab_name=name

    def restore(self,original):
        try:
            # Stop the lab PCM writer and restore its settings before returning to family
            # media. No member's playback should be captured to the temporary WAV.
            backend,options=self.read_settings()
            self.tv.transport.bind_original_options(original)
            if options!=original.options:
                self.restart();self.ui.enter_advanced();self.ui.save_mpv(original.options)
            if backend!=original.backend:
                self.restart();self.ui.enter_advanced();self.ui.dpad(backend);self.ui.dpad(original.backend,cap=5)
            self.focus_profile();self.servers_page();servers=self.server_entries()
            if set(servers)==set(original.servers)|{self.lab_name}:
                self.focus_profile();self.servers_page();self.server_entries(self.lab_name,delete=True);self.ui.wait_label('Select Server')
            elif set(servers)!=set(original.servers):raise ValueError('unexpected family server list change')
            for _ in range(12):self.ui.key('DPAD_LEFT')
            self.server_entries(original.active);self.ui.wait_label('Select User');self.ui.activate(original.user);self.ui.wait_label('Home')
        except Exception:raise ValueError('could not restore and verify family UI; no clear or uninstall attempted') from None
