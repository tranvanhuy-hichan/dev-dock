"""Env Manager: mọi file .env trong các project — xem/sửa biến (che bí mật), so sánh giữa các file, phát hiện biến thiếu."""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import envfiles as E  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import DataTable, Page, bg, button, copy_text, hbox, label, vbox  # noqa: E402
from .sysmon import confirm  # noqa: E402

OK, WARN, BAD, DIM = '#26a269', '#e5a50a', '#e01b24', '#9a9996'
DIFF_TXT = {'same': ('Giống', DIM), 'different': ('Khác', WARN), 'only_a': ('Chỉ có ở file này', BAD), 'only_b': ('Thiếu ở file này', BAD)}


class VarDialog(Adw.Dialog):
    def __init__(self, page, path, key=None, value=''):
        super().__init__(title='Sửa biến' if key else 'Thêm biến', content_width=460)
        self.page, self.path, self.edit = page, path, key
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='Huỷ')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Lưu', css_classes=['suggested-action'])
        ok.connect('clicked', lambda *_: self.save())
        hdr.pack_start(cancel)
        hdr.pack_end(ok)
        tv.add_top_bar(hdr)
        grp = Adw.PreferencesGroup(margin_top=12, margin_bottom=8, margin_start=12, margin_end=12)
        self.key = Adw.EntryRow(title='Tên biến', text=key or '', sensitive=key is None)
        self.val = Adw.EntryRow(title='Giá trị', text=value)
        grp.add(self.key)
        grp.add(self.val)
        self.err = label('', ['error'], wrap=True, margin_start=12, margin_end=12, margin_bottom=8)
        box = vbox(0)
        box.append(grp)
        box.append(self.err)
        tv.set_content(box)
        self.set_child(tv)

    def save(self):
        try:
            E.set_var(self.path, self.key.get_text().strip(), self.val.get_text())
        except (ValueError, OSError) as e:
            self.err.set_text(str(e))
            return
        self.page.toast('Đã lưu')
        self.page.load_file(self.path)
        self.close()


class EnvPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.files, self.cur, self.data, self.busy, self.reveal = [], None, {}, False, False
        box = hbox(0)
        # ---- trái: danh sách file
        left = vbox(0, width_request=290)
        head = hbox(6, margin_top=10, margin_bottom=6, margin_start=10, margin_end=10)
        head.append(label('File .env', ['heading'], hexpand=True))
        head.append(button(icon='view-refresh-symbolic', css=['flat'], on=self.refresh, tip='Quét lại'))
        left.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.list.set_header_func(self._header)
        self.list.connect('row-selected', lambda _l, r: r and self.load_file(r.path))
        left.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        # ---- phải
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.msg = Adw.StatusPage(icon_name='dialog-password-symbolic', title='Đang tìm file .env…')
        self.stack.add_named(self.msg, 'msg')
        self.stack.add_named(self._main(), 'main')
        box.append(self.stack)
        self.set_child(box)
        self.every(120, lambda: self.refresh(True))

    def _header(self, row, before):
        d = os.path.dirname(row.path)
        if before is None or os.path.dirname(before.path) != d:
            row.set_header(label(self.rel_dir(d), ['dim-label', 'caption'], margin_top=10, margin_bottom=2, margin_start=12, ellipsize=1))
        else:
            row.set_header(None)

    def roots(self):
        return [os.path.realpath(r) for r in (self.window.store['project_roots'] or P.default_roots())]

    def rel_dir(self, d):
        for r in self.roots():
            if d == r or d.startswith(r + os.sep):
                return os.path.relpath(d, os.path.dirname(r)) if d != r else os.path.basename(r)
        return d

    def _main(self):
        box = vbox(10, margin_top=14, margin_bottom=14, margin_start=18, margin_end=18)
        top = hbox(10)
        self.title = label('', ['title-3'], hexpand=True, ellipsize=3)
        top.append(self.title)
        self.tg = Adw.ToggleGroup()
        self.tg.add(Adw.Toggle(name='vars', label='Biến'))
        self.tg.add(Adw.Toggle(name='cmp', label='So sánh'))
        self.tg.set_active_name('vars')
        self.tg.connect('notify::active-name', lambda g, _p: self.view.set_visible_child_name(g.get_active_name()))
        top.append(self.tg)
        box.append(top)
        self.warn = Adw.Banner(revealed=False)
        box.append(self.warn)
        self.view = Gtk.Stack(vexpand=True)
        self.view.add_named(self._vars(), 'vars')
        self.view.add_named(self._cmp(), 'cmp')
        box.append(self.view)
        return box

    # ---- tab Biến
    def _vars(self):
        box = vbox(8)
        bar = hbox(6)
        self.sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw.connect('notify::active', lambda s, _p: (setattr(self, 'reveal', s.get_active()), self.render()))
        bar.append(self.sw)
        bar.append(label('Hiện giá trị bí mật', hexpand=True))
        self.v_btns = {}
        for k, t, ic, fn, css in (('add', 'Thêm biến', 'list-add-symbolic', self.add_var, ['suggested-action']), ('edit', 'Sửa', 'document-edit-symbolic', self.edit_var, None),
                                  ('copy', 'Copy giá trị', 'edit-copy-symbolic', self.copy_val, None), ('del', 'Xoá', 'user-trash-symbolic', self.del_var, None)):
            b = button(t, icon=ic, css=css, on=fn)
            b.set_sensitive(k == 'add')
            self.v_btns[k] = b
            bar.append(b)
        box.append(bar)
        cols = [
            {'title': 'Biến', 'get': lambda d: d['key'], 'sort': lambda d: d['key'].lower(), 'width': 280, 'css': ['mono']},
            {'title': 'Giá trị', 'get': self.show_val, 'sort': lambda d: d['value'], 'expand': True, 'css': ['mono'], 'tip': lambda d: None},
            {'title': 'Ghi chú', 'get': lambda d: ' · '.join(x for x in (('bí mật' if d['secret'] else ''), ('trống' if d['value'] == '' else ''), ('khai báo lặp' if d['dup'] else '')) if x), 'width': 180},
        ]
        self.table = DataTable(cols, key=lambda d: d['key'], search_text=lambda d: d['key'].lower(), on_select=self.on_var_select)
        box.append(self.table)
        box.append(label('Giá trị bí mật được che theo mặc định. Chỉnh sửa ghi trực tiếp vào file, giữ nguyên comment và thứ tự.', ['dim-label', 'caption']))
        return box

    def show_val(self, d):
        if d['secret'] and not self.reveal:
            return E.mask(d['value'])
        return d['value'].replace('\n', '⏎')

    def on_var_select(self, d):
        for k in ('edit', 'copy', 'del'):
            self.v_btns[k].set_sensitive(d is not None)

    # ---- tab So sánh
    def _cmp(self):
        box = vbox(8)
        bar = hbox(8)
        bar.append(label('So với:'))
        self.other = Gtk.DropDown.new_from_strings([])
        self.other.set_hexpand(True)
        self.other.connect('notify::selected', lambda *_: self.render_cmp())
        bar.append(self.other)
        self.fill_btn = button('Thêm biến thiếu vào file này', icon='list-add-symbolic', css=['suggested-action'], on=self.fill_missing)
        bar.append(self.fill_btn)
        box.append(bar)
        self.cmp_info = label('', ['dim-label'], wrap=True)
        box.append(self.cmp_info)
        cols = [
            {'title': 'Biến', 'get': lambda d: d['key'], 'sort': lambda d: d['key'].lower(), 'width': 260, 'css': ['mono']},
            {'title': 'Trạng thái', 'get': lambda d: f"<span foreground='{DIFF_TXT[d['status']][1]}'>{DIFF_TXT[d['status']][0]}</span>", 'markup': True, 'sort': lambda d: d['status'], 'width': 170},
            {'title': 'File này', 'get': lambda d: self.cmp_val(d, 'a'), 'expand': True, 'css': ['mono']},
            {'title': 'File so sánh', 'get': lambda d: self.cmp_val(d, 'b'), 'expand': True, 'css': ['mono']},
        ]
        self.cmp_table = DataTable(cols, key=lambda d: d['key'])
        box.append(self.cmp_table)
        return box

    def cmp_val(self, d, side):
        v = d[side]
        if v is None:
            return '—'
        return E.mask(v) if (E.is_secret(d['key'], v) and not self.reveal) else v.replace('\n', '⏎')

    # ---- dữ liệu
    def refresh(self, quiet=False):
        if self.busy:
            return
        self.busy = True
        roots = self.roots()

        def done(files, err):
            self.busy = False
            if err:
                self.msg.set_title('Không quét được')
                self.msg.set_description(err)
                self.stack.set_visible_child_name('msg')
                return
            if files != self.files:
                self.files = files
                self.rebuild_list()
        if not self.files and not quiet:
            self.stack.set_visible_child_name('msg')
        bg(lambda: E.find_env_files(roots), done)

    def rebuild_list(self):
        keep = self.cur
        self.list.remove_all()
        for path in sorted(self.files, key=lambda p: (os.path.dirname(p), E.is_example(p) is False, os.path.basename(p))):
            row = Gtk.ListBoxRow()
            row.path = path
            line = hbox(6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            line.append(label(os.path.basename(path), ['mono'], hexpand=True, ellipsize=3))
            ex = E.example_for(path, self.files)
            if ex:
                try:
                    miss = E.missing_keys(path, ex)
                except OSError:
                    miss = []
                if miss:
                    line.append(label(f'thiếu {len(miss)}', ['badge', 'warn'], tooltip_text='Biến có trong ' + os.path.basename(ex) + ' nhưng thiếu ở file này: ' + ', '.join(miss)))
            elif E.is_example(path):
                line.append(label('mẫu', ['badge', 'info']))
            row.set_child(line)
            self.list.append(row)
        if not self.files:
            self.msg.set_icon_name('dialog-password-symbolic')
            self.msg.set_title('Không thấy file .env nào')
            self.msg.set_description('Đang quét: ' + ', '.join(self.roots()) + '\nĐổi thư mục gốc ở trang Projects.')
            self.stack.set_visible_child_name('msg')
            return
        self.stack.set_visible_child_name('main')
        target = keep if keep in self.files else self.files and next(iter(sorted(self.files)))
        for r in self._rows():
            if r.path == target:
                self.list.select_row(r)
                break

    def _rows(self):
        r = self.list.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):  # ListBox có header thì header cũng là con trực tiếp
                yield r
            r = r.get_next_sibling()

    def load_file(self, path):
        self.cur = path
        try:
            text = E.read(path)
        except OSError as e:
            self.toast(f'Không đọc được: {e}')
            return
        ents = E.parse(text)
        counts = {}
        for e in ents:
            counts[e['key']] = counts.get(e['key'], 0) + 1
        self.data = {}
        for e in ents:
            self.data[e['key']] = {'key': e['key'], 'value': e['value'], 'secret': E.is_secret(e['key'], e['value']), 'dup': counts[e['key']] > 1}
        self.title.set_text(f"{self.rel_dir(os.path.dirname(path))}/{os.path.basename(path)}")
        ex = E.example_for(path, self.files)
        miss = [k for k in E.load(ex) if k not in self.data] if ex else []
        self.warn.set_revealed(bool(miss))
        if miss:
            self.warn.set_title(f"Thiếu {len(miss)} biến so với {os.path.basename(ex)}: " + ', '.join(miss[:6]) + ('…' if len(miss) > 6 else ''))
        self.render()
        # danh sách file để so sánh: cùng thư mục trước
        others = sorted((f for f in self.files if f != path), key=lambda f: (os.path.dirname(f) != os.path.dirname(path), not E.is_example(f), f))
        self.others = others
        self.other.set_model(Gtk.StringList.new([f"{self.rel_dir(os.path.dirname(f))}/{os.path.basename(f)}" for f in others]) if others else Gtk.StringList.new([]))
        self.render_cmp()

    def render(self):
        self.table.set_rows(list(self.data.values()))

    def render_cmp(self):
        i = self.other.get_selected()
        others = getattr(self, 'others', [])
        if not self.cur or not others or i >= len(others):
            self.cmp_table.set_rows([])
            self.cmp_info.set_text('Cần ít nhất hai file .env để so sánh.')
            self.fill_btn.set_sensitive(False)
            return
        try:
            a, b = E.load(self.cur), E.load(others[i])
        except OSError as e:
            self.cmp_info.set_text(str(e))
            return
        rows = [{'key': k, 'status': s, 'a': va, 'b': vb} for k, s, va, vb in E.diff(a, b)]
        self.cmp_table.set_rows(rows)
        n = {s: sum(1 for r in rows if r['status'] == s) for s in DIFF_TXT}
        self.cmp_info.set_text(f"{n['different']} khác giá trị · {n['only_b']} thiếu ở file này · {n['only_a']} chỉ có ở file này · {n['same']} giống nhau")
        self.fill_btn.set_sensitive(n['only_b'] > 0)

    # ---- hành động
    def add_var(self):
        if self.cur:
            VarDialog(self, self.cur).present(self.get_root())

    def edit_var(self):
        d = self.table.selected()
        if d:
            VarDialog(self, self.cur, d['key'], d['value']).present(self.get_root())

    def copy_val(self):
        d = self.table.selected()
        if d:
            copy_text(self, d['value'])
            self.toast(f"Đã sao chép giá trị {d['key']}")

    def del_var(self):
        d = self.table.selected()
        if not d:
            return

        def go():
            try:
                E.delete_var(self.cur, d['key'])
                self.toast(f"Đã xoá {d['key']}")
                self.load_file(self.cur)
            except OSError as e:
                self.toast(str(e))
        confirm(self.window, f"Xoá biến {d['key']}?", f'Xoá khỏi {os.path.basename(self.cur)}. Không hoàn tác được.', 'Xoá', go)

    def fill_missing(self):
        i = self.other.get_selected()
        other = self.others[i]
        rows = [r for r in self.cmp_table.store]
        items = [(r.d['key'], '' if E.is_secret(r.d['key'], r.d['b'] or '') else (r.d['b'] or '')) for r in rows if r.d['status'] == 'only_b']

        def go():
            try:
                n = E.add_missing(self.cur, items)
                self.toast(f'Đã thêm {n} biến (giá trị bí mật để trống)')
                self.load_file(self.cur)
                self.rebuild_list()
            except (ValueError, OSError) as e:
                self.toast(str(e))
        confirm(self.window, f'Thêm {len(items)} biến thiếu?', 'Từ ' + os.path.basename(other) + ': ' + ', '.join(k for k, _ in items[:8]) + ('…' if len(items) > 8 else '') + '\nBiến bí mật được thêm với giá trị trống.', 'Thêm', go, destructive=False)
