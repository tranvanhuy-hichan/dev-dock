"""Dev Services: danh sách server dev của bạn, bật/tắt một chỗ, thấy cổng nào đang bị ai chiếm, xem log trực tiếp."""
import os
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from ..lib import projects as P  # noqa: E402
from ..lib import services as S  # noqa: E402
from ..lib.sysmon import Monitor  # noqa: E402
from ..ui import Page, bg, button, duration, hbox, label, vbox  # noqa: E402
from .sysmon import confirm  # noqa: E402

STATE = {'running': ('Đang chạy', 'ok'), 'stopped': ('Đã dừng', 'info'), 'failed': ('Lỗi', 'bad')}


class ServiceDialog(Adw.Dialog):
    """Thêm/sửa một service."""

    def __init__(self, page, svc=None):
        super().__init__(title='Sửa service' if svc else 'Thêm service', content_width=520)
        self.page, self.svc = page, svc
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='Huỷ')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Lưu', css_classes=['suggested-action'])
        ok.connect('clicked', lambda *_: self.save())
        hdr.pack_start(cancel)
        hdr.pack_end(ok)
        tv.add_top_bar(hdr)
        grp = Adw.PreferencesGroup(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.name = Adw.EntryRow(title='Tên', text=(svc or {}).get('name', ''))
        self.cwd = Adw.EntryRow(title='Thư mục chạy', text=(svc or {}).get('cwd', ''))
        pick = Gtk.Button(icon_name='folder-open-symbolic', valign=Gtk.Align.CENTER, css_classes=['flat'], tooltip_text='Chọn thư mục')
        pick.connect('clicked', lambda *_: self.pick())
        self.cwd.add_suffix(pick)
        self.cwd.connect('changed', lambda *_: self.suggest())
        self.cmd = Adw.EntryRow(title='Lệnh (chạy bằng bash)', text=(svc or {}).get('cmd', ''))
        self.sug_row = Adw.ComboRow(title='Gợi ý lệnh', visible=False)
        self.sug_row.connect('notify::selected', self.apply_suggestion)
        self.port = Adw.SpinRow.new_with_range(0, 65535, 1)
        self.port.set_title('Cổng (0 = không có)')
        self.port.set_value((svc or {}).get('port', 0))
        for w in (self.name, self.cwd, self.sug_row, self.cmd, self.port):
            grp.add(w)
        self.err = label('', ['error'], wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        box = vbox(0)
        box.append(grp)
        box.append(self.err)
        tv.set_content(box)
        self.set_child(tv)
        self.sugs = []
        self.suggest()

    def pick(self):
        dlg = Gtk.FileDialog(title='Thư mục chạy service')

        def done(d, res):
            try:
                self.cwd.set_text(d.select_folder_finish(res).get_path())
            except GLib.Error:
                pass
        dlg.select_folder(self.page.get_root(), None, done)

    def suggest(self):
        self.sugs = S.detect_commands(self.cwd.get_text())
        self.sug_row.set_visible(bool(self.sugs))
        self.sug_row.set_model(Gtk.StringList.new(['— chọn —'] + [c[0] for c in self.sugs]))

    def apply_suggestion(self, *_):
        i = self.sug_row.get_selected()
        if i > 0 and i - 1 < len(self.sugs):
            self.cmd.set_text(self.sugs[i - 1][1])
            if not self.name.get_text():
                self.name.set_text(os.path.basename(self.cwd.get_text().rstrip('/')))
            if not int(self.port.get_value()):
                self.port.set_value(S.guess_port(self.sugs[i - 1][1], ''))

    def save(self):
        cwd, cmd = self.cwd.get_text().strip(), self.cmd.get_text().strip()
        if not os.path.isdir(os.path.expanduser(cwd)):
            self.err.set_text('Thư mục không tồn tại')
            return
        if not cmd:
            self.err.set_text('Cần nhập lệnh để chạy')
            return
        self.page.save_service(self.svc, self.name.get_text(), cwd, cmd, int(self.port.get_value()))
        self.close()


class ServicesPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.mgr = window.services
        self.sel_id, self.log_seq, self.rows = None, 0, {}
        box = hbox(0)
        # ---- danh sách bên trái
        left = vbox(0, width_request=300)
        head = hbox(6, margin_top=10, margin_bottom=6, margin_start=10, margin_end=10)
        head.append(label('Services', ['heading'], hexpand=True))
        head.append(button('Thêm', icon='list-add-symbolic', css=['suggested-action'], on=lambda: ServiceDialog(self).present(self.get_root())))
        left.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.list.connect('row-selected', lambda _l, r: self.select(r.sid if r else None))
        left.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        # ---- chi tiết
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.empty = Adw.StatusPage(icon_name='media-playback-start-symbolic', title='Chưa có service',
                                    description='Thêm server dev của bạn (npm run dev, cargo run, docker compose up…) để bật/tắt và xem log tại một chỗ.')
        self.empty.set_child(button('Thêm service đầu tiên', css=['suggested-action', 'pill'], on=lambda: ServiceDialog(self).present(self.get_root())))
        self.stack.add_named(self.empty, 'empty')
        self.stack.add_named(self._detail(), 'detail')
        box.append(self.stack)
        self.set_child(box)
        self.every(1, self.tick)
        self.reload()

    # ---- dữ liệu
    @property
    def svcs(self):
        return self.window.store['services']

    def get(self, sid):
        return next((s for s in self.svcs if s['id'] == sid), None)

    def save_service(self, old, name, cwd, cmd, port):
        if old:
            old.update(name=name.strip() or old['name'], cwd=cwd, cmd=cmd, port=port)
            sid = old['id']
        else:
            s = S.new_service(name, cwd, cmd, port)
            self.svcs.append(s)
            sid = s['id']
        self.window.store.save()
        self.reload(sid)

    def delete_service(self):
        s = self.get(self.sel_id)
        if not s:
            return

        def go():
            self.mgr.forget(s['id'])
            self.svcs.remove(s)
            self.window.store.save()
            self.reload()
        confirm(self.window, f"Xoá service {s['name']}?", 'Chỉ xoá khỏi danh sách; không xoá file nào. Nếu đang chạy sẽ bị dừng.', 'Xoá', go)

    def reload(self, select=None):
        self.list.remove_all()
        self.rows = {}
        for s in self.svcs:
            row = Gtk.ListBoxRow()
            row.sid = s['id']
            line = hbox(10, margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
            dot = label('●', ['dim-label'])
            col = vbox(0, hexpand=True)
            name = label(s['name'], ['heading'], ellipsize=3)
            sub = label('', ['dim-label', 'caption'], ellipsize=3)
            col.append(name)
            col.append(sub)
            line.append(dot)
            line.append(col)
            row.set_child(line)
            self.list.append(row)
            self.rows[s['id']] = (dot, name, sub)
        self.stack.set_visible_child_name('detail' if self.svcs else 'empty')
        want = select or (self.sel_id if self.get(self.sel_id) else (self.svcs[0]['id'] if self.svcs else None))
        for r in self._rows():
            if r.sid == want:
                self.list.select_row(r)
        if not want:
            self.sel_id = None
        self.tick()

    def _rows(self):
        r = self.list.get_first_child()
        while r:
            yield r
            r = r.get_next_sibling()

    # ---- giao diện chi tiết
    def _detail(self):
        box = vbox(10, margin_top=14, margin_bottom=14, margin_start=18, margin_end=18)
        top = hbox(10)
        self.d_name = label('', ['title-2'], hexpand=True, ellipsize=3)
        self.d_badge = label('', ['badge'])
        top.append(self.d_name)
        top.append(self.d_badge)
        box.append(top)
        self.d_meta = label('', ['dim-label'], wrap=True, selectable=True, max_width_chars=70, wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(self.d_meta)
        self.d_warn = Adw.Banner(revealed=False, button_label='Giải phóng cổng')
        self.d_warn.connect('button-clicked', lambda *_: self.free_port())
        box.append(self.d_warn)
        bar = hbox(6)
        self.b_start = button('Chạy', icon='media-playback-start-symbolic', css=['suggested-action'], on=self.start)
        self.b_stop = button('Dừng', icon='media-playback-stop-symbolic', on=self.stop)
        self.b_restart = button('Chạy lại', icon='view-refresh-symbolic', on=self.restart)
        self.b_open = button('Mở trình duyệt', icon='web-browser-symbolic', on=self.open_browser)
        self.b_logs = button('Log Viewer', icon='utilities-terminal-symbolic', on=lambda: self.window.open_logs(('service', self.sel_id, self.get(self.sel_id)['name'])))
        self.b_code = button('VS Code', icon='text-editor-symbolic', css=['flat'], on=lambda: self.guard(lambda: P.open_vscode(self.get(self.sel_id)['cwd'])))
        self.b_edit = button('Sửa', css=['flat'], on=lambda: ServiceDialog(self, self.get(self.sel_id)).present(self.get_root()))
        self.b_del = button('Xoá', css=['flat'], on=self.delete_service)
        for b in (self.b_start, self.b_stop, self.b_restart, self.b_open, self.b_logs, self.b_code, self.b_edit, self.b_del):
            bar.append(b)
        box.append(bar)
        self.log = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.log.add_css_class('mono')
        self.logbuf = self.log.get_buffer()
        sw = Gtk.ScrolledWindow(child=self.log, vexpand=True)
        sw.add_css_class('codebox')
        self.log_sw = sw
        box.append(sw)
        return box

    def guard(self, fn):
        try:
            fn()
        except RuntimeError as e:
            self.toast(str(e))

    def select(self, sid):
        self.sel_id, self.log_seq = sid, 0
        self.logbuf.set_text('', -1)
        self.tick()

    # ---- hành động
    def start(self):
        self.guard(lambda: self.mgr.start(self.get(self.sel_id)))
        self.log_seq = 0
        self.logbuf.set_text('', -1)
        self.tick()

    def stop(self):
        self.mgr.stop(self.sel_id)
        GLib.timeout_add(400, lambda: self.tick() or False)

    def restart(self):
        s = self.get(self.sel_id)
        self.toast('Đang chạy lại…')

        def done(_r, err):
            if err:
                self.toast(err)
            self.log_seq = 0
            self.logbuf.set_text('', -1)
        bg(lambda: self.mgr.restart(s), done)

    def open_browser(self):
        s = self.get(self.sel_id)
        if s and s['port']:
            Gio.AppInfo.launch_default_for_uri(f"http://localhost:{s['port']}", None)

    def free_port(self):
        s = self.get(self.sel_id)
        owner = S.port_owner(s['port']) if s and s['port'] else None
        if not owner or not owner[0]:
            self.toast('Không xác định được process đang chiếm cổng (có thể thuộc user khác)')
            return

        def go():
            try:
                Monitor.kill(owner[0], False)
                self.toast(f'Đã dừng {owner[1]}')
            except RuntimeError as e:
                self.toast(str(e))
        confirm(self.window, f"Giải phóng cổng {s['port']}?", f'Sẽ dừng {owner[1]} (PID {owner[0]}).', 'Dừng', go)

    # ---- cập nhật định kỳ
    def tick(self):
        for s in self.svcs:
            st = self.mgr.status(s['id'])
            dot, _name, sub = self.rows[s['id']]
            for c in ('success', 'error', 'dim-label'):
                dot.remove_css_class(c)
            dot.add_css_class({'running': 'success', 'failed': 'error'}.get(st['state'], 'dim-label'))
            sub.set_text(s['cmd'] + (f"  ·  :{s['port']}" if s['port'] else ''))
        s = self.get(self.sel_id)
        if not s:
            return
        st = self.mgr.status(s['id'])
        txt, css = STATE[st['state']]
        self.d_name.set_text(s['name'])
        for c in ('ok', 'info', 'bad'):
            self.d_badge.remove_css_class(c)
        self.d_badge.add_css_class(css)
        self.d_badge.set_text(txt)
        running = st['state'] == 'running'
        meta = f"$ {s['cmd']}\n{s['cwd']}"
        if running:
            meta += f"\nPID {st['pid']} · chạy được {duration(time.time() - st['since'])}"
        elif st['rc'] is not None:
            meta += f"\nThoát với mã {st['rc']}"
        if s['port']:
            up = S.port_open(s['port'])
            meta += f"\nCổng {s['port']}: " + ('đang mở' if up else 'chưa mở')
        self.d_meta.set_text(meta)
        for b, on in ((self.b_start, not running), (self.b_stop, running), (self.b_restart, True), (self.b_open, bool(s['port']) and running)):
            b.set_sensitive(on)
        conflict = None
        if s['port'] and not running and S.port_open(s['port']):
            conflict = S.port_owner(s['port'])
            self.d_warn.set_title(f"Cổng {s['port']} đang bị {conflict[1] if conflict else 'một process khác'} chiếm" + (f' (PID {conflict[0]})' if conflict and conflict[0] else ''))
        self.d_warn.set_revealed(bool(conflict) or bool(s['port'] and not running and S.port_open(s['port'])))
        # log tăng dần
        run = self.mgr.runs.get(s['id'])
        if run:
            new, seq = run.since(self.log_seq)
            if new:
                at_end = self.log_sw.get_vadjustment().get_value() + self.log_sw.get_vadjustment().get_page_size() >= self.log_sw.get_vadjustment().get_upper() - 40
                self.logbuf.insert(self.logbuf.get_end_iter(), '\n'.join(ln for _, ln in new) + '\n')
                self.log_seq = seq
                if self.logbuf.get_line_count() > 3000:
                    cut = self.logbuf.get_iter_at_line(self.logbuf.get_line_count() - 2500)
                    self.logbuf.delete(self.logbuf.get_start_iter(), cut)
                if at_end:
                    GLib.idle_add(lambda: self.log_sw.get_vadjustment().set_value(self.log_sw.get_vadjustment().get_upper()) or False)

    def shutdown(self):
        pass  # dừng ở MainWindow._on_close qua window.services.shutdown()
