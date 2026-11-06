"""DockerDesk: container (start/stop/restart/xoá, log, shell), image (pull/xoá), Docker Compose."""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import dockercli as D  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import DataTable, Page, bg, button, esc, hbox, human, label, vbox  # noqa: E402
from .sysmon import confirm  # noqa: E402

OK, WARN, BAD, DIM = '#26a269', '#e5a50a', '#e01b24', '#9a9996'
STATE_COLOR = {'running': OK, 'paused': WARN, 'restarting': WARN, 'exited': BAD, 'dead': BAD, 'created': DIM}


class DockerPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.ok, self.busy_c, self.busy_i, self.show_all = False, False, False, True
        self.stack = Adw.ViewStack()
        root = vbox(0)
        top = hbox(8, margin_top=8, halign=Gtk.Align.CENTER)
        top.append(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        root.append(top)
        self.info = label('', ['dim-label', 'caption'], xalign=0.5, margin_top=4, ellipsize=3)
        root.append(self.info)
        self.down = Adw.StatusPage(icon_name='package-x-generic-symbolic', title='Không kết nối được Docker', vexpand=True, visible=False)
        root.append(self.down)
        self.stack.add_titled_with_icon(self._containers(), 'containers', 'Containers', 'package-x-generic-symbolic')
        self.stack.add_titled_with_icon(self._images(), 'images', 'Images', 'image-x-generic-symbolic')
        self.stack.add_titled_with_icon(self._compose(), 'compose', 'Compose', 'view-list-symbolic')
        self.stack.set_vexpand(True)
        root.append(self.stack)
        self.set_child(root)
        self.every(5, self.tick_status).every(3, self.tick_containers).every(8, self.tick_images)

    def show_tool(self, name):
        self.stack.set_visible_child_name(name)

    def tab(self):
        return self.stack.get_visible_child_name()

    def tick_status(self):
        def done(st, _err):
            self.ok = bool(st and st['available'])
            self.stack.set_visible(self.ok)
            self.down.set_visible(not self.ok and bool(st))
            if st and not st['available']:
                self.down.set_description((st['error'] or '') + '\nHãy chắc chắn Docker đang chạy và user thuộc nhóm docker (sudo usermod -aG docker $USER).')
            if st and st['info']:
                i = st['info']
                self.info.set_text(f"Docker {i['version']} · {i['running']}/{i['containers']} container chạy · {i['images']} image · {i['cpus']} CPU · {human(i['mem_total'])} RAM")
            elif st:
                self.info.set_text('')
        bg(D.status, done)

    def guard_err(self, err):
        if err:
            self.toast(err)

    # ---------------- Containers ----------------
    def _containers(self):
        box = vbox(8, margin_top=12, margin_bottom=14, margin_start=18, margin_end=18)
        bar = hbox(6)
        s = Gtk.SearchEntry(placeholder_text='Tìm container, image, project…', hexpand=True)
        s.connect('search-changed', lambda e: self.ct.set_query(e.get_text()))
        bar.append(s)
        self.all_cb = Gtk.CheckButton(label='Hiện cả container đã dừng', active=True)
        self.all_cb.connect('toggled', lambda b: (setattr(self, 'show_all', b.get_active()), self.tick_containers(True)))
        bar.append(self.all_cb)
        box.append(bar)
        acts = hbox(6)
        self.cbtn = {}
        for k, t, ic, fn, css in (('start', 'Khởi động', 'media-playback-start-symbolic', lambda: self.act('start'), ['suggested-action']),
                                  ('stop', 'Dừng', 'media-playback-stop-symbolic', lambda: self.act('stop'), None),
                                  ('restart', 'Restart', 'view-refresh-symbolic', lambda: self.act('restart'), None),
                                  ('logs', 'Xem log', 'utilities-terminal-symbolic', self.open_logs, None),
                                  ('shell', 'Shell', 'utilities-terminal-symbolic', self.open_shell, None),
                                  ('remove', 'Xoá', 'user-trash-symbolic', self.remove, ['destructive-action'])):
            b = button(t, icon=ic, css=css, on=fn)
            b.set_sensitive(False)
            self.cbtn[k] = b
            acts.append(b)
        box.append(acts)
        cols = [
            {'title': 'Tên', 'get': lambda d: f"<b>{esc(d['name'])}</b>" + (f"  <span foreground='{DIM}'>{esc(d['project'])}</span>" if d['project'] else ''), 'markup': True, 'sort': lambda d: d['name'], 'width': 280, 'tip': lambda d: d['id'][:12]},
            {'title': 'Image', 'get': lambda d: d['image'], 'sort': lambda d: d['image'], 'width': 260},
            {'title': 'Trạng thái', 'get': lambda d: f"<span foreground='{STATE_COLOR.get(d['state'], DIM)}'>● {esc(d['state'])}</span>  {esc(d['status'])}", 'markup': True, 'sort': lambda d: (d['state'] != 'running', d['name']), 'width': 280},
            {'title': 'Cổng', 'get': lambda d: ', '.join(d['ports'][:3]) + (f" +{len(d['ports']) - 3}" if len(d['ports']) > 3 else ''), 'expand': True, 'tip': lambda d: '\n'.join(d['ports'])},
        ]
        self.ct = DataTable(cols, key=lambda d: d['id'], search_text=lambda d: f"{d['name']} {d['image']} {d['project'] or ''}", on_select=self.on_select)
        self.ct.sort_by(2, False)
        box.append(self.ct)
        return box

    def on_select(self, d):
        running = bool(d and d['state'] == 'running')
        for k, b in self.cbtn.items():
            b.set_sensitive(bool(d) and (k not in ('stop', 'shell') or running) and (k != 'start' or not running))

    def tick_containers(self, force=False):
        if not self.ok or (self.tab() != 'containers' and not force) or self.busy_c:
            return
        self.busy_c = True

        def done(rows, err):
            self.busy_c = False
            if rows is not None:
                self.ct.set_rows(rows)
        bg(lambda: D.containers(self.show_all), done)

    def act(self, action):
        d = self.ct.selected()
        if not d:
            return
        bg(lambda: D.action(d['id'], action), lambda _r, err: (self.guard_err(err), self.tick_containers(True)))

    def open_logs(self):
        d = self.ct.selected()
        if d:
            self.window.open_logs(('docker', d['id'], d['name']))

    def open_shell(self):
        d = self.ct.selected()
        if not d:
            return
        try:
            P.open_terminal(cmd=D.exec_argv(d['id']))
        except RuntimeError as e:
            self.toast(str(e))

    def remove(self):
        d = self.ct.selected()
        if d:
            confirm(self.window, f"Xoá container {d['name']}?", 'Container sẽ bị buộc dừng và xoá. Dữ liệu trong volume ẩn danh có thể mất.', 'Xoá',
                    lambda: bg(lambda: D.action(d['id'], 'remove'), lambda _r, err: (self.guard_err(err), self.tick_containers(True))))

    # ---------------- Images ----------------
    def _images(self):
        box = vbox(8, margin_top=12, margin_bottom=14, margin_start=18, margin_end=18)
        bar = hbox(6)
        self.pull_entry = Gtk.Entry(placeholder_text='Pull image, vd. nginx:alpine', hexpand=True)
        self.pull_entry.connect('activate', lambda *_: self.pull())
        self.pull_btn = button('Pull', icon='folder-download-symbolic', css=['suggested-action'], on=self.pull)
        self.rm_img = button('Xoá image', icon='user-trash-symbolic', css=['destructive-action'], on=self.remove_image)
        self.rm_img.set_sensitive(False)
        for w in (self.pull_entry, self.pull_btn, self.rm_img):
            bar.append(w)
        box.append(bar)
        cols = [
            {'title': 'Image', 'get': lambda d: d['tag'], 'sort': lambda d: d['tag'], 'expand': True},
            {'title': 'ID', 'get': lambda d: d['id'].replace('sha256:', '')[:12], 'width': 130, 'css': ['mono']},
            {'title': 'Kích thước', 'get': lambda d: d['size'], 'width': 120, 'align': 1.0},
            {'title': 'Tạo', 'get': lambda d: d['created'], 'width': 160},
            {'title': 'Sử dụng', 'get': lambda d: f"<span foreground='{OK if d['used'] else DIM}'>{'đang dùng' if d['used'] else 'không dùng'}</span>", 'markup': True, 'sort': lambda d: d['used'], 'width': 120},
        ]
        self.it = DataTable(cols, key=lambda d: d['id'], search_text=lambda d: d['tag'], on_select=lambda d: self.rm_img.set_sensitive(d is not None))
        box.append(self.it)
        return box

    def tick_images(self, force=False):
        if not self.ok or (self.tab() != 'images' and not force) or self.busy_i:
            return
        self.busy_i = True

        def done(rows, _err):
            self.busy_i = False
            if rows is not None:
                self.it.set_rows(rows)
        bg(D.images, done)

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
            confirm(self.window, 'Xoá image?', d['tag'], 'Xoá', lambda: bg(lambda: D.remove_image(d['id']), lambda _r, err: (self.guard_err(err), self.tick_images(True))))

    # ---------------- Compose ----------------
    def _compose(self):
        box = hbox(0)
        left = vbox(6, width_request=300, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        left.append(button('Thêm file compose…', icon='list-add-symbolic', on=self.add_compose))
        left.append(button('Tìm trong thư mục project', icon='system-search-symbolic', css=['flat'], on=self.find_compose))
        self.clist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.clist.connect('row-selected', lambda *_: self.on_compose_select())
        left.append(Gtk.ScrolledWindow(child=self.clist, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        right = vbox(8, hexpand=True, margin_top=12, margin_bottom=12, margin_start=14, margin_end=14)
        bar = hbox(6)
        self.cp_btns = []
        for act, t, ic, css in (('up', 'Up -d', 'media-playback-start-symbolic', ['suggested-action']), ('stop', 'Stop', 'media-playback-stop-symbolic', None),
                                ('restart', 'Restart', 'view-refresh-symbolic', None), ('down', 'Down', 'edit-delete-symbolic', ['destructive-action']),
                                ('pull', 'Pull', 'folder-download-symbolic', None), ('ps', 'ps', None, ['flat']), ('logs', 'logs', None, ['flat'])):
            b = button(t, icon=ic, css=css, on=lambda a=act: self.run_compose(a))
            b.set_sensitive(False)
            self.cp_btns.append(b)
            bar.append(b)
        right.append(bar)
        self.cp_title = label('', ['dim-label', 'caption'], ellipsize=1)
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

    def load_compose_list(self):
        self.clist.remove_all()
        for f in self.files():
            row = Gtk.ListBoxRow()
            row.path = f
            col = vbox(0, margin_top=6, margin_bottom=6, margin_start=10, margin_end=8)
            col.append(label(os.path.basename(os.path.dirname(f)) or f, ['heading'], ellipsize=3))
            col.append(label(f, ['dim-label', 'caption'], ellipsize=1))
            line = hbox(4)
            col.set_hexpand(True)
            line.append(col)
            rm = Gtk.Button(icon_name='window-close-symbolic', css_classes=['flat', 'circular'], valign=Gtk.Align.CENTER, tooltip_text='Bỏ khỏi danh sách')
            rm.connect('clicked', lambda _b, p=f: self.remove_compose(p))
            line.append(rm)
            row.set_child(line)
            self.clist.append(row)

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
        r = self.clist.get_selected_row()
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
