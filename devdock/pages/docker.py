"""DockerDesk: tổng quan + dọn dẹp, container (chi tiết/logs/thống kê/biến môi trường), image, volume, network, Docker Compose."""
import os
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..lib import dockercli as D  # noqa: E402
from ..lib import envfiles as E  # noqa: E402
from ..lib import logsrc as L  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, SelectionBar, Sparkline, bar, bg, button, content_box, copy_text, debounce, esc, hbox, human, icon_btn, label, linked, toolbar, vbox)  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

OK, WARN, BAD, DIM = '#26a269', '#e5a50a', '#e01b24', '#9a9996'
STATE_COLOR = {'running': OK, 'paused': WARN, 'restarting': WARN, 'exited': BAD, 'dead': BAD, 'created': DIM}
HEALTH_COLOR = {'healthy': OK, 'unhealthy': BAD, 'starting': WARN}


def state_markup(d):
    return f"<span foreground='{STATE_COLOR.get(d['state'], DIM)}'>● {esc(d['state'])}</span>  {esc(d['status'])}"


class RunDialog(Adw.Dialog):
    """Chạy một container mới từ image."""

    def __init__(self, page, image):
        super().__init__(title='Chạy container', content_width=520)
        self.page, self.image = page, image
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='Huỷ')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Chạy', css_classes=['suggested-action'])
        ok.connect('clicked', lambda *_: self.run())
        hdr.pack_start(cancel)
        hdr.pack_end(ok)
        tv.add_top_bar(hdr)
        grp = Adw.PreferencesGroup(margin_top=12, margin_start=12, margin_end=12, margin_bottom=8, description=GLib.markup_escape_text(image))
        self.name = Adw.EntryRow(title='Tên container (tuỳ chọn)')
        self.ports = Adw.EntryRow(title='Cổng — host:container, cách nhau bằng dấu phẩy (vd. 8080:80, 5432:5432)')
        self.env = Adw.EntryRow(title='Biến môi trường — KEY=VALUE, cách nhau bằng dấu phẩy')
        self.cmd = Adw.EntryRow(title='Lệnh (tuỳ chọn, ghi đè mặc định của image)')
        self.rm = Adw.SwitchRow(title='Tự xoá container khi dừng (--rm)')
        for w in (self.name, self.ports, self.env, self.cmd, self.rm):
            grp.add(w)
        self.err = label('', ['error'], wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        box = vbox(0)
        box.append(grp)
        box.append(self.err)
        tv.set_content(box)
        self.set_child(tv)

    def run(self):
        split = lambda s: [x.strip() for x in s.split(',') if x.strip()]
        kw = dict(name=self.name.get_text().strip(), ports=split(self.ports.get_text()), env=split(self.env.get_text()), cmd=self.cmd.get_text(), rm=self.rm.get_active())
        try:
            D.run_argv(self.image, **kw)  # kiểm tra hợp lệ trước khi chạy
        except D.DockerError as e:
            self.err.set_text(str(e))
            return
        self.close()
        self.page.toast(f'Đang chạy {self.image}…')
        bg(lambda: D.run_container(self.image, **kw), lambda cid, err: (self.page.toast(err or f'Đã chạy container {cid}'), self.page.refresh_all()))


class ContainerDetail(Gtk.Box):
    """Khung chi tiết một container: thông tin, logs trực tiếp, thống kê trực tiếp, biến môi trường."""

    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.page, self.cur, self.info, self.stream, self.reveal = page, None, None, None, False
        self.cpu_h, self.mem_h = deque(maxlen=60), deque(maxlen=60)
        head = hbox(8)
        names = vbox(0, hexpand=True)
        self.title = label('', ['page-title'], ellipsize=3)
        self.sub = label('', ['dim-label', 'caption'], ellipsize=3)
        names.append(self.title)
        names.append(self.sub)
        head.append(names)
        head.append(icon_btn('window-close-symbolic', 'Đóng', lambda: page.ct.sel.unselect_all(), ['flat', 'circular']))
        self.append(head)
        self.tg = Adw.ToggleGroup(halign=Gtk.Align.START)
        for n, t in (('info', 'Chi tiết'), ('logs', 'Logs'), ('stats', 'Thống kê'), ('env', 'Môi trường')):
            self.tg.add(Adw.Toggle(name=n, label=t))
        self.tg.set_active_name('info')
        self.tg.connect('notify::active-name', lambda *_: self.on_tab())
        self.append(self.tg)
        self.stack = Gtk.Stack(vexpand=True)
        # --- chi tiết
        self.info_box = vbox(12)
        self.stack.add_named(Gtk.ScrolledWindow(child=self.info_box, hscrollbar_policy=Gtk.PolicyType.NEVER), 'info')
        # --- logs
        lg = vbox(6)
        self.tv = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=4, bottom_margin=4)
        self.tv.add_css_class('mono')
        self.buf = self.tv.get_buffer()
        for lv, c in (('error', '#e01b24'), ('warn', '#c88800'), ('debug', '#8d8d8a')):
            self.buf.create_tag(lv, foreground=c)
        self.log_sw = Gtk.ScrolledWindow(child=self.tv, vexpand=True)
        self.log_sw.add_css_class('codebox')
        lg.append(self.log_sw)
        lg.append(button('Mở trong Log Viewer', icon='utilities-terminal-symbolic', css=['flat'], on=self.open_viewer))
        self.stack.add_named(lg, 'logs')
        # --- thống kê
        st = vbox(10)
        self.cpu_card = card('CPU', '—', '', Sparkline((.21, .52, .89), 56, 100))
        self.mem_card = card('RAM', '—', '', Sparkline((.2, .82, .6), 56, 100))
        self.io_label = label('', ['dim-label'], wrap=True, xalign=0)
        st.append(self.cpu_card[0])
        st.append(self.mem_card[0])
        st.append(self.io_label)
        self.stack.add_named(Gtk.ScrolledWindow(child=st, hscrollbar_policy=Gtk.PolicyType.NEVER), 'stats')
        # --- môi trường
        ev = vbox(6)
        self.rev = Gtk.ToggleButton(label='Hiện giá trị bí mật', halign=Gtk.Align.START)
        self.rev.connect('toggled', lambda b: (setattr(self, 'reveal', b.get_active()), self.render_env()))
        ev.append(self.rev)
        self.env_table = DataTable([{'title': 'Biến', 'get': lambda d: d['k'], 'sort': lambda d: d['k'].lower(), 'width': 160, 'css': ['mono']},
                                    {'title': 'Giá trị', 'get': lambda d: E.mask(d['v']) if (d['secret'] and not self.reveal) else d['v'], 'expand': True, 'css': ['mono']}], key=lambda d: d['k'])
        ev.append(self.env_table)
        self.stack.add_named(ev, 'env')
        self.append(self.stack)

    # ---- chọn container
    def show(self, c):
        if c and self.cur and c['id'] == self.cur['id']:
            # cùng container (bảng vừa làm mới): giữ nguyên tab/log; chỉ nạp lại thông tin nếu trạng thái đổi
            changed = c['state'] != self.cur['state']
            self.cur = c
            if changed:
                cid = c['id']
                bg(lambda: D.inspect(cid), lambda info, err: self.cur and self.cur['id'] == cid and (setattr(self, 'info', info), self.render_info(info, err), self.render_env()))
            return
        self.stop_stream()
        self.cur, self.info = c, None
        self.cpu_h.clear()
        self.mem_h.clear()
        if not c:
            return
        self.title.set_text(c['name'])
        self.sub.set_text(c['image'])
        self.tg.set_active_name('info')
        self.stack.set_visible_child_name('info')
        while w := self.info_box.get_first_child():
            self.info_box.remove(w)
        self.info_box.append(label('Đang đọc thông tin…', ['dim-label']))
        cid = c['id']

        def done(info, err):
            if self.cur and self.cur['id'] == cid:
                self.info = info
                self.render_info(info, err)
                self.render_env()
        bg(lambda: D.inspect(cid), done)

    def on_tab(self):
        tab = self.tg.get_active_name()
        self.stack.set_visible_child_name(tab)
        self.stop_stream()
        if tab == 'logs' and self.cur:
            self.buf.set_text('', -1)
            self.stream = L.ProcStream(L.docker_argv(self.cur['id'], 200))

    def stop_stream(self):
        if self.stream:
            self.stream.stop()
            self.stream = None

    def open_viewer(self):
        if self.cur:
            self.page.window.open_logs(('docker', self.cur['id'], self.cur['name']))

    # ---- hiển thị
    def render_info(self, i, err):
        while w := self.info_box.get_first_child():
            self.info_box.remove(w)
        if err or not i:
            self.info_box.append(label(err or 'Không đọc được thông tin', ['error'], wrap=True))
            return

        def group(title, rows):
            if not rows:
                return
            g = Adw.PreferencesGroup(title=esc(title))
            for r in rows:
                g.add(r)
            self.info_box.append(g)

        def row(title, value, mono=False, suffix=None):
            r = Adw.ActionRow(title=esc(title), subtitle=esc(value) or '—', subtitle_selectable=True, subtitle_lines=0)
            r.add_css_class('property')
            if mono:
                r.add_css_class('mono')
            if suffix:
                r.add_suffix(suffix)
            return r
        health = f" · <span foreground='{HEALTH_COLOR.get(i['health'], DIM)}'>{i['health']}</span>" if i['health'] else ''
        state = row('Trạng thái', i['state'] + (f" · mã thoát {i['exit_code']}" if not i['running'] and i['exit_code'] is not None else '') + (f" · {i['health']}" if i['health'] else ''))
        group('Tổng quan', [state, row('Image', i['image']), row('Tạo lúc', i['created'][:19].replace('T', ' ')), row('Khởi động', i['started'][:19].replace('T', ' ') if i['running'] else '—'),
                            row('Restart policy', f"{i['restart']}" + (f" · đã restart {i['restarts']} lần" if i['restarts'] else '')), row('Lệnh', (i['entrypoint'] + ' ' + i['cmd']).strip(), True),
                            row('Thư mục / user', f"{i['workdir'] or '/'} · {i['user'] or 'root'}")] + ([row('Project compose', f"{i['project']} / {i['service']}")] if i['project'] else []))
        ports = []
        for p in i['ports']:
            if p['host_port']:
                url_host = '127.0.0.1' if p['host_ip'] in ('', '0.0.0.0', '::') else p['host_ip']
                b = icon_btn('web-browser-symbolic', f"Mở http://{url_host}:{p['host_port']}", lambda h=url_host, hp=p['host_port']: Gio.AppInfo.launch_default_for_uri(f'http://{h}:{hp}', None), ['flat'])
                ports.append(row(f"{p['host_ip'] or '0.0.0.0'}:{p['host_port']}", f"→ {p['container']}", True, b))
            else:
                ports.append(row(p['container'], 'không publish ra host', True))
        group('Cổng', ports)
        group('Volume / mount', [row(f"{m['type']} · {'rw' if m['rw'] else 'ro'}", f"{m['src']}  →  {m['dst']}", True) for m in i['mounts']])
        group('Mạng', [row(n['name'], f"IP {n['ip'] or '—'} · gateway {n['gateway'] or '—'}") for n in i['networks']])

    def render_env(self):
        if self.info:
            self.env_table.set_rows([{'k': k, 'v': v, 'secret': E.is_secret(k, v)} for k, v in self.info['env']])

    # ---- gọi định kỳ bởi trang
    def flush_logs(self):
        if not self.stream or self.tg.get_active_name() != 'logs':
            return
        new = self.stream.drain(800)
        if not new:
            return
        for line in new:
            lv = L.level_of(line)
            end = self.buf.get_end_iter()
            if lv in ('error', 'warn', 'debug'):
                self.buf.insert_with_tags_by_name(end, line + '\n', lv)
            else:
                self.buf.insert(end, line + '\n')
        if self.buf.get_line_count() > 4000:
            self.buf.delete(self.buf.get_start_iter(), self.buf.get_iter_at_line(1000))
        adj = self.log_sw.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)

    def tick_stats(self):
        if not self.cur or self.tg.get_active_name() != 'stats' or self.cur['state'] != 'running':
            return
        cid = self.cur['id']

        def done(s, err):
            if not self.cur or self.cur['id'] != cid:
                return
            if err or not s:
                self.cpu_card[1].set_text('—')
                self.io_label.set_text(err or '')
                return
            self.cpu_h.append(min(s['cpu'], 100))
            self.mem_h.append(s['mem_pct'])
            self.cpu_card[1].set_text(f"{s['cpu']:.1f}%")
            self.mem_card[1].set_text(f"{s['mem_pct']:.1f}%")
            self.mem_card[2].set_text(s['mem'])
            self.cpu_card[0].get_last_child().set_data(self.cpu_h)
            self.mem_card[0].get_last_child().set_data(self.mem_h)
            self.io_label.set_text(f"Mạng ↓/↑: {s['net']}\nĐĩa đọc/ghi: {s['block']}\nPID: {s['pids']}")
        bg(lambda: D.stats_one(cid), done)


class DockerPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.ok, self.show_all, self.project, self.discovered = False, True, None, []
        self.busy = set()
        self.stack = Adw.ViewStack(vexpand=True)
        self.hdr_title = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh_all())]
        self.down = Adw.StatusPage(icon_name='package-x-generic-symbolic', title='Không kết nối được Docker', vexpand=True, visible=False)
        for name, title, icon, build in (('overview', 'Tổng quan', 'view-grid-symbolic', self._overview), ('containers', 'Containers', 'package-x-generic-symbolic', self._containers),
                                         ('images', 'Images', 'image-x-generic-symbolic', self._images), ('volumes', 'Volumes', 'drive-harddisk-symbolic', self._volumes),
                                         ('networks', 'Networks', 'network-wired-symbolic', self._networks), ('compose', 'Compose', 'view-list-symbolic', self._compose)):
            self.stack.add_titled_with_icon(build(), name, title, icon)
        root = vbox(0)
        root.append(self.down)
        root.append(self.stack)
        self.set_child(root)
        self.every(5, self.tick_status).every(3, self.tick_containers).every(4, self.tick_overview).every(20, self.tick_df).every(10, self.tick_images)
        self.every(15, self.tick_volumes).every(10, self.tick_networks).every(0.3, lambda: self.detail.flush_logs()).every(2, lambda: self.detail.tick_stats())

    def show_tool(self, name):
        self.stack.set_visible_child_name(name)

    def tab(self):
        return self.stack.get_visible_child_name()

    def once(self, key, fn, done):
        """Chạy fn ở nền, bỏ qua nếu lần trước còn đang chạy."""
        if key in self.busy:
            return
        self.busy.add(key)
        bg(fn, lambda r, e: (self.busy.discard(key), done(r, e)))

    def err(self, e):
        if e:
            self.toast(e)

    def refresh_all(self):
        for f in (self.tick_containers, self.tick_overview, self.tick_df, self.tick_images, self.tick_volumes, self.tick_networks):
            f(True)

    def tick_status(self):
        def done(st, _e):
            was = self.ok
            self.ok = bool(st and st['available'])
            if self.ok and not was:
                self.refresh_all()  # vừa kết nối: nạp dữ liệu ngay thay vì đợi chu kỳ sau
            self.stack.set_visible(self.ok)
            self.down.set_visible(not self.ok and bool(st))
            if st and not st['available']:
                self.down.set_description((st['error'] or '') + '\nHãy chắc chắn Docker đang chạy và user thuộc nhóm docker (sudo usermod -aG docker $USER).')
            if st and st['info']:
                i = st['info']
                self.docker_info.set_text(f"Docker {i['version']} · {i['os']} · {i['cpus']} CPU · {human(i['mem_total'])} RAM")
                self.c_cont[1].set_text(f"{i['running']} / {i['containers']}")
                self.c_cont[2].set_text(f"{i['images']} image")
        self.once('status', D.status, done)

    # ================= Tổng quan =================
    def _overview(self):
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = content_box(12)
        box.set_margin_bottom(12)
        self.docker_info = label('', ['dim-label'], xalign=0)
        box.append(self.docker_info)
        row = hbox(12, homogeneous=True)
        self.c_cont = card('Container chạy', '—', '')
        self.c_cpu = card('CPU (container)', '—', '')
        self.c_mem = card('RAM (container)', '—', '')
        self.c_disk = card('Dung lượng Docker', '—', '')
        for c in (self.c_cont, self.c_cpu, self.c_mem, self.c_disk):
            row.append(c[0])
        box.append(row)
        self.df_group = Adw.PreferencesGroup(title='Dung lượng và dọn dẹp', description='Dọn xoá vĩnh viễn; mục "có thể dọn" là phần không còn được container nào dùng.')
        box.append(self.df_group)
        self.df_rows = {}
        for key, title, note in (('Images', 'Images', 'Image không có container dùng'), ('Containers', 'Containers', 'Container đã dừng'), ('Local Volumes', 'Volumes', 'Volume không container nào gắn — CÓ THỂ MẤT DỮ LIỆU'),
                                 ('Build Cache', 'Build cache', 'Cache của docker build')):
            r = Adw.ActionRow(title=title, subtitle=note)
            b = button('Dọn', css=['destructive-action'], on=lambda k=key: self.prune(k))
            b.set_valign(Gtk.Align.CENTER)
            r.add_suffix(b)
            self.df_group.add(r)
            self.df_rows[key] = (r, b, note)
        box.append(label('Container đang chạy', ['heading'], xalign=0, margin_top=6))
        cols = [
            {'title': 'Container', 'get': lambda d: d['name'], 'sort': lambda d: d['name'], 'expand': True},
            {'title': 'CPU', 'get': lambda d: f"{d['cpu']:.1f}%", 'sort': lambda d: d['cpu'], 'width': 90, 'align': 1.0, 'css': ['tnum']},
            {'title': 'RAM', 'get': lambda d: d['mem'], 'sort': lambda d: d['mem_pct'], 'width': 200, 'css': ['tnum']},
            {'title': 'RAM %', 'get': lambda d: f"{d['mem_pct']:.1f}%", 'sort': lambda d: d['mem_pct'], 'width': 90, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Net ↓/↑', 'get': lambda d: d['net'], 'width': 170},
            {'title': 'Block I/O', 'get': lambda d: d['block'], 'width': 150},
        ]
        self.stats_table = DataTable(cols, key=lambda d: d['name'])
        self.stats_table.set_size_request(-1, 180)
        self.stats_table.sort_by(1, True)
        box.append(self.stats_table)
        self.no_run = label('Không có container nào đang chạy', ['dim-label'], xalign=0, visible=False)
        box.append(self.no_run)
        sc.set_child(box)
        return sc

    def tick_overview(self, force=False):
        if not self.ok or (self.tab() != 'overview' and not force):
            return

        def done(rows, err):
            if rows is None:
                return
            self.stats_table.set_rows(rows)
            self.no_run.set_visible(not rows)
            self.stats_table.set_visible(bool(rows))
            self.c_cpu[1].set_text(f"{sum(r['cpu'] for r in rows):.1f}%")
            self.c_cpu[2].set_text(f"{len(rows)} container")
            mem = sum(D.parse_size(str(r['mem']).split('/')[0]) for r in rows)
            self.c_mem[1].set_text(human(mem))
            self.c_mem[2].set_text('đang dùng')
        self.once('stats', D.stats, done)

    def tick_df(self, force=False):
        if not self.ok or (self.tab() != 'overview' and not force):
            return

        def done(rows, err):
            if not rows:
                return
            by = {r['type']: r for r in rows}
            total = sum(r['size'] for r in rows)
            recl = sum(r['reclaimable'] for r in rows)
            self.c_disk[1].set_text(human(total))
            self.c_disk[2].set_text(f'có thể dọn {human(recl)}')
            for key, (r, b, note) in self.df_rows.items():
                d = by.get(key)
                if d:
                    r.set_subtitle(esc(f"{d['total']} mục ({d['active']} đang dùng) · {human(d['size'])} · có thể dọn {human(d['reclaimable'])}  —  {note}"))
                    b.set_sensitive(d['reclaimable'] > 0 or key == 'Containers' and d['total'] > d['active'])
        self.once('df', D.system_df, done)

    def prune(self, key):
        fn, what = {'Images': (lambda: D.image_prune(True), 'Xoá mọi image không có container nào dùng?'), 'Containers': (D.container_prune, 'Xoá mọi container đã dừng?'),
                    'Local Volumes': (D.volume_prune, 'Xoá mọi volume không gắn với container nào? Dữ liệu trong volume sẽ MẤT.'), 'Build Cache': (D.builder_prune, 'Xoá toàn bộ build cache?')}[key]
        confirm(self.window, 'Dọn dẹp Docker', what, 'Dọn', lambda: bg(fn, lambda r, e: (self.toast(e or r), self.refresh_all())))

    # ================= Containers =================
    def _containers(self):
        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, min_sidebar_width=380, max_sidebar_width=560, sidebar_width_fraction=0.4, show_sidebar=False, enable_show_gesture=False, enable_hide_gesture=False)
        root = content_box()
        root.set_margin_bottom(12)
        s = Gtk.SearchEntry(placeholder_text='Tìm container, image, project…', hexpand=True)
        s.connect('search-changed', lambda e: self.ct.set_query(e.get_text()))
        self.proj_dd = Gtk.DropDown.new_from_strings(['Mọi project'])
        self.proj_dd.connect('notify::selected', lambda d, _p: self.on_project(d))
        self.all_cb = Gtk.CheckButton(label='Cả đã dừng', active=True)
        self.all_cb.connect('toggled', lambda b: (setattr(self, 'show_all', b.get_active()), self.tick_containers(True)))
        root.append(toolbar(s, self.proj_dd, self.all_cb))
        cols = [
            {'title': 'Tên', 'get': lambda d: f"<b>{esc(d['name'])}</b>" + (f"  <span foreground='{DIM}'>{esc(d['project'])}</span>" if d['project'] else ''), 'markup': True, 'sort': lambda d: d['name'], 'width': 260, 'tip': lambda d: d['id'][:12]},
            {'title': 'Image', 'get': lambda d: d['image'], 'sort': lambda d: d['image'], 'width': 220},
            {'title': 'Trạng thái', 'get': state_markup, 'markup': True, 'sort': lambda d: (d['state'] != 'running', d['name']), 'width': 240},
            {'title': 'Cổng', 'get': lambda d: ', '.join(d['ports'][:2]) + (f" +{len(d['ports']) - 2}" if len(d['ports']) > 2 else ''), 'expand': True, 'tip': lambda d: '\n'.join(d['ports'])},
        ]
        self.ct = DataTable(cols, key=lambda d: d['id'], search_text=lambda d: f"{d['name']} {d['image']} {d['project'] or ''}", on_select=self.on_select)
        self.ct.sort_by(2, False)
        root.append(self.ct)
        self.cbar = SelectionBar()
        self.cbtn = {}
        mk = lambda k, text, ic, fn, css=None: self.cbtn.setdefault(k, button(text, icon=ic, css=css, on=fn))
        self.proj_menu = Gtk.MenuButton(label='Project', icon_name='view-more-symbolic', valign=Gtk.Align.CENTER, visible=False)
        pop = Gtk.Popover()
        pb = vbox(2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        for act, text in (('start', 'Khởi động cả project'), ('stop', 'Dừng cả project'), ('restart', 'Restart cả project'), ('down', 'Down (dừng + xoá container)')):
            b = button(text, css=['flat'], on=lambda a=act: (pop.popdown(), self.project_action(a)))
            pb.append(b)
        pop.set_child(pb)
        self.proj_menu.set_popover(pop)
        self.cbar.add(linked(mk('start', 'Khởi động', 'media-playback-start-symbolic', lambda: self.act('start')), mk('stop', 'Dừng', 'media-playback-stop-symbolic', lambda: self.act('stop')),
                             mk('restart', 'Restart', 'view-refresh-symbolic', lambda: self.act('restart'))),
                      linked(mk('shell', 'Shell', 'utilities-terminal-symbolic', self.open_shell)), self.proj_menu,
                      mk('remove', 'Xoá', 'user-trash-symbolic', self.remove, ['destructive-action']))
        outer = vbox(0)
        outer.append(root)
        outer.append(self.cbar)
        self.split.set_content(outer)
        self.detail = ContainerDetail(self)
        self.split.set_sidebar(self.detail)
        return self.split

    def on_project(self, dd):
        i = dd.get_selected()
        m = dd.get_model()
        self.project = None if i == 0 or i >= m.get_n_items() else m.get_string(i)
        self.tick_containers(True)

    def on_select(self, d):
        self.cbar.select(d['name'] if d else None, f"{d['image']} · {d['status']}" if d else '')
        running = bool(d and d['state'] == 'running')
        for k, b in self.cbtn.items():
            b.set_sensitive(bool(d) and (k not in ('stop', 'shell', 'restart') or running) and (k != 'start' or not running))
        self.proj_menu.set_visible(bool(d and d['project']))
        if d and d['project']:
            self.proj_menu.set_label(f"Project {d['project']}")
        self.split.set_show_sidebar(d is not None)
        self.detail.show(d)

    def tick_containers(self, force=False):
        if not self.ok or (self.tab() not in ('containers', 'compose') and not force):
            return

        def done(rows, _e):
            if rows is None:
                return
            projects = sorted({r['project'] for r in rows if r['project']})
            m = self.proj_dd.get_model()
            if [m.get_string(i) for i in range(1, m.get_n_items())] != projects:
                keep = self.project
                self.proj_dd.set_model(Gtk.StringList.new(['Mọi project'] + projects))
                if keep in projects:
                    self.proj_dd.set_selected(projects.index(keep) + 1)
            self.discovered = sorted({f for r in rows for f in r['compose_files']})
            self.ct.set_rows([r for r in rows if self.project is None or r['project'] == self.project])
            if self.tab() == 'compose':
                self.load_compose_list()
        self.once('containers', lambda: D.containers(self.show_all), done)

    def act(self, action):
        d = self.ct.selected()
        if d:
            bg(lambda: D.action(d['id'], action), lambda _r, err: (self.err(err), self.tick_containers(True)))

    def project_action(self, action):
        d = self.ct.selected()
        if not d or not d['project']:
            return
        proj = d['project']
        run = lambda: bg(lambda: D.project_action(proj, action), lambda _r, err: (self.toast(err or f'Đã {action} project {proj}'), self.tick_containers(True)))
        if action in ('down', 'stop'):
            confirm(self.window, f'{action.capitalize()} project {proj}?', 'Áp dụng cho mọi container của project.' + (' Container sẽ bị xoá.' if action == 'down' else ''), 'Xác nhận', run)
        else:
            run()

    def open_shell(self):
        d = self.ct.selected()
        if d:
            try:
                P.open_terminal(cmd=D.exec_argv(d['id']))
            except RuntimeError as e:
                self.toast(str(e))

    def remove(self):
        d = self.ct.selected()
        if d:
            confirm(self.window, f"Xoá container {d['name']}?", 'Container sẽ bị buộc dừng và xoá. Dữ liệu trong volume ẩn danh có thể mất.', 'Xoá',
                    lambda: bg(lambda: D.action(d['id'], 'remove'), lambda _r, err: (self.err(err), self.tick_containers(True))))

    # ================= Images =================
    def _images(self):
        root = content_box()
        root.set_margin_bottom(12)
        self.pull_entry = Gtk.Entry(placeholder_text='Pull image, vd. nginx:alpine', hexpand=True)
        self.pull_entry.connect('activate', lambda *_: self.pull())
        self.pull_btn = button('Pull', icon='folder-download-symbolic', css=['suggested-action'], on=self.pull)
        root.append(toolbar(self.pull_entry, self.pull_btn, button('Dọn image không dùng', icon='edit-clear-all-symbolic', on=lambda: self.prune('Images'))))
        cols = [
            {'title': 'Image', 'get': lambda d: d['tag'], 'sort': lambda d: d['tag'], 'expand': True},
            {'title': 'ID', 'get': lambda d: d['id'].replace('sha256:', '')[:12], 'width': 130, 'css': ['mono']},
            {'title': 'Kích thước', 'get': lambda d: d['size'], 'sort': lambda d: D.parse_size(d['size']), 'width': 120, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Tạo', 'get': lambda d: d['created'], 'width': 160},
            {'title': 'Sử dụng', 'get': lambda d: f"<span foreground='{OK if d['used'] else DIM}'>{'đang dùng' if d['used'] else 'không dùng'}</span>", 'markup': True, 'sort': lambda d: d['used'], 'width': 120},
        ]
        self.it = DataTable(cols, key=lambda d: d['id'], search_text=lambda d: d['tag'], on_select=lambda d: self.ibar.select(d['tag'] if d else None, d['size'] if d else ''))
        self.it.sort_by(2, True)
        root.append(self.it)
        self.ibar = SelectionBar()
        self.ibar.add(button('Chạy container…', icon='media-playback-start-symbolic', css=['suggested-action'], on=self.run_image),
                      button('Xoá image', icon='user-trash-symbolic', css=['destructive-action'], on=self.remove_image))
        outer = vbox(0)
        outer.append(root)
        outer.append(self.ibar)
        return outer

    def tick_images(self, force=False):
        if self.ok and (self.tab() == 'images' or force):
            self.once('images', D.images, lambda rows, _e: rows is not None and self.it.set_rows(rows))

    def run_image(self):
        d = self.it.selected()
        if d and d['tag'] != '<none>':
            RunDialog(self, d['tag']).present(self.get_root())
        elif d:
            self.toast('Image chưa có tag — hãy chạy theo ID: ' + d['id'][7:19])

    def pull(self):
        name = self.pull_entry.get_text().strip()
        if not name:
            return
        self.pull_btn.set_sensitive(False)
        self.toast(f'Đang pull {name}…')

        def done(res, err):
            self.pull_btn.set_sensitive(True)
            self.toast(err or f'Đã pull: {res}')
            if not err:
                self.pull_entry.set_text('')
            self.tick_images(True)
        bg(lambda: D.pull(name), done)

    def remove_image(self):
        d = self.it.selected()
        if d:
            confirm(self.window, 'Xoá image?', d['tag'], 'Xoá', lambda: bg(lambda: D.remove_image(d['id']), lambda _r, err: (self.err(err), self.tick_images(True), self.tick_df(True))))

    # ================= Volumes =================
    def _volumes(self):
        root = content_box()
        root.set_margin_bottom(12)
        self.vol_info = label('', ['dim-label'], xalign=0, hexpand=True)
        root.append(toolbar(self.vol_info, button('Dọn volume không dùng', icon='edit-clear-all-symbolic', on=lambda: self.prune('Local Volumes'))))
        cols = [
            {'title': 'Volume', 'get': lambda d: d['name'], 'sort': lambda d: d['name'], 'expand': True, 'css': ['mono']},
            {'title': 'Driver', 'get': lambda d: d['driver'], 'width': 100},
            {'title': 'Dung lượng', 'get': lambda d: human(d['size']), 'sort': lambda d: d['size'], 'width': 120, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Container dùng', 'get': lambda d: (f"{d['links']} container" if d['links'] else '— không dùng'), 'sort': lambda d: d['links'], 'width': 160},
        ]
        self.vt = DataTable(cols, key=lambda d: d['name'], search_text=lambda d: d['name'], on_select=lambda d: self.vbar.select(d['name'] if d else None, ('đang được dùng' if d and d['links'] else 'không container nào dùng') if d else ''))
        self.vt.sort_by(2, True)
        root.append(self.vt)
        self.vbar = SelectionBar()
        self.vbar.add(button('Xoá volume', icon='user-trash-symbolic', css=['destructive-action'], on=self.remove_volume))
        outer = vbox(0)
        outer.append(root)
        outer.append(self.vbar)
        return outer

    def tick_volumes(self, force=False):
        if not (self.ok and (self.tab() == 'volumes' or force)):
            return

        def done(rows, _e):
            if rows is not None:
                self.vt.set_rows(rows)
                self.vol_info.set_text(f'{len(rows)} volume · tổng {human(sum(r["size"] for r in rows))}')
        self.once('volumes', D.volumes, done)

    def remove_volume(self):
        d = self.vt.selected()
        if d:
            confirm(self.window, f"Xoá volume {d['name']}?", 'Dữ liệu trong volume sẽ MẤT vĩnh viễn.' + ('\nVolume đang được container dùng nên Docker sẽ từ chối.' if d['links'] else ''), 'Xoá',
                    lambda: bg(lambda: D.volume_remove(d['name']), lambda _r, err: (self.err(err), self.tick_volumes(True), self.tick_df(True))))

    # ================= Networks =================
    def _networks(self):
        root = content_box()
        root.set_margin_bottom(12)
        root.append(toolbar(Gtk.Box(hexpand=True), button('Dọn network không dùng', icon='edit-clear-all-symbolic', on=self.prune_networks)))
        cols = [
            {'title': 'Network', 'get': lambda d: d['name'] + ('  (mặc định)' if d['builtin'] else ''), 'sort': lambda d: d['name'], 'expand': True},
            {'title': 'Driver', 'get': lambda d: d['driver'], 'width': 100},
            {'title': 'Scope', 'get': lambda d: d['scope'], 'width': 90},
            {'title': 'Subnet', 'get': lambda d: d['subnet'] or '—', 'width': 200, 'css': ['mono']},
            {'title': 'Container', 'get': lambda d: str(d['containers']), 'sort': lambda d: d['containers'], 'width': 100, 'align': 1.0},
        ]
        self.nt = DataTable(cols, key=lambda d: d['id'], search_text=lambda d: d['name'], on_select=self.on_net_select)
        root.append(self.nt)
        self.nbar = SelectionBar()
        self.net_rm = button('Xoá network', icon='user-trash-symbolic', css=['destructive-action'], on=self.remove_network)
        self.nbar.add(self.net_rm)
        outer = vbox(0)
        outer.append(root)
        outer.append(self.nbar)
        return outer

    def on_net_select(self, d):
        self.nbar.select(d['name'] if d else None, ('network mặc định, không xoá được' if d['builtin'] else f"{d['containers']} container") if d else '')
        self.net_rm.set_sensitive(bool(d) and not d['builtin'])

    def tick_networks(self, force=False):
        if self.ok and (self.tab() == 'networks' or force):
            self.once('networks', D.networks, lambda rows, _e: rows is not None and self.nt.set_rows(rows))

    def remove_network(self):
        d = self.nt.selected()
        if d and not d['builtin']:
            confirm(self.window, f"Xoá network {d['name']}?", f"{d['containers']} container đang dùng network này." if d['containers'] else '', 'Xoá',
                    lambda: bg(lambda: D.network_remove(d['name']), lambda _r, err: (self.err(err), self.tick_networks(True))))

    def prune_networks(self):
        confirm(self.window, 'Dọn network không dùng?', 'Xoá mọi network không có container nào gắn (trừ mặc định).', 'Dọn', lambda: bg(D.network_prune, lambda r, e: (self.toast(e or r), self.tick_networks(True))))

    # ================= Compose =================
    def _compose(self):
        box = hbox(0)
        left = vbox(0, width_request=300)
        tb = toolbar(linked(button('Thêm…', icon='list-add-symbolic', on=self.add_compose), button('Tìm trong project', icon='system-search-symbolic', on=self.find_compose)), spacing=6)
        tb.set_margin_top(12)
        tb.set_margin_start(12)
        tb.set_margin_end(12)
        tb.set_margin_bottom(6)
        left.append(tb)
        self.clist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.clist.connect('row-selected', lambda *_: self.on_compose_select())
        left.append(Gtk.ScrolledWindow(child=self.clist, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        right = content_box(10)
        right.set_hexpand(True)
        right.set_margin_bottom(12)
        self.cp_btns = []

        def mk(act, t, ic, css=None):
            b = button(t, icon=ic, css=css, on=lambda a=act: self.run_compose(a))
            b.set_sensitive(False)
            self.cp_btns.append(b)
            return b
        self.cp_title = label('', ['dim-label', 'caption'], ellipsize=1, xalign=0)
        right.append(toolbar(linked(mk('up', 'Up -d', 'media-playback-start-symbolic', ['suggested-action']), mk('stop', 'Stop', 'media-playback-stop-symbolic'),
                                    mk('restart', 'Restart', 'view-refresh-symbolic'), mk('down', 'Down', 'edit-delete-symbolic')),
                             linked(mk('pull', 'Pull', 'folder-download-symbolic'), mk('ps', 'ps', None), mk('logs', 'logs', None))))
        right.append(self.cp_title)
        self.cp_out = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.cp_out.add_css_class('mono')
        sw = Gtk.ScrolledWindow(child=self.cp_out, vexpand=True)
        sw.add_css_class('codebox')
        right.append(sw)
        box.append(right)
        self.load_compose_list()
        return box

    def files(self):
        return self.window.store['compose_files']

    def all_compose(self):
        """File đã lưu + file được phát hiện từ nhãn của container đang có (chạy hoặc đã dừng)."""
        seen, out = set(), []
        for f in self.files() + self.discovered:
            if f not in seen and os.path.isfile(f):
                seen.add(f)
                out.append((f, f in self.discovered))
        return out

    def load_compose_list(self):
        keep = self.sel_compose()
        self.clist.remove_all()
        for f, running in self.all_compose():
            row = Gtk.ListBoxRow()
            row.path = f
            col = vbox(0, margin_top=6, margin_bottom=6, margin_start=10, margin_end=8, hexpand=True)
            col.append(label(os.path.basename(os.path.dirname(f)) or f, ['heading'], ellipsize=3))
            col.append(label(f, ['dim-label', 'caption'], ellipsize=1))
            line = hbox(4)
            line.append(col)
            if running:
                line.append(label('có container', ['badge', 'ok'], valign=Gtk.Align.CENTER))
            if f in self.files():
                rm = Gtk.Button(icon_name='window-close-symbolic', css_classes=['flat', 'circular'], valign=Gtk.Align.CENTER, tooltip_text='Bỏ khỏi danh sách')
                rm.connect('clicked', lambda _b, p=f: self.remove_compose(p))
                line.append(rm)
            row.set_child(line)
            self.clist.append(row)
            if f == keep:
                self.clist.select_row(row)

    def add_compose(self):
        dlg = Gtk.FileDialog(title='Chọn file docker-compose')

        def done(d, res):
            try:
                self.add_file(d.open_finish(res).get_path())
            except GLib.Error:
                pass
        dlg.open(self.get_root(), None, done)

    def add_file(self, path):
        if path not in self.files():
            self.files().append(path)
            self.window.store.save()
        self.load_compose_list()

    def find_compose(self):
        roots = self.window.store['project_roots'] or P.default_roots()
        self.toast('Đang tìm file compose…')

        def done(found, err):
            n = 0
            for f in found or []:
                if f not in self.files():
                    self.files().append(f)
                    n += 1
            self.window.store.save()
            self.load_compose_list()
            self.toast(err or f'Đã thêm {n} file compose')
        bg(lambda: D.find_compose(roots), done)

    def remove_compose(self, path):
        if path in self.files():
            self.files().remove(path)
            self.window.store.save()
            self.load_compose_list()

    def sel_compose(self):
        r = self.clist.get_selected_row() if hasattr(self, 'clist') else None
        return r.path if r else None

    def on_compose_select(self):
        p = self.sel_compose()
        for b in self.cp_btns:
            b.set_sensitive(bool(p))
        self.cp_title.set_text(p or '')

    def run_compose(self, action):
        p = self.sel_compose()
        if not p:
            return
        buf = self.cp_out.get_buffer()
        buf.set_text(f'$ docker compose -f {p} {action}\n\nĐang chạy…', -1)
        for b in self.cp_btns:
            b.set_sensitive(False)

        def done(res, err):
            buf.set_text(f'$ docker compose -f {p} {action}\n\n' + (err or res or '(không có output)'), -1)
            self.on_compose_select()
            self.tick_containers(True)
        bg(lambda: D.compose(p, action), done)

    def shutdown(self):
        self.detail.stop_stream()
