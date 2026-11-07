"""Port Manager: mọi cổng đang lắng nghe, ai giữ cổng (process / dev service / container / project), dừng an toàn, cảnh báo trùng cổng."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from ..lib import dockercli as D  # noqa: E402
from ..lib import ports as PT  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import DataTable, InfoStrip, Page, SelectionBar, bg, button, content_box, copy_text, esc, hbox, icon_btn, label, toolbar, vbox  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

OK, WARN, DIM, ACC = '#26a269', '#e5a50a', '#9a9996', '#3584e4'
FILTERS = [('all', 'Tất cả'), ('dev', 'Dev & project'), ('docker', 'Docker'), ('public', 'Mở ra mạng')]
KIND_TXT = {'service': 'Dev service', 'container': 'Container', 'project': 'Project', 'process': 'Process'}


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


def match(row, flt):
    if flt == 'dev':
        return row['kind'] == 'service' or bool(row['project'])
    if flt == 'docker':
        return row['kind'] == 'container'
    if flt == 'public':
        return not row['local'] and row['protocol'] == 'TCP'
    return True


class PortsPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.rows, self.containers, self.flt, self.busy, self.dbusy = [], [], 'all', False, False
        self.docker_ok, self.proj_paths, self.pbusy = None, [], False
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh())]

        root = content_box()
        root.set_margin_bottom(0)
        stats = hbox(12, homogeneous=True)
        self.c_all, self.c_dev, self.c_dock, self.c_pub = (card('Cổng đang nghe', '—', ''), card('Dev & project', '—', ''), card('Docker', '—', ''), card('Mở ra mạng', '—', ''))
        for c in (self.c_all, self.c_dev, self.c_dock, self.c_pub):
            stats.append(c[0])
        root.append(stats)

        self.warn = InfoStrip('warning')
        root.append(self.warn)

        self.search = Gtk.SearchEntry(placeholder_text='Tìm theo cổng, process, project…', hexpand=True)
        self.tg = Adw.ToggleGroup()
        for name, txt in FILTERS:
            self.tg.add(Adw.Toggle(name=name, label=txt))
        self.tg.set_active_name('all')
        self.tg.connect('notify::active-name', lambda *_: self.set_filter(self.tg.get_active_name()))
        self.search.connect('search-changed', lambda e: self.table.set_query(e.get_text()))
        root.append(toolbar(self.search, self.tg))

        self.table = DataTable([
            {'title': 'Cổng', 'get': lambda d: str(d['port']), 'sort': lambda d: d['port'], 'width': 78, 'css': ['mono', 'tnum']},
            {'title': 'Giao thức', 'get': lambda d: d['protocol'], 'width': 80},
            {'title': 'Chủ sở hữu', 'get': lambda d: span({'service': OK, 'container': ACC}.get(d['kind'], DIM) if d['kind'] != 'process' else DIM, '● ') + esc(d['owner']),
             'markup': True, 'sort': lambda d: d['owner'].lower(), 'expand': True, 'width': 200, 'tip': lambda d: d['owner']},
            {'title': 'Project', 'get': lambda d: (d['project'].rsplit('/', 1)[-1] if d['project'] else '—'), 'sort': lambda d: d['project'], 'width': 140, 'tip': lambda d: d['project']},
            {'title': 'PID', 'get': lambda d: str(d['pid']) if d['pid'] else '—', 'sort': lambda d: d['pid'], 'width': 70, 'css': ['mono']},
            {'title': 'Địa chỉ', 'get': lambda d: span(WARN if not d['local'] else DIM, 'mạng' if not d['local'] else 'localhost'), 'markup': True, 'width': 104, 'sort': lambda d: d['local'],
             'tip': lambda d: ', '.join(d.get('addresses', [d['address']]))},
            {'title': 'Chạy', 'get': lambda d: PT.uptime(d['started']) or '—', 'width': 60, 'sort': lambda d: -d['started']},
            {'title': 'Lệnh', 'get': lambda d: d['cmd'] or '—', 'expand': True, 'width': 220, 'css': ['mono', 'dim-label'], 'tip': lambda d: d['cmd']},
        ], key=lambda d: (d['port'], d['protocol'], d['pid']), search_text=lambda d: f"{d['port']} {d['owner']} {d['process']} {d['project']} {d['cmd']}".lower(),
            on_select=self.on_select)
        self.table.sort_by(0, False)
        root.append(self.table)

        self.bar = SelectionBar()
        self.b_open = button('Mở trình duyệt', icon='web-browser-symbolic', css=['flat'], on=self.open_browser)
        self.b_copy = button('Sao chép', icon='edit-copy-symbolic', css=['flat'], on=self.copy)
        self.b_stop = button('Giải phóng cổng', icon='process-stop-symbolic', css=['destructive-action'], on=self.free)
        self.bar.add(self.b_open, self.b_copy, self.b_stop)
        outer = vbox(0)
        outer.append(root)
        outer.append(self.bar)
        self.set_child(outer)

        self.every(60, self.refresh_projects)
        self.every(3, self.refresh)
        self.every(10, self.refresh_docker)

    # ---- dữ liệu
    def refresh(self):
        if self.busy:
            return
        self.busy = True
        svcs = list(self.window.store['services'])
        mgr = {s['id']: self.window.services.status(s['id']) for s in svcs}
        paths = self.proj_paths + [s['cwd'] for s in svcs]
        conts = list(self.containers)

        def done(rows, err):
            self.busy = False
            if err or rows is None:
                self.toast(err or 'Không đọc được danh sách cổng')
                return
            self.rows = rows
            self.render(svcs)
        bg(lambda: PT.snapshot(svcs, mgr, conts, paths), done)

    def refresh_projects(self):
        if self.pbusy:
            return
        self.pbusy = True
        roots = self.window.store['project_roots'] or P.default_roots()

        def done(res, _err):
            self.pbusy = False
            if res is not None:
                self.proj_paths = res
        bg(lambda: [p if isinstance(p, str) else p['path'] for p in P.find_projects(roots)], done)

    def refresh_docker(self):
        if self.dbusy:
            return
        self.dbusy = True

        def work():
            st = D.status()
            return D.containers(False) if st['available'] else None

        def done(res, _err):
            self.dbusy = False
            self.docker_ok = res is not None
            if res is not None:
                self.containers = res

        bg(work, done)

    def set_filter(self, flt):
        self.flt = flt or 'all'
        self.render(None)

    def render(self, svcs):
        rows = self.rows
        shown = [r for r in rows if match(r, self.flt)]
        self.table.set_rows(shown)
        tcp = [r for r in rows if r['protocol'] == 'TCP']
        self.c_all[1].set_text(str(len({(r['port'], r['protocol']) for r in rows})))
        self.c_all[2].set_text(f'{len(tcp)} TCP · {len(rows) - len(tcp)} UDP')
        dev = [r for r in rows if match(r, 'dev')]
        self.c_dev[1].set_text(str(len(dev)))
        self.c_dev[2].set_text(f"{sum(1 for r in rows if r['kind'] == 'service')} dev service")
        dock = [r for r in rows if r['kind'] == 'container']
        self.c_dock[1].set_text(str(len(dock)) if self.docker_ok is not False else '—')
        self.c_dock[2].set_text('container đang chạy' if self.docker_ok is not False else 'Docker không khả dụng')
        pub = [r for r in tcp if not r['local']]
        self.c_pub[1].set_text(str(len(pub)))
        self.c_pub[2].set_text('nghe trên mọi địa chỉ' if pub else 'chỉ localhost')
        self.set_subtitle(f'{len(rows)} cổng')
        self.render_conflicts(svcs if svcs is not None else list(self.window.store['services']))

    def render_conflicts(self, svcs):
        running = {s['id'] for s in svcs if self.window.services.is_running(s['id'])}
        cs = PT.conflicts(svcs, self.rows, running)
        if not cs:
            self.warn.set_visible(False)
            return
        c = cs[0]
        more = f' (+{len(cs) - 1} cảnh báo khác)' if len(cs) > 1 else ''
        self.warn.show(c['detail'] + more, '\n'.join(x['detail'] for x in cs), 'Chọn cổng' if c['level'] == 'busy' else None, lambda: self.focus_port(c['port']))

    def focus_port(self, port):
        self.search.set_text(str(port))
        self.tg.set_active_name('all')

    # ---- thao tác
    def on_select(self, d):
        if not d:
            self.bar.select(None)
            return
        ok, why = PT.can_stop(d)
        self.bar.select(f"Cổng {d['port']} · {d['owner']}", (f"PID {d['pid']} · " if d['pid'] else '') + (d['cmd'][:80] if d['cmd'] else '') + ('' if ok else f'   🔒 {why}'))
        self.b_stop.set_sensitive(ok)
        self.b_stop.set_tooltip_text(why or None)
        self.b_stop.get_child().get_last_child().set_text('Dừng container' if d['container'] else 'Giải phóng cổng')
        self.b_open.set_sensitive(d['protocol'] == 'TCP')

    def open_browser(self):
        d = self.table.selected()
        if d:
            Gio.AppInfo.launch_default_for_uri(f"http://localhost:{d['port']}", None)

    def copy(self):
        d = self.table.selected()
        if d:
            copy_text(self, f"localhost:{d['port']}")
            self.toast('Đã sao chép')

    def free(self):
        d = self.table.selected()
        if not d:
            return
        ok, why = PT.can_stop(d)
        if not ok:
            self.toast(why)
            return
        if d['container']:
            c = d['container']
            confirm(self.window, f"Dừng container {c['name']}?", f"Container đang giữ cổng {d['port']}. Dữ liệu trong volume không bị xoá.", 'Dừng',
                    lambda: bg(lambda: D.action(c['id'], 'stop'), lambda _r, err: (self.toast(err or f"Đã dừng {c['name']}"), self.refresh_docker(), self.refresh())))
            return
        pid, name = d['pid'], d['process']

        def go(force=False):
            def work():
                return PT.stop_pid(pid, force=force)

            def done(res, err):
                if err:
                    self.toast(err)
                elif res == 'running':
                    confirm(self.window, f'{name} chưa thoát', 'Process không phản hồi tín hiệu dừng. Buộc dừng (SIGKILL) có thể mất dữ liệu chưa lưu.', 'Buộc dừng', lambda: go(True))
                else:
                    self.toast(f'Đã dừng {name} (PID {pid})')
                self.refresh()
            bg(work, done)
        confirm(self.window, f"Giải phóng cổng {d['port']}?", f"Sẽ gửi tín hiệu dừng tới {name} (PID {pid}).\n{d['cmd'][:200]}", 'Dừng', go)
