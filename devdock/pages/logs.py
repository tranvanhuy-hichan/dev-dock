"""Log Viewer: xem log trực tiếp từ Docker, journal, file và dev service; lọc, tô màu theo mức lỗi, tạm dừng, lưu."""
import os
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import dockercli  # noqa: E402
from ..lib import logsrc as L  # noqa: E402
from ..ui import Page, bg, content_box, copy_text, debounce, hbox, icon_btn, label, linked, toolbar, vbox  # noqa: E402

COLORS = {'error': '#e01b24', 'warn': '#c88800', 'debug': '#8d8d8a'}
SHOW_MAX = 5000


class LogsPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.stream, self.source, self.sources = None, None, []
        self.all = deque(maxlen=20000)
        self.pending = deque()
        self.paused = False
        self.matcher = lambda line: True
        self.levels = set(L.LEVELS) | {None}
        self.shown = 0
        self.docker_rows = []
        # --- thanh tiêu đề: mở file (trái); tạm dừng, tự cuộn, xoá, copy, lưu (phải)
        self.hdr_start = [icon_btn('document-open-symbolic', 'Mở file log…', self.pick_file)]
        self.pause = icon_btn('media-playback-pause-symbolic', 'Tạm dừng', toggle=True)
        self.pause.connect('toggled', lambda b: setattr(self, 'paused', b.get_active()))
        self.follow = icon_btn('go-bottom-symbolic', 'Tự cuộn xuống dòng mới nhất', toggle=True)
        self.follow.set_active(True)
        self.hdr_end = [self.pause, self.follow, icon_btn('edit-clear-all-symbolic', 'Xoá màn hình', self.clear),
                        icon_btn('edit-copy-symbolic', 'Copy log đang hiển thị', self.copy), icon_btn('document-save-symbolic', 'Lưu ra file…', self.save)]
        box = hbox(0)
        # ---- trái: nguồn log
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'], width_request=260)
        self.list.set_header_func(self._header)
        self.list.connect('row-selected', lambda _l, r: r and self.select(r.source))
        box.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        # ---- phải: một thanh lọc duy nhất + log + dòng trạng thái
        right = content_box(10)
        right.set_hexpand(True)
        right.set_margin_bottom(8)
        self.search = Gtk.SearchEntry(placeholder_text='Lọc dòng log…', hexpand=True)
        self.search.connect('search-changed', debounce(self.refilter, 200))
        self.rx = Gtk.ToggleButton(label='.*', tooltip_text='Dùng regex')
        self.rx.connect('toggled', lambda *_: self.refilter())
        self.lvl_btns = {}
        for lv, text in (('error', 'Lỗi'), ('warn', 'Cảnh báo'), ('info', 'Info'), ('debug', 'Debug')):
            b = Gtk.ToggleButton(label=text, active=True)
            b.connect('toggled', lambda *_: self.refilter())
            self.lvl_btns[lv] = b
        right.append(toolbar(self.search, self.rx, linked(*self.lvl_btns.values())))
        self.err = label('', ['error'], wrap=True, visible=False)
        right.append(self.err)
        self.tv = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.tv.add_css_class('mono')
        self.buf = self.tv.get_buffer()
        for lv, c in COLORS.items():
            self.buf.create_tag(lv, foreground=c)
        self.buf.create_tag('hit', background='#3584e466')
        self.sw = Gtk.ScrolledWindow(child=self.tv, vexpand=True)
        self.sw.add_css_class('codebox')
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(Adw.StatusPage(icon_name='utilities-terminal-symbolic', title='Chọn nguồn log', description='Chọn một container, dev service, journal hoặc mở một file log ở bên trái.'), 'empty')
        self.stack.add_named(self.sw, 'log')
        right.append(self.stack)
        self.status = label('', ['dim-label', 'caption'], xalign=0, ellipsize=3, margin_top=2)
        right.append(self.status)
        box.append(right)
        self.set_child(box)
        self.every(0.2, self.flush).every(6, self.refresh_sources)

    # ---- nguồn
    def _header(self, row, before):
        group = row.source[0] if row.source[0] in ('docker', 'service', 'file') else 'system'
        prev = None if before is None else (before.source[0] if before.source[0] in ('docker', 'service', 'file') else 'system')
        names = {'docker': 'Docker containers', 'service': 'Dev services', 'system': 'Hệ thống', 'file': 'Tệp'}
        row.set_header(label(names[group], ['dim-label', 'caption'], margin_top=10, margin_bottom=2, margin_start=12) if group != prev else None)

    def refresh_sources(self):
        def done(rows, _err):
            self.docker_rows = rows or []
            self.rebuild()
        bg(lambda: dockercli.containers(True), done)

    def build_sources(self):
        out = []
        for c in self.docker_rows:
            out.append((('docker', c['id'], c['name']), c['name'], f"{c['state']} · {c['image']}"))
        for s in self.window.store['services']:
            out.append((('service', s['id'], s['name']), s['name'], 'dev service'))
        out.append((('journal',), 'Journal hệ thống', 'journalctl -f'))
        out.append((('journal-user',), 'Journal người dùng', 'journalctl --user -f'))
        for f in self.window.store['log_files']:
            out.append((('file', f), os.path.basename(f), f))
        return out

    def rebuild(self):
        srcs = self.build_sources()
        sig = [(s[0], s[1], s[2]) for s in srcs]
        if sig == self.sources:
            return
        self.sources = sig
        cur = self.source
        self.list.remove_all()
        for src, title, sub in srcs:
            row = Gtk.ListBoxRow()
            row.source = src
            col = vbox(0, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            col.append(label(title, ellipsize=3))
            col.append(label(sub, ['dim-label', 'caption'], ellipsize=3))
            row.set_child(col)
            self.list.append(row)
            if cur and src[:2] == cur[:2]:
                self.list.select_row(row)

    def open_source(self, source):
        """Gọi từ trang khác (DockerDesk, Dev Services)."""
        if source[0] == 'file' and source[1] not in self.window.store['log_files']:
            self.window.store['log_files'] = ([source[1]] + self.window.store['log_files'])[:10]
            self.window.store.save()
        if not self.docker_rows and source[0] == 'docker':
            self.docker_rows = [{'id': source[1], 'name': source[2], 'state': '', 'image': ''}]
        self.sources = []
        self.rebuild()
        for r in self._rows():
            if r.source[:2] == source[:2]:
                self.list.select_row(r)
                return
        self.select(source)

    def _rows(self):
        r = self.list.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):  # ListBox có header thì header cũng là con trực tiếp
                yield r
            r = r.get_next_sibling()

    def pick_file(self):
        dlg = Gtk.FileDialog(title='Chọn file log')

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            self.open_source(('file', path))
        dlg.open(self.get_root(), None, done)

    def select(self, source):
        if self.stream:
            self.stream.stop()
        self.source = source
        self.all.clear()
        self.pending.clear()
        self.buf.set_text('', -1)
        self.shown = 0
        self.err.set_visible(False)
        self.stream = L.open_stream(source, self.window.services)
        if self.stream.error:
            self.err.set_text(self.stream.error)
            self.err.set_visible(True)
        self.stack.set_visible_child_name('log')
        self.update_status()

    # ---- hiển thị
    def passes(self, line, lvl):
        return lvl in self.levels and self.matcher(line)

    def flush(self):
        if not self.stream:
            return
        new = self.stream.drain(1500)
        if self.paused:
            self.pending.extend(new)
            self.update_status()
            return
        if self.pending:
            new = list(self.pending) + new
            self.pending.clear()
        if not new:
            return
        for line in new:
            lvl = L.level_of(line)
            self.all.append((line, lvl))
            if self.passes(line, lvl):
                self.insert(line, lvl)
        self.trim()
        if self.follow.get_active():
            GLib.idle_add(lambda: self.tv.scroll_to_mark(self.buf.get_insert(), 0, False, 0, 0) or self.sw.get_vadjustment().set_value(self.sw.get_vadjustment().get_upper()) or False)
        self.update_status()

    def insert(self, line, lvl):
        end = self.buf.get_end_iter()
        if lvl in COLORS:
            self.buf.insert_with_tags_by_name(end, line + '\n', lvl)
        else:
            self.buf.insert(end, line + '\n')
        self.shown += 1

    def trim(self):
        n = self.buf.get_line_count()
        if n > SHOW_MAX + 1000:
            self.buf.delete(self.buf.get_start_iter(), self.buf.get_iter_at_line(1000))
            self.shown -= 1000

    def refilter(self, *_):
        try:
            self.matcher = L.compile_query(self.search.get_text(), self.rx.get_active())
            self.err.set_visible(bool(self.stream and self.stream.error))
        except ValueError as e:
            self.err.set_text(f'Regex không hợp lệ: {e}')
            self.err.set_visible(True)
            return
        self.levels = {lv for lv, b in self.lvl_btns.items() if b.get_active()}
        if self.lvl_btns['info'].get_active():
            self.levels.add(None)  # dòng không rõ mức được coi như info
        self.buf.set_text('', -1)
        self.shown = 0
        for line, lvl in list(self.all)[-SHOW_MAX * 3:]:
            if self.passes(line, lvl):
                self.insert(line, lvl)
        self.trim()
        self.update_status()

    def update_status(self):
        if not self.source:
            self.status.set_text('')
            return
        state = 'đã dừng' if self.stream and self.stream.closed else 'đang theo dõi'
        extra = f' · đang giữ {len(self.pending)} dòng (tạm dừng)' if self.paused and self.pending else ''
        name = os.path.basename(self.source[1]) if self.source[0] == 'file' else (self.source[-1] if len(self.source) > 1 else 'journal')
        self.status.set_text(f'{self.shown} / {len(self.all)} dòng · {name} · {state}{extra}')

    def clear(self):
        self.all.clear()
        self.buf.set_text('', -1)
        self.shown = 0
        self.update_status()

    def text(self):
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def copy(self):
        copy_text(self, self.text())
        self.toast('Đã sao chép log đang hiển thị')

    def save(self):
        dlg = Gtk.FileDialog(title='Lưu log', initial_name='log.txt')
        text = self.text()

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            try:
                with open(f.get_path(), 'w', encoding='utf-8') as out:
                    out.write(text)
                self.toast(f'Đã lưu {f.get_path()}')
            except OSError as e:
                self.toast(f'Lỗi: {e}')
        dlg.save(self.get_root(), None, done)

    def show_tool(self, name):  # dùng cho chế độ chụp ảnh kiểm thử
        if name == 'file' and self.window.store['log_files']:
            self.open_source(('file', self.window.store['log_files'][0]))

    def shutdown(self):
        if self.stream:
            self.stream.stop()
