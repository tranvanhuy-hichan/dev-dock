"""Database Explorer: PostgreSQL, MySQL/MariaDB, SQLite, Redis. Tự phát hiện DB trong Docker; mặc định CHỈ ĐỌC (ép ở cả tầng DB)."""
import json
import os
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..lib import dbclient as D  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, SelectionBar, bg, button, content_box, copy_button, copy_text, debounce, esc, hbox, icon_btn, label, linked, toolbar, vbox)  # noqa: E402
from .sysmon import confirm  # noqa: E402

DIM = '#9a9996'
GROUP = {'docker': 'Docker', 'file': 'SQLite', 'tcp': 'Kết nối TCP'}
KIND_NAME = {'postgres': 'PostgreSQL', 'mysql': 'MySQL / MariaDB', 'redis': 'Redis', 'sqlite': 'SQLite'}
KIND_ICON = {'postgres': 'drive-multidisk-symbolic', 'mysql': 'drive-multidisk-symbolic', 'redis': 'view-list-symbolic', 'sqlite': 'text-x-generic-symbolic'}
PAGE_SIZES = [50, 100, 200, 500]


def cell_markup(v):
    if v is None:
        return f"<span foreground='{DIM}'>NULL</span>"
    s = str(v).replace('\n', '⏎').replace('\t', ' ')
    return esc(s[:300] + ('…' if len(s) > 300 else ''))


def sort_key(v):
    if v is None:
        return (0, 0.0, '')
    try:
        return (1, float(v), '')
    except (TypeError, ValueError):
        return (2, 0.0, str(v).lower())


def make_grid(cols, rows, on_select=None):
    """Bảng kết quả với cột động (tên cột lấy từ truy vấn)."""
    specs = []
    for i, name in enumerate(cols):
        specs.append({'title': (name or f'#{i + 1}').replace('_', '__'), 'get': lambda d, i=i: cell_markup(d['v'][i]), 'markup': True, 'sort': lambda d, i=i: sort_key(d['v'][i]),
                      'width': 150, 'expand': i == len(cols) - 1, 'tip': lambda d, i=i: None if d['v'][i] is None else str(d['v'][i])[:2000]})
    t = DataTable(specs, key=lambda d: d['n'], on_select=on_select)
    t.set_rows([{'n': n, 'v': r} for n, r in enumerate(rows)])
    return t


def fmt_rows(n):
    if n is None:
        return ''
    return f'≈{n / 1e6:.1f}M' if n >= 1_000_000 else (f'≈{n / 1e3:.1f}k' if n >= 10_000 else f'{n}')


class RowDialog(Adw.Dialog):
    """Xem đầy đủ một dòng (mỗi cột một hàng, chọn/copy được)."""

    def __init__(self, cols, vals):
        super().__init__(title='Chi tiết dòng', content_width=640, content_height=560)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'], margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        for c, v in zip(cols, vals):
            r = Adw.ActionRow(title=esc(c), subtitle=esc('NULL' if v is None else str(v)[:20000]) or ' ', subtitle_selectable=True, subtitle_lines=0)
            r.add_css_class('property')
            r.add_suffix(copy_button('' if v is None else str(v)))
            lb.append(r)
        tv.set_content(Gtk.ScrolledWindow(child=lb, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(tv)


class TcpDialog(Adw.Dialog):
    """Thêm kết nối TCP (mật khẩu chỉ giữ trong bộ nhớ, không lưu)."""

    def __init__(self, page):
        super().__init__(title='Thêm kết nối TCP', content_width=480)
        self.page = page
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='Huỷ')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Kết nối', css_classes=['suggested-action'])
        ok.connect('clicked', lambda *_: self.save())
        hdr.pack_start(cancel)
        hdr.pack_end(ok)
        tv.add_top_bar(hdr)
        grp = Adw.PreferencesGroup(margin_top=12, margin_bottom=8, margin_start=12, margin_end=12, description='Mật khẩu chỉ giữ trong bộ nhớ của phiên này, không được lưu.')
        self.kind = Adw.ComboRow(title='Loại', model=Gtk.StringList.new(['PostgreSQL', 'MySQL / MariaDB', 'Redis']))
        self.name = Adw.EntryRow(title='Tên hiển thị')
        self.host = Adw.EntryRow(title='Máy chủ', text='127.0.0.1')
        self.port = Adw.EntryRow(title='Cổng (để trống = mặc định)')
        self.user = Adw.EntryRow(title='Người dùng')
        self.db = Adw.EntryRow(title='Database (tuỳ chọn)')
        self.pw = Adw.PasswordEntryRow(title='Mật khẩu')
        for w in (self.kind, self.name, self.host, self.port, self.user, self.db, self.pw):
            grp.add(w)
        self.err = label('', ['error'], wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        box = vbox(0)
        box.append(grp)
        box.append(self.err)
        tv.set_content(box)
        self.set_child(tv)

    def save(self):
        kind = ['postgres', 'mysql', 'redis'][self.kind.get_selected()]
        host = self.host.get_text().strip()
        port = self.port.get_text().strip()
        if not host:
            self.err.set_text('Cần nhập máy chủ')
            return
        if port and not (port.isdigit() and 0 < int(port) < 65536):
            self.err.set_text('Cổng không hợp lệ')
            return
        port = int(port) if port else {'postgres': 5432, 'mysql': 3306, 'redis': 6379}[kind]
        c = D.make_conn(kind, 'tcp', self.name.get_text().strip() or f'{host}:{port}', host=host, port=port, user=self.user.get_text().strip(), db=self.db.get_text().strip(),
                        password=self.pw.get_text(), sub=f'{host}:{port}')
        self.page.add_conn(c)
        self.close()


class DatabasePage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.conns, self.cur, self.db, self.tables, self.tbl = [], None, '', [], None
        self.readonly, self.pw_cache, self.history = True, {}, deque(maxlen=30)
        self.gen = {'conn': 0, 'tables': 0, 'data': 0, 'struct': 0, 'sql': 0, 'rkeys': 0, 'rval': 0}
        self.page_no, self.more, self.last = 0, False, ([], [])
        self.sel_row = None
        self.busy_n = 0
        self.saved = D.load_saved()
        self.detected = []
        self.sig = None

        # --- thanh tiêu đề
        add = Gtk.MenuButton(icon_name='list-add-symbolic', tooltip_text='Thêm kết nối', valign=Gtk.Align.CENTER)
        pop = Gtk.Popover()
        pb = vbox(2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        pb.append(button('File SQLite…', css=['flat'], on=lambda: (pop.popdown(), self.pick_sqlite())))
        pb.append(button('Kết nối TCP (Postgres/MySQL/Redis)…', css=['flat'], on=lambda: (pop.popdown(), TcpDialog(self).present(self.get_root()))))
        pop.set_child(pb)
        add.set_popover(pop)
        self.hdr_start = [add]
        self.spin = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.write_btn = icon_btn('changes-prevent-symbolic', 'Đang ở chế độ CHỈ ĐỌC — bấm để cho phép ghi', toggle=True)
        self.write_btn.connect('toggled', lambda b: self.set_write(b.get_active()))
        self.hdr_end = [self.spin, icon_btn('view-refresh-symbolic', 'Làm mới', self.refresh_all), self.write_btn]

        root = hbox(0)
        # ---- trái: kết nối + bảng
        left = vbox(0, width_request=300)
        left.append(label('Kết nối', ['dim-label', 'caption'], margin_top=10, margin_start=12, margin_bottom=2))
        self.clist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.clist.set_header_func(self._conn_header)
        self.clist.connect('row-selected', lambda _l, r: r and not self._syncing and self.select_conn(r.conn))
        self._syncing = False
        # danh sách kết nối, hoặc lời nhắc gọn khi chưa có kết nối nào (không để khoảng trống lớn)
        self.cstack = Gtk.Stack(vhomogeneous=False)
        self.cstack.add_named(Gtk.ScrolledWindow(child=self.clist, max_content_height=300, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER), 'list')
        hint = vbox(4, margin_top=10, margin_bottom=14, margin_start=12, margin_end=12)
        hint.append(label('Chưa có kết nối nào', ['heading'], xalign=0))
        hint.append(label('Database trong Docker tự hiện khi container đang chạy. Bấm + trên thanh tiêu đề để thêm file SQLite hoặc kết nối TCP.', ['dim-label', 'caption'], xalign=0, wrap=True, max_width_chars=32, width_chars=28))
        self.cstack.add_named(hint, 'empty')
        self.cstack.set_visible_child_name('empty')
        left.append(self.cstack)
        # phần chọn database/bảng chỉ hiện khi đã chọn một kết nối
        self.tbox = vbox(0, vexpand=True, visible=False)
        self.tbox.append(Gtk.Separator())
        self.db_dd = Gtk.DropDown.new_from_strings([])
        self.db_dd.set_hexpand(True)
        self.db_dd.connect('notify::selected', lambda *_: self.on_db_change())
        self.tsearch = Gtk.SearchEntry(placeholder_text='Lọc bảng…', hexpand=True)
        self.tsearch.connect('search-changed', lambda *_: self.tlist.invalidate_filter())
        tb = toolbar(self.db_dd)
        tb.set_margin_start(10)
        tb.set_margin_end(10)
        tb.set_margin_top(8)
        self.tbox.append(tb)
        tb2 = toolbar(self.tsearch)
        tb2.set_margin_start(10)
        tb2.set_margin_end(10)
        tb2.set_margin_top(4)
        tb2.set_margin_bottom(4)
        self.tbox.append(tb2)
        self.tlist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.tlist.set_header_func(self._table_header)
        self.tlist.set_filter_func(lambda r: self.tsearch.get_text().strip().lower() in r.item['name'].lower())
        self.tlist.connect('row-selected', lambda _l, r: r and not self._syncing and self.select_table(r.item))
        self.tbox.append(Gtk.ScrolledWindow(child=self.tlist, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        left.append(self.tbox)
        root.append(left)
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        # ---- phải
        self.rstack = Gtk.Stack(hexpand=True, vexpand=True)
        self.empty = Adw.StatusPage(icon_name='drive-multidisk-symbolic', title='Chọn một kết nối', description='DevDock tự tìm PostgreSQL, MySQL/MariaDB và Redis trong các container đang chạy. Thêm file SQLite hoặc kết nối TCP bằng nút + ở thanh tiêu đề.')
        self.rstack.add_named(self.empty, 'empty')
        self.rstack.add_named(self._sql_view(), 'sql')
        self.rstack.add_named(self._redis_view(), 'redis')
        right = vbox(0, hexpand=True)
        self.strip_ro = InfoStrip('warning')
        self.strip_err = InfoStrip('error')
        sbox = content_box(6)
        sbox.set_margin_bottom(0)
        sbox.append(self.strip_ro)
        sbox.append(self.strip_err)
        right.append(sbox)
        right.append(self.rstack)
        root.append(right)
        self.set_child(root)
        self.every(15, self.refresh_conns)

    # ================= tiện ích =================
    def busy(self, delta):
        self.busy_n = max(0, self.busy_n + delta)
        self.spin.set_spinning(self.busy_n > 0)

    def run(self, fn, done, key=None):
        """Chạy fn ở nền; bỏ kết quả cũ nếu trong lúc đó có yêu cầu mới cùng `key`."""
        token = None
        if key:
            self.gen[key] += 1
            token = self.gen[key]
        self.busy(1)

        def finished(res, err):
            self.busy(-1)
            if key and token != self.gen[key]:
                return
            done(res, err)
        bg(fn, finished)

    def show_error(self, msg):
        if msg:
            self.strip_err.show(str(msg).split('\n')[0][:200], str(msg))
        else:
            self.strip_err.hide()

    def set_write(self, on):
        self.readonly = not on
        self.write_btn.set_icon_name('changes-allow-symbolic' if on else 'changes-prevent-symbolic')
        self.write_btn.set_tooltip_text('CHẾ ĐỘ GHI đang bật — bấm để quay lại chỉ đọc' if on else 'Đang ở chế độ CHỈ ĐỌC — bấm để cho phép ghi')
        if on:
            self.strip_ro.show('CHẾ ĐỘ GHI đang bật: câu lệnh thay đổi dữ liệu sẽ được chạy thật (có hỏi xác nhận).')
        else:
            self.strip_ro.hide()

    def _conn_header(self, row, before):
        g = GROUP[row.conn['mode']]
        row.set_header(label(g, ['dim-label', 'caption'], margin_top=8, margin_bottom=2, margin_start=12) if before is None or GROUP[before.conn['mode']] != g else None)

    def _table_header(self, row, before):
        s = row.item.get('schema') or ''
        prev = (before.item.get('schema') or '') if before is not None else None
        show = s and (before is None or s != prev) and self.cur and self.cur['kind'] == 'postgres'
        row.set_header(label(s, ['dim-label', 'caption'], margin_top=8, margin_bottom=2, margin_start=12) if show else None)

    # ================= danh sách kết nối =================
    def refresh_all(self):
        self.refresh_conns(True)
        if self.cur:
            self.load_tables()

    def refresh_conns(self, force=False):
        def done(found, err):
            self.detected = found or []
            self.rebuild_conns()
        self.run(D.detect_docker, done, None)

    def all_conns(self):
        out = list(self.detected) + [c for c in self.saved if c['id'] not in {x['id'] for x in self.detected}]
        for c in out:
            if c['mode'] == 'tcp' and c['id'] in self.pw_cache:
                c['password'] = self.pw_cache[c['id']]
        return out

    def rebuild_conns(self):
        conns = self.all_conns()
        sig = [(c['id'], c['name']) for c in conns]
        self.conns = conns
        if sig == self.sig:
            return
        self.sig = sig
        keep = self.cur['id'] if self.cur else None
        self._syncing = True
        self.clist.remove_all()
        for c in conns:
            row = Gtk.ListBoxRow()
            row.conn = c
            line = hbox(8, margin_start=10, margin_end=4, margin_top=5, margin_bottom=5)
            line.append(Gtk.Image(icon_name=KIND_ICON[c['kind']]))
            col = vbox(0, hexpand=True)
            col.append(label(c['name'], ellipsize=3, xalign=0, max_width_chars=26))
            col.append(label(f"{KIND_NAME[c['kind']]} · {c['sub'] or D.describe(c)}", ['dim-label', 'caption'], ellipsize=3, max_width_chars=30))
            line.append(col)
            if c['mode'] in ('file', 'tcp'):
                line.append(icon_btn('window-close-symbolic', 'Bỏ khỏi danh sách', lambda c=c: self.remove_conn(c), ['flat', 'circular']))
            row.set_child(line)
            self.clist.append(row)
            if c['id'] == keep:
                self.clist.select_row(row)
        self._syncing = False
        self.cstack.set_visible_child_name('list' if conns else 'empty')
        if not conns:
            self.tbox.set_visible(False)
            self.empty.set_title('Chưa thấy database nào')

    def add_conn(self, c):
        if c['id'] not in {x['id'] for x in self.saved}:
            self.saved.append(c)
            D.save_saved(self.saved)
        if c['password']:
            self.pw_cache[c['id']] = c['password']
        self.sig = None
        self.rebuild_conns()
        for r in self._conn_rows():
            if r.conn['id'] == c['id']:
                self.clist.select_row(r)

    def _conn_rows(self):
        r = self.clist.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):
                yield r
            r = r.get_next_sibling()

    def remove_conn(self, c):
        self.saved = [x for x in self.saved if x['id'] != c['id']]
        D.save_saved(self.saved)
        self.pw_cache.pop(c['id'], None)
        if self.cur and self.cur['id'] == c['id']:
            self.cur = None
            self.rstack.set_visible_child_name('empty')
            self.tlist.remove_all()
        self.sig = None
        self.rebuild_conns()

    def pick_sqlite(self):
        dlg = Gtk.FileDialog(title='Chọn file SQLite')

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            self.add_conn(D.make_conn('sqlite', 'file', os.path.basename(path), path=path, sub=path))
        dlg.open(self.get_root(), None, done)

    # ================= chọn kết nối =================
    def select_conn(self, c):
        self.show_error('')
        if c['kind'] != 'sqlite' and c['mode'] == 'tcp' and not c['password'] and c['id'] not in self.pw_cache and c['kind'] != 'redis':
            self.ask_password(c)
            return
        self.cur, self.db, self.tbl, self.tables = c, '', None, []
        self.tlist.remove_all()
        self.rstack.set_visible_child_name('redis' if c['kind'] == 'redis' else 'sql')
        self.tbox.set_visible(True)
        self.db_dd.set_visible(False)  # chỉ hiện khi đã có danh sách database (tránh ô trống ghi "(None)")
        self.tsearch.set_placeholder_text('Lọc database…' if c['kind'] == 'redis' else 'Lọc bảng…')
        self.sql_title.set_text('Chọn một bảng ở bên trái')
        self.clear_views()
        cid = self.gen['conn'] = self.gen['conn'] + 1

        def work():
            if c['kind'] == 'redis':
                return D.redis_keyspace(c)
            return D.list_databases(c)

        def done(res, err):
            if cid != self.gen['conn']:
                return
            if err:
                self.show_error(err)
                return
            if c['kind'] == 'redis':
                self.tables = [{'schema': '', 'name': f'db{n}', 'type': 'db', 'rows': k, 'db': n} for n, k in res]
                self.render_tables()
                self.select_first()
                return
            self._syncing = True
            self.dbs = res
            self.db_dd.set_model(Gtk.StringList.new(res))
            self.db_dd.set_visible(c['kind'] in ('postgres', 'mysql') and bool(res))
            if c['kind'] != 'sqlite':
                want = c['db'] if c['db'] in res else (res[0] if res else '')
                if want in res:
                    self.db_dd.set_selected(res.index(want))
            self._syncing = False
            self.db = res[self.db_dd.get_selected()] if res else ''
            self.load_tables()
        self.run(work, done)

    def ask_password(self, c):
        d = Adw.AlertDialog(heading=f'Mật khẩu cho {c["name"]}', body=f'{D.describe(c)}\nChỉ giữ trong bộ nhớ, không lưu.')
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        d.set_extra_child(entry)
        d.add_response('no', 'Huỷ')
        d.add_response('yes', 'Kết nối')
        d.set_response_appearance('yes', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('yes')

        def resp(_d, r):
            if r == 'yes':
                self.pw_cache[c['id']] = entry.get_text()
                c['password'] = entry.get_text()
                self.select_conn(c)
        d.connect('response', resp)
        d.present(self.get_root())

    def on_db_change(self):
        if self._syncing or not self.cur or self.cur['kind'] not in ('postgres', 'mysql'):
            return
        m = self.db_dd.get_model()
        i = self.db_dd.get_selected()
        self.db = m.get_string(i) if m and i < m.get_n_items() else ''
        self.tbl = None
        self.clear_views()
        self.load_tables()

    def load_tables(self):
        c, db = self.cur, self.db

        def done(res, err):
            if err:
                self.show_error(err)
                return
            self.show_error('')
            self.tables = res
            self.render_tables()
        self.run(lambda: D.list_tables(c, db), done, 'tables')

    def render_tables(self):
        self._syncing = True
        self.tlist.remove_all()
        for t in self.tables:
            row = Gtk.ListBoxRow()
            row.item = t
            line = hbox(6, margin_start=10, margin_end=10, margin_top=4, margin_bottom=4)
            line.append(label(t['name'], ellipsize=3, xalign=0, hexpand=True))
            if t['type'] not in ('bảng', 'db'):
                line.append(label(t['type'], ['badge', 'info']))
            if t['rows'] is not None:
                line.append(label(fmt_rows(t['rows']), ['dim-label', 'caption', 'tnum']))
            row.set_child(line)
            self.tlist.append(row)
        self._syncing = False

    def select_first(self):
        r = self.tlist.get_first_child()
        while r and not isinstance(r, Gtk.ListBoxRow):
            r = r.get_next_sibling()
        if r:
            self.tlist.select_row(r)

    def select_table(self, t):
        self.tbl = t
        if self.cur['kind'] == 'redis':
            self.rdb = t['db']
            self.scan_keys()
            return
        q = (t['schema'] + '.' if t['schema'] and self.cur['kind'] == 'postgres' else '') + t['name']
        self.sql_title.set_text(q + ('' if t['rows'] is None else f"   ·   {fmt_rows(t['rows'])} dòng"))
        self.page_no = 0
        self.filter_entry.set_text('')
        self.load_data()
        self.load_struct()

    def clear_views(self):
        for holder in (self.data_holder, self.struct_cols, self.struct_idx, self.sql_holder):
            self._clear(holder)
        self.rbar.select(None)
        self.page_info.set_text('')
        self.sql_status.set_text('')

    @staticmethod
    def _clear(box):
        while w := box.get_first_child():
            box.remove(w)

    # ================= SQL view =================
    def _sql_view(self):
        root = content_box(8)
        root.set_margin_bottom(0)
        head = hbox(10)
        self.sql_title = label('', ['page-title'], hexpand=True, ellipsize=3, xalign=0)
        head.append(self.sql_title)
        self.tabs = Adw.ToggleGroup()
        for n, t in (('data', 'Dữ liệu'), ('struct', 'Cấu trúc'), ('sql', 'SQL')):
            self.tabs.add(Adw.Toggle(name=n, label=t))
        self.tabs.set_active_name('data')
        self.tabs.connect('notify::active-name', lambda g, _p: self.vstack.set_visible_child_name(g.get_active_name()))
        head.append(self.tabs)
        root.append(head)
        self.vstack = Gtk.Stack(vexpand=True)
        # --- Dữ liệu
        dv = vbox(8)
        self.filter_entry = Gtk.Entry(placeholder_text="Bộ lọc WHERE, vd. id > 10 AND name LIKE 'A%'  (Enter để áp dụng)", hexpand=True)
        self.filter_entry.connect('activate', lambda *_: (setattr(self, 'page_no', 0), self.load_data()))
        self.size_dd = Gtk.DropDown.new_from_strings([f'{n} dòng' for n in PAGE_SIZES])
        self.size_dd.set_selected(1)
        self.size_dd.connect('notify::selected', lambda *_: (setattr(self, 'page_no', 0), self.load_data()))
        self.prev_btn = icon_btn('go-previous-symbolic', 'Trang trước', lambda: self.goto_page(-1))
        self.next_btn = icon_btn('go-next-symbolic', 'Trang sau', lambda: self.goto_page(1))
        self.page_info = label('', ['dim-label', 'caption'])
        dv.append(toolbar(self.filter_entry, self.size_dd, linked(self.prev_btn, self.next_btn), self.page_info, button('CSV', icon='document-save-symbolic', css=['flat'], on=self.export_csv, tip='Xuất trang hiện tại ra CSV')))
        self.data_holder = vbox(0, vexpand=True)
        dv.append(self.data_holder)
        self.vstack.add_named(dv, 'data')
        # --- Cấu trúc
        sv = vbox(8)
        sv.append(label('Cột', ['heading'], xalign=0))
        self.struct_cols = vbox(0, vexpand=True)
        sv.append(self.struct_cols)
        sv.append(label('Index', ['heading'], xalign=0))
        self.struct_idx = vbox(0)
        sv.append(self.struct_idx)
        self.vstack.add_named(Gtk.ScrolledWindow(child=sv, hscrollbar_policy=Gtk.PolicyType.NEVER), 'struct')
        # --- SQL
        qv = vbox(8)
        self.editor = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=8, bottom_margin=8, accepts_tab=True)
        self.editor.add_css_class('mono')
        esw = Gtk.ScrolledWindow(child=self.editor, min_content_height=120, max_content_height=220)
        esw.add_css_class('codebox')
        key = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        key.connect('key-pressed', self.on_editor_key)
        self.editor.add_controller(key)
        qv.append(esw)
        self.hist_btn = Gtk.MenuButton(label='Lịch sử', icon_name='document-open-recent-symbolic', valign=Gtk.Align.CENTER)
        self.hist_pop = Gtk.Popover()
        self.hist_btn.set_popover(self.hist_pop)
        self.sql_status = label('', ['dim-label', 'caption'], hexpand=True, xalign=0, ellipsize=3)
        qv.append(toolbar(button('Chạy  (Ctrl+Enter)', icon='media-playback-start-symbolic', css=['suggested-action'], on=self.run_sql), self.hist_btn,
                          button('CSV', icon='document-save-symbolic', css=['flat'], on=self.export_csv, tip='Xuất kết quả ra CSV'), self.sql_status))
        self.sql_holder = vbox(0, vexpand=True)
        qv.append(self.sql_holder)
        self.vstack.add_named(qv, 'sql')
        root.append(self.vstack)
        # thanh hành động cho dòng đang chọn
        self.rbar = SelectionBar()
        self.rbar.add(button('Xem dòng', icon='view-reveal-symbolic', on=self.view_row), button('Copy JSON', icon='edit-copy-symbolic', on=self.copy_row))
        outer = vbox(0, vexpand=True)
        outer.append(root)
        outer.append(self.rbar)
        return outer

    def on_row_select(self, cols):
        def cb(d):
            if d is None:
                self.sel_row = None
                self.rbar.select(None)
                return
            self.sel_row = (cols, d['v'])
            self.rbar.select(f"Dòng {d['n'] + 1 + (self.page_no * PAGE_SIZES[self.size_dd.get_selected()] if self.vstack.get_visible_child_name() == 'data' else 0)}",
                             ' · '.join(f"{c}={'NULL' if v is None else str(v)[:30]}" for c, v in list(zip(cols, d['v']))[:4]))
        return cb

    def view_row(self):
        if self.sel_row:
            RowDialog(*self.sel_row).present(self.get_root())

    def copy_row(self):
        if self.sel_row:
            copy_text(self, json.dumps(dict(zip(*self.sel_row)), ensure_ascii=False, indent=2))
            self.toast('Đã sao chép dòng dạng JSON')

    # ---- Dữ liệu
    def page_size(self):
        return PAGE_SIZES[self.size_dd.get_selected()]

    def goto_page(self, d):
        self.page_no = max(0, self.page_no + d)
        self.load_data()

    def load_data(self):
        c, t, db, size, where, off = self.cur, self.tbl, self.db, self.page_size(), self.filter_entry.get_text(), self.page_no * self.page_size()
        if not t:
            return
        self.rbar.select(None)

        def done(res, err):
            if err:
                self.show_error(err)
                return
            self.show_error('')
            r, more = res
            self.more = more
            self.last = (r.cols, r.rows)
            self._clear(self.data_holder)
            if not r.cols:
                self.data_holder.append(Adw.StatusPage(icon_name='view-list-symbolic', title='Không có dòng nào', vexpand=True))
            else:
                self.data_holder.append(make_grid(r.cols, r.rows, self.on_row_select(r.cols)))
            n = len(r.rows)
            self.page_info.set_text(f'dòng {off + 1}–{off + n} · {r.ms} ms' if n else f'0 dòng · {r.ms} ms')
            self.prev_btn.set_sensitive(self.page_no > 0)
            self.next_btn.set_sensitive(more)
        self.run(lambda: D.table_page(c, db, t['schema'], t['name'], where=where, limit=size, offset=off), done, 'data')

    # ---- Cấu trúc
    def load_struct(self):
        c, t, db = self.cur, self.tbl, self.db

        def work():
            return D.table_columns(c, db, t['schema'], t['name']), D.table_indexes(c, db, t['schema'], t['name'])

        def done(res, err):
            if err:
                self.show_error(err)
                return
            cols, idx = res
            self._clear(self.struct_cols)
            self._clear(self.struct_idx)
            specs = [{'title': 'Cột', 'get': lambda d: f"<b>{esc(d['name'])}</b>", 'markup': True, 'sort': lambda d: d['name'].lower(), 'width': 220},
                     {'title': 'Kiểu', 'get': lambda d: d['type'], 'width': 180, 'css': ['mono']},
                     {'title': 'Null', 'get': lambda d: 'có' if d['nullable'] else 'không', 'width': 70},
                     {'title': 'Khoá', 'get': lambda d: d['key'], 'width': 70},
                     {'title': 'Mặc định', 'get': lambda d: '' if d['default'] is None else str(d['default']), 'expand': True, 'css': ['mono']}]
            tb = DataTable(specs, key=lambda d: d['name'])
            tb.set_size_request(-1, max(120, min(360, 36 * (len(cols) + 1))))
            tb.set_rows(cols)
            self.struct_cols.append(tb)
            if idx:
                it = DataTable([{'title': 'Index', 'get': lambda d: d[0], 'width': 260}, {'title': 'Định nghĩa', 'get': lambda d: d[1], 'expand': True, 'css': ['mono']}], key=lambda d: d[0])
                it.set_size_request(-1, max(90, min(260, 36 * (len(idx) + 1))))
                it.set_rows(idx)
                self.struct_idx.append(it)
            else:
                self.struct_idx.append(label('Không có index', ['dim-label'], xalign=0))
        self.run(work, done, 'struct')

    # ---- SQL tự do
    def on_editor_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.run_sql()
            return True
        return False

    def editor_text(self):
        buf = self.editor.get_buffer()
        if buf.get_has_selection():
            a, b = buf.get_selection_bounds()
            return buf.get_text(a, b, False)
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def run_sql(self):
        if not self.cur or self.cur['kind'] == 'redis':
            return
        try:
            stmt, cls = D.single_statement(self.editor_text(), self.cur['kind'], allow_write=not self.readonly)
        except D.DbError as e:
            self.show_error(e)
            return
        self.show_error('')
        if cls == 'write':
            confirm(self.window, 'Chạy câu lệnh GHI?', stmt[:600] + ('…' if len(stmt) > 600 else ''), 'Chạy thật', lambda: self._exec(stmt, cls))
        else:
            self._exec(stmt, cls)

    def _exec(self, stmt, cls):
        c, db, ro = self.cur, self.db, self.readonly
        self.sql_status.set_text('Đang chạy…')

        def done(res, err):
            if err:
                self.sql_status.set_text('Lỗi')
                self.show_error(err)
                return
            self.show_error('')
            self.history.appendleft(stmt)
            self.render_history()
            self._clear(self.sql_holder)
            if res.notice:
                self.sql_holder.append(Adw.StatusPage(icon_name='emblem-ok-symbolic', title=res.notice, vexpand=True))
                self.sql_status.set_text(f'{res.ms} ms')
                if cls == 'write' and self.tbl:
                    self.load_data()
                return
            self.last = (res.cols, res.rows)
            self.sel_row = None
            self.sql_holder.append(make_grid(res.cols, res.rows, self.on_row_select(res.cols)))
            self.sql_status.set_text(f'{len(res.rows)} dòng · {res.ms} ms' + (f' · đã cắt ở {D.MAX_ROWS} dòng' if res.truncated else ''))
        self.run(lambda: D.exec_sql(c, stmt, db=db, readonly=ro), done, 'sql')

    def render_history(self):
        box = vbox(0, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        for q in self.history:
            b = Gtk.Button(css_classes=['flat'])
            b.set_child(label(' '.join(q.split())[:90], xalign=0, ellipsize=3, max_width_chars=60))
            b.connect('clicked', lambda _b, q=q: (self.editor.get_buffer().set_text(q, -1), self.hist_pop.popdown()))
            box.append(b)
        self.hist_pop.set_child(Gtk.ScrolledWindow(child=box, min_content_height=40, max_content_height=300, min_content_width=420, propagate_natural_height=True))

    def export_csv(self):
        cols, rows = self.last
        if not cols:
            self.toast('Chưa có kết quả để xuất')
            return
        dlg = Gtk.FileDialog(title='Xuất CSV', initial_name='ket-qua.csv')

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            try:
                with open(path, 'w', encoding='utf-8', newline='') as f:
                    f.write(D.to_csv(cols, rows))
                self.toast(f'Đã xuất {len(rows)} dòng ra {path}')
            except OSError as e:
                self.toast(f'Lỗi: {e}')
        dlg.save(self.get_root(), None, done)

    # ================= Redis =================
    def _redis_view(self):
        root = content_box(8)
        root.set_margin_bottom(12)
        head = hbox(10)
        self.r_title = label('Redis', ['page-title'], hexpand=True, xalign=0)
        head.append(self.r_title)
        self.rtabs = Adw.ToggleGroup()
        for n, t in (('keys', 'Keys'), ('cmd', 'Lệnh')):
            self.rtabs.add(Adw.Toggle(name=n, label=t))
        self.rtabs.set_active_name('keys')
        self.rtabs.connect('notify::active-name', lambda g, _p: self.rstack2.set_visible_child_name(g.get_active_name()))
        head.append(self.rtabs)
        root.append(head)
        self.rstack2 = Gtk.Stack(vexpand=True)
        # --- keys
        kv = vbox(8)
        self.pattern = Gtk.Entry(text='*', placeholder_text='Mẫu key, vd. user:*', hexpand=True)
        self.pattern.connect('activate', lambda *_: self.scan_keys())
        self.r_info = label('', ['dim-label', 'caption'])
        kv.append(toolbar(self.pattern, button('Quét', icon='system-search-symbolic', css=['suggested-action'], on=self.scan_keys), self.r_info))
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, position=340, shrink_start_child=False, shrink_end_child=False)
        self.keys_table = DataTable([{'title': 'Key', 'get': lambda d: d['key'], 'sort': lambda d: d['key'], 'expand': True, 'css': ['mono']},
                                     {'title': 'Kiểu', 'get': lambda d: d['type'], 'sort': lambda d: d['type'], 'width': 80},
                                     {'title': 'TTL', 'get': lambda d: 'không hết hạn' if d['ttl'] == -1 else ('—' if d['ttl'] < 0 else self.fmt_ttl(d['ttl'])), 'sort': lambda d: d['ttl'], 'width': 110}],
                                    key=lambda d: d['key'], search_text=lambda d: d['key'].lower(), on_select=self.on_key_select)
        paned.set_start_child(self.keys_table)
        vp = vbox(6, margin_start=10)
        self.v_title = label('Chọn một key', ['heading'], xalign=0, ellipsize=3)
        self.v_note = label('', ['dim-label', 'caption'], xalign=0)
        vp.append(self.v_title)
        vp.append(self.v_note)
        self.v_holder = vbox(0, vexpand=True)
        vp.append(self.v_holder)
        paned.set_end_child(vp)
        kv.append(paned)
        self.rstack2.add_named(kv, 'keys')
        # --- lệnh
        cv = vbox(8)
        self.cmd_entry = Gtk.Entry(placeholder_text='Lệnh Redis, vd. GET user:1   (KEYS bị chặn — dùng SCAN)', hexpand=True)
        self.cmd_entry.connect('activate', lambda *_: self.run_cmd())
        cv.append(toolbar(self.cmd_entry, button('Chạy', icon='media-playback-start-symbolic', css=['suggested-action'], on=self.run_cmd)))
        self.cmd_out = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.cmd_out.add_css_class('mono')
        sw = Gtk.ScrolledWindow(child=self.cmd_out, vexpand=True)
        sw.add_css_class('codebox')
        cv.append(sw)
        self.rstack2.add_named(cv, 'cmd')
        root.append(self.rstack2)
        self.rdb = 0
        return root

    @staticmethod
    def fmt_ttl(ms):
        s = ms // 1000
        return f'{s // 86400}d {s % 86400 // 3600}h' if s >= 86400 else (f'{s // 3600}h {s % 3600 // 60}m' if s >= 3600 else (f'{s // 60}m {s % 60}s' if s >= 60 else f'{s}s'))

    def scan_keys(self):
        c, db, pat = self.cur, self.rdb, self.pattern.get_text().strip() or '*'
        self.r_title.set_text(f'{c["name"]} · db{db}')
        self.show_error('')

        def work():
            return D.redis_scan(c, db, pat), D.redis_dbsize(c, db)

        def done(res, err):
            if err:
                self.show_error(err)
                return
            (keys, cut), total = res
            self.keys_table.set_rows(keys)
            self.r_info.set_text(f'{len(keys)} key' + (f' (đã cắt ở 1000 / tổng {total})' if cut else f' / tổng {total}'))
            self.v_title.set_text('Chọn một key')
            self.v_note.set_text('')
            self._clear(self.v_holder)
        self.run(work, done, 'rkeys')

    def on_key_select(self, d):
        if not d:
            return
        c, db = self.cur, self.rdb
        self.v_title.set_text(d['key'])
        self.v_note.set_text(f"{d['type']} · TTL " + ('không hết hạn' if d['ttl'] == -1 else self.fmt_ttl(d['ttl'])))

        def done(v, err):
            self._clear(self.v_holder)
            if err:
                self.v_holder.append(label(str(err), ['error'], wrap=True, xalign=0))
                return
            self.v_note.set_text(self.v_note.get_text() + ' · ' + v['note'])
            if v['kind'] == 'text':
                tv = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
                tv.add_css_class('mono')
                tv.get_buffer().set_text(v['text'], -1)
                sw = Gtk.ScrolledWindow(child=tv, vexpand=True)
                sw.add_css_class('codebox')
                self.v_holder.append(sw)
                self.v_holder.append(button('Copy giá trị', icon='edit-copy-symbolic', css=['flat'], on=lambda: (copy_text(self, v['text']), self.toast('Đã sao chép'))))
            else:
                self.v_holder.append(make_grid(v['cols'], v['rows']))
        self.run(lambda: D.redis_value(c, d['key'], d['type'], db), done, 'rval')

    def run_cmd(self):
        line = self.cmd_entry.get_text().strip()
        if not line or not self.cur:
            return
        try:
            args = D.parse_redis_line(line)
            cls = D.redis_class(args)
            if cls == 'blocked':
                raise D.DbError(f'Lệnh {args[0].upper()} bị chặn (nguy hiểm hoặc chặn máy chủ). Dùng SCAN thay cho KEYS.')
            if cls == 'write' and self.readonly:
                raise D.DbError('Đang ở chế độ CHỈ ĐỌC: lệnh này có thể thay đổi dữ liệu. Bật "Cho phép ghi" ở thanh tiêu đề nếu cần.')
        except D.DbError as e:
            self.show_error(e)
            return
        self.show_error('')
        run = lambda: self._cmd(args, line)
        if cls == 'write':
            extra = '\nCẢNH BÁO: lệnh này xoá TOÀN BỘ dữ liệu.' if args[0].lower() in ('flushall', 'flushdb') else ''
            confirm(self.window, 'Chạy lệnh GHI?', line + extra, 'Chạy thật', run)
        else:
            run()

    def _cmd(self, args, line):
        c, db = self.cur, self.rdb

        def done(res, err):
            buf = self.cmd_out.get_buffer()
            text = f'> {line}\n' + (f'(lỗi) {err}' if err else D.resp_format(res[0])) + '\n\n'
            buf.insert(buf.get_end_iter(), text)
            GLib.idle_add(lambda: self.cmd_out.scroll_to_mark(buf.get_insert(), 0, False, 0, 0) or False)
            if not err:
                self.cmd_entry.set_text('')
        self.run(lambda: D.redis_command(c, args, db, allow_write=not self.readonly), done)

    def shutdown(self):
        self.pw_cache.clear()
