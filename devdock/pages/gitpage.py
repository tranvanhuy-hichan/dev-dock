"""Git: thay đổi (stage/diff/commit), lịch sử, nhánh, stash cho từng repo + đẩy tất cả repo chưa push."""
import os

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import gitops as G  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, SelectionBar, bg, button, content_box, debounce, esc, hbox, icon_btn, label, linked, toolbar, ui, vbox)  # noqa: E402
from .sysmon import confirm  # noqa: E402

OK, WARN, BAD, DIM, INFO = '#26a269', '#e5a50a', '#e01b24', '#9a9996', '#3584e4'
AREA = {'staged': ('Đã stage', OK), 'unstaged': ('Chưa stage', WARN), 'untracked': ('Mới', INFO), 'conflict': ('Xung đột', BAD)}


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


LETTER = {'M': 'M', 'A': 'A', 'D': 'D', 'R': 'R', 'C': 'C', 'T': 'T', 'U': '!', '?': '+'}
LETTER_COLOR = {'M': WARN, 'A': OK, 'D': BAD, 'R': INFO, 'C': INFO, 'T': WARN, 'U': BAD, '?': INFO}


class ChangeList(Gtk.Box):
    """Danh sách thay đổi nhóm theo khu vực (đã stage / chưa stage / mới / xung đột) → thư mục → file.
    Mỗi nhóm gập/mở bằng cách bấm vào tiêu đề; mỗi file hiện tên in đậm, loại thay đổi và số dòng thêm/xoá."""

    ORDER = ('conflict', 'staged', 'unstaged', 'untracked')

    def __init__(self, on_select):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._cb, self._quiet = on_select, False
        self.collapsed, self.rows = set(), []
        self.lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.lb.set_filter_func(self._visible)
        self.lb.connect('row-selected', lambda _l, r: not self._quiet and self._cb and self._cb(self.selected()))
        self.lb.connect('row-activated', self._toggle)
        self.sw = Gtk.ScrolledWindow(child=self.lb, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.sw.add_css_class('codebox')
        self.append(self.sw)

    # ---- truy vấn
    def _file_rows(self):
        r = self.lb.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow) and r.kind == 'file':
                yield r
            r = r.get_next_sibling()

    def selected(self):
        r = self.lb.get_selected_row()
        return r.item if r is not None and r.kind == 'file' else None

    def count(self):
        return sum(1 for _ in self._file_rows())

    def lb_rows(self):
        """Mọi hàng (tiêu đề + file) theo thứ tự hiển thị."""
        out, r = [], self.lb.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):
                out.append(r)
            r = r.get_next_sibling()
        return out

    def kinds(self):
        out, r = [], self.lb.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):
                out.append(r.kind)
            r = r.get_next_sibling()
        return out

    def select_path(self, path, area=None):
        for r in self._file_rows():
            if r.item['path'] == path and (area is None or r.item['area'] == area):
                self.lb.select_row(r)
                return True
        return False

    def select_first(self):
        for r in self._file_rows():
            self.lb.select_row(r)
            return True
        return False

    # ---- gập/mở
    def _visible(self, row):
        if row.kind == 'area':
            return True
        if ('area', row.area) in self.collapsed:
            return False
        return row.kind == 'dir' or ('dir', row.area, row.dir) not in self.collapsed

    def _toggle(self, _lb, row):
        if row.kind not in ('area', 'dir'):
            return
        key = ('area', row.area) if row.kind == 'area' else ('dir', row.area, row.dir)
        self.collapsed.symmetric_difference_update({key})
        row.chev.set_from_icon_name('pan-end-symbolic' if key in self.collapsed else 'pan-down-symbolic')
        self.lb.invalidate_filter()

    # ---- dựng danh sách
    def _header(self, kind, area, d, title_markup, count, indent):
        row = Gtk.ListBoxRow(selectable=False)
        row.kind, row.area, row.dir = kind, area, d
        line = hbox(6, margin_start=indent, margin_end=8, margin_top=6 if kind == 'area' else 3, margin_bottom=3)
        key = ('area', area) if kind == 'area' else ('dir', area, d)
        row.chev = Gtk.Image(icon_name='pan-end-symbolic' if key in self.collapsed else 'pan-down-symbolic', css_classes=['dim-label'])
        line.append(row.chev)
        if kind == 'dir':
            line.append(Gtk.Image(icon_name='folder-symbolic', css_classes=['dim-label']))
        lb = Gtk.Label(use_markup=True, label=title_markup, xalign=0, hexpand=True, ellipsize=1)
        line.append(lb)
        line.append(label(str(count), ['dim-label', 'caption']))
        row.set_child(line)
        return row

    def _file(self, r):
        row = Gtk.ListBoxRow()
        row.kind, row.area, row.dir, row.item = 'file', r['area'], os.path.dirname(r['path']), r
        line = hbox(8, margin_start=36, margin_end=8, margin_top=4, margin_bottom=4)
        code = r['code']
        chip = Gtk.Label(use_markup=True, label=f'<span foreground="{LETTER_COLOR.get(code, DIM)}" weight="bold">{LETTER.get(code, code)}</span>', width_chars=2, tooltip_text=G.STATUS_TXT.get(code, code))
        chip.add_css_class('mono')
        line.append(chip)
        name = os.path.basename(r['path']) + (f"  ← {os.path.basename(r['orig'])}" if r.get('orig') else '')
        nl = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=3, css_classes=['heading'], tooltip_text=r['path'] + (f"  ←  {r['orig']}" if r.get('orig') else ''))
        line.append(nl)
        st = r.get('stat')
        if st:
            a, d = st
            txt = 'nhị phân' if a is None else f'<span foreground="{OK}">+{a}</span> <span foreground="{BAD}">−{d}</span>'
            line.append(Gtk.Label(use_markup=True, label=txt, css_classes=['mono', 'caption', 'tnum']))
        row.set_child(line)
        return row

    def set_rows(self, rows):
        """Dựng lại danh sách nhưng giữ file đang chọn, trạng thái gập và vị trí cuộn; on_select chỉ gọi một lần sau khi xong."""
        keep = self.selected()
        keep_key = keep['key'] if keep else None
        adj = self.sw.get_vadjustment()
        pos = adj.get_value()
        self._quiet = True
        target = None
        try:
            self.lb.remove_all()
            self.rows = rows
            for area in self.ORDER:
                items = [r for r in rows if r['area'] == area]
                if not items:
                    continue
                title, color = AREA[area]
                self.lb.append(self._header('area', area, '', f'<span foreground="{color}" weight="bold">{esc(title)}</span>', len(items), 8))
                dirs = {}
                for r in sorted(items, key=lambda r: r['path'].lower()):
                    dirs.setdefault(os.path.dirname(r['path']), []).append(r)
                for d in sorted(dirs, key=lambda x: (x != '', x.lower())):
                    self.lb.append(self._header('dir', area, d, f'<span weight="bold">{esc(d) if d else "(thư mục gốc)"}</span>', len(dirs[d]), 18))
                    for r in dirs[d]:
                        row = self._file(r)
                        self.lb.append(row)
                        if r['key'] == keep_key:
                            target = row
            if target is not None:
                self.lb.select_row(target)
        finally:
            self._quiet = False
        self.lb.invalidate_filter()
        now = self.selected()
        if self._cb and (now is not None or keep_key is not None):
            self._cb(now)
        GLib.idle_add(lambda: adj.set_value(min(pos, max(0, adj.get_upper() - adj.get_page_size()))) or False)


def make_diff_view():
    """TextView chỉ đọc để hiển thị diff có tô màu. Trả (khung_cuộn, textview)."""
    tv = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.NONE, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
    tv.add_css_class('mono')
    buf = tv.get_buffer()
    buf.create_tag('add', foreground=OK)
    buf.create_tag('del', foreground=BAD)
    buf.create_tag('hunk', foreground=INFO)
    buf.create_tag('meta', foreground=DIM)
    sw = Gtk.ScrolledWindow(child=tv, vexpand=True, hexpand=True)
    sw.add_css_class('codebox')
    return sw, tv


def fill_diff(tv, text, empty='(không có thay đổi để hiển thị)'):
    buf = tv.get_buffer()
    buf.set_text('', -1)
    if not text:
        buf.set_text(empty, -1)
        return
    lines = text.split('\n')
    for i, ln in enumerate(lines[:6000]):
        if ln.startswith(('+++', '---', 'diff ', 'index ', 'new file', 'deleted file', 'similarity', 'rename ', 'Binary ')):
            tag = 'meta'
        elif ln.startswith('@@'):
            tag = 'hunk'
        elif ln.startswith('+'):
            tag = 'add'
        elif ln.startswith('-'):
            tag = 'del'
        else:
            tag = None
        end = buf.get_end_iter()
        (buf.insert_with_tags_by_name(end, ln + '\n', tag) if tag else buf.insert(end, ln + '\n'))
    if len(lines) > 6000:
        buf.insert(buf.get_end_iter(), f'\n… còn {len(lines) - 6000} dòng nữa (đã cắt bớt)')


class NameDialog:
    """Hộp thoại nhập một dòng (Adw.AlertDialog)."""

    def __init__(self, page, heading, body, placeholder, on_ok, ok_label='Tạo'):
        d = Adw.AlertDialog(heading=heading, body=body)
        self.entry = Gtk.Entry(placeholder_text=placeholder, activates_default=True)
        d.set_extra_child(self.entry)
        d.add_response('no', 'Huỷ')
        d.add_response('yes', ok_label)
        d.set_response_appearance('yes', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('yes')
        d.set_close_response('no')
        d.connect('response', lambda _d, r: r == 'yes' and on_ok(self.entry.get_text().strip()))
        d.present(page.get_root())


class PushAllDialog(Adw.Dialog):
    """Liệt kê repo có commit chưa push, cho chọn và đẩy tuần tự."""

    def __init__(self, page):
        super().__init__(title='Push tất cả', content_width=700, content_height=560)
        self.page, self.rows, self.running = page, {}, False
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        close = Gtk.Button(label='Đóng')
        close.connect('clicked', lambda *_: self.close())
        self.go = Gtk.Button(label='Đẩy', css_classes=['suggested-action'], sensitive=False)
        self.go.connect('clicked', lambda *_: self.start())
        hdr.pack_start(close)
        hdr.pack_end(self.go)
        tv.add_top_bar(hdr)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name='emblem-synchronizing-symbolic', title='Đang kiểm tra các repo…'), 'wait')
        self.empty = Adw.StatusPage(icon_name='emblem-ok-symbolic', title='Không có repo nào cần push', description='Mọi repo có upstream đều đã đồng bộ.')
        self.stack.add_named(self.empty, 'empty')
        self.box = vbox(10, margin_top=10, margin_bottom=12, margin_start=12, margin_end=12)
        self.info = label('', ['dim-label'], xalign=0, wrap=True)
        self.box.append(self.info)
        self.grp = Adw.PreferencesGroup()
        self.box.append(Gtk.ScrolledWindow(child=self.grp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.stack.add_named(self.box, 'list')
        tv.set_content(self.stack)
        self.set_child(tv)
        roots = page.window.store['project_roots'] or P.default_roots()
        bg(lambda: [p for p in P.scan(roots) if p['git'] and p['ahead'] > 0], self.loaded)

    def loaded(self, repos, err):
        if err or not repos:
            self.empty.set_description(err or 'Mọi repo có upstream đều đã đồng bộ.')
            self.stack.set_visible_child_name('empty')
            return
        for p in repos:
            row = Adw.ActionRow(title=esc(p['name']), subtitle=esc(f"{p['branch']} · {p['ahead']} commit chưa push · {p['path']}"))
            chk = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
            chk.connect('toggled', lambda *_: self.count())
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            self.grp.add(row)
            self.rows[p['path']] = (row, chk, p)
        self.info.set_text('Đẩy tuần tự từng repo. DevDock không bao giờ force-push: repo bị từ chối sẽ được báo lỗi để bạn Pull trước.')
        self.count()
        self.stack.set_visible_child_name('list')

    def count(self):
        n = sum(1 for _r, c, _p in self.rows.values() if c.get_active())
        self.go.set_sensitive(n > 0 and not self.running)
        self.go.set_label(f'Đẩy {n} repo' if n else 'Đẩy')

    def start(self):
        paths = [path for path, (_r, c, _p) in self.rows.items() if c.get_active()]
        if not paths:
            return
        self.running = True
        self.go.set_sensitive(False)
        for _r, c, _p in self.rows.values():
            c.set_sensitive(False)
        for path in paths:
            self.rows[path][0].set_subtitle('Đang chờ…')
        bg(lambda: G.push_all(paths, on_result=lambda p, ok, m: ui(self.mark, p, ok, m)), self.finished)

    def mark(self, path, ok, msg):
        row = self.rows[path][0]
        row.set_subtitle(esc(('✓ ' if ok else '✗ ') + msg))

    def finished(self, res, err):
        self.running = False
        okn = sum(1 for _p, ok, _m in (res or []) if ok)
        self.info.set_text(err or f'Xong: {okn}/{len(res or [])} repo đã push.')
        self.page.refresh_repos(True)
        self.page.refresh_status()


class GitPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.repos, self.repo, self.st, self.tab = [], None, None, 'changes'
        self.busy, self.op_busy, self.commits, self.diff_key = set(), False, [], None
        # ---- thanh tiêu đề: tab ở giữa, làm mới + Push tất cả ở bên phải
        self.tg = Adw.ToggleGroup()
        for n, t in (('changes', 'Thay đổi'), ('history', 'Lịch sử'), ('branches', 'Nhánh'), ('stash', 'Stash')):
            self.tg.add(Adw.Toggle(name=n, label=t))
        self.tg.set_active_name('changes')
        self.tg.connect('notify::active-name', lambda g, _p: self.on_tab(g.get_active_name()))
        self.hdr_title = self.tg
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh_all(True)), button('Push tất cả…', icon='go-up-symbolic', on=lambda: PushAllDialog(self).present(self.get_root()))]

        box = hbox(0)
        # ---- trái: danh sách repo
        left = vbox(6, width_request=280)
        self.search = Gtk.SearchEntry(placeholder_text='Tìm repo…', margin_top=12, margin_start=10, margin_end=10)
        self.search.connect('search-changed', lambda *_: self.list.invalidate_filter())
        left.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.list.set_filter_func(lambda row: not self.search.get_text().strip() or self.search.get_text().strip().lower() in row.repo['name'].lower())
        self.list.connect('row-selected', lambda _l, r: r and self.select_repo(r.repo))
        left.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        # ---- phải
        self.right = Gtk.Stack(hexpand=True, vexpand=True)
        self.empty = Adw.StatusPage(icon_name='media-playlist-repeat-symbolic', title='Đang quét repo…', description='')
        self.right.add_named(self.empty, 'empty')
        self.right.add_named(self._main(), 'main')
        box.append(self.right)
        self.set_child(box)
        self.every(30, lambda: self.refresh_repos(True)).every(5, self.tick)
        self.commit_tv.set_size_request(-1, 64)

    # ================= khung chính =================
    def _main(self):
        root = content_box(10)
        root.set_margin_bottom(0)
        head = hbox(12)
        names = vbox(0, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = label('', ['page-title'], ellipsize=3)
        self.sub = label('', ['dim-label', 'caption'], ellipsize=1)
        names.append(self.title)
        names.append(self.sub)
        head.append(names)
        self.b_fetch = button('Fetch', icon='emblem-synchronizing-symbolic', on=lambda: self.op(G.fetch, 'Đã fetch'))
        self.b_pull = button('Pull', icon='go-down-symbolic', on=lambda: self.op(G.pull))
        self.b_push = button('Push', icon='go-up-symbolic', css=['suggested-action'], on=lambda: self.op(G.push))
        self.op_widgets = [self.b_fetch, self.b_pull, self.b_push]
        head.append(linked(self.b_fetch, self.b_pull, self.b_push))
        root.append(head)
        self.strip = InfoStrip('error')
        root.append(self.strip)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self._changes(), 'changes')
        self.stack.add_named(self._history(), 'history')
        self.stack.add_named(self._branches(), 'branches')
        self.stack.add_named(self._stash(), 'stash')
        root.append(self.stack)
        return root

    # ================= dữ liệu repo =================
    def roots(self):
        return self.window.store['project_roots'] or P.default_roots()

    def once(self, key, fn, done):
        if key in self.busy:
            return
        self.busy.add(key)
        bg(fn, lambda r, e: (self.busy.discard(key), done(r, e)))

    def refresh_all(self, force=False):
        self.refresh_repos(True)
        self.refresh_tab(force)

    def refresh_repos(self, quiet=False):
        roots = self.roots()

        def done(res, err):
            if err:
                self.empty.set_title('Không quét được')
                self.empty.set_description(err)
                self.right.set_visible_child_name('empty')
                return
            self.repos = [p for p in res if p['git']]
            self.rebuild_list()
        self.once('repos', lambda: P.scan(roots), done)

    def rebuild_list(self):
        keep = self.repo['path'] if self.repo else None
        self.list.remove_all()
        for p in self.repos:
            row = Gtk.ListBoxRow()
            row.repo = p
            line = hbox(6, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            col = vbox(0, hexpand=True)
            col.append(label(p['name'], ['heading'], ellipsize=3))
            col.append(label(p['branch'] or '—', ['dim-label', 'caption'], ellipsize=3))
            line.append(col)
            bits = []
            if p['dirty'] or p['untracked']:
                bits.append(span(WARN, f"●{p['dirty'] + p['untracked']}"))
            if p['ahead']:
                bits.append(span(BAD, f"↑{p['ahead']}"))
            if p['behind']:
                bits.append(span(WARN, f"↓{p['behind']}"))
            if bits:
                lb = Gtk.Label(use_markup=True, valign=Gtk.Align.CENTER)
                lb.set_markup(' '.join(bits))
                line.append(lb)
            row.set_child(line)
            self.list.append(row)
        if not self.repos:
            self.empty.set_title('Không tìm thấy repo git')
            self.empty.set_description('Đang quét: ' + ', '.join(self.roots()) + '\nĐổi thư mục gốc ở trang Projects.')
            self.right.set_visible_child_name('empty')
            return
        target = next((r for r in self._rows() if r.repo['path'] == keep), None) or next(iter(self._rows()), None)
        if target is not None:
            self.list.select_row(target)  # giữ repo đang chọn (select_repo là idempotent) hoặc chọn repo đầu tiên

    def _rows(self):
        r = self.list.get_first_child()
        while r:
            if isinstance(r, Gtk.ListBoxRow):
                yield r
            r = r.get_next_sibling()

    def select_repo(self, p):
        if self.repo and self.repo['path'] == p['path']:
            self.repo = p
            return
        self.repo, self.st = p, None
        self.strip.hide()
        self.commit_buf.set_text('', -1)
        self.right.set_visible_child_name('main')
        self.title.set_text(p['name'])
        self.sub.set_text(self.rel(p['path']))
        self.files.set_rows([])
        fill_diff(self.diff_tv, '')
        fill_diff(self.hist_diff_tv, '')
        fill_diff(self.stash_diff_tv, '')
        self.refresh_status()
        self.refresh_tab(True)

    @staticmethod
    def rel(path):
        h = os.path.expanduser('~')
        return '~' + path[len(h):] if path.startswith(h) else path

    def on_tab(self, name):
        self.tab = name
        self.stack.set_visible_child_name(name)
        self.refresh_tab(True)

    def tick(self):
        if self.repo and self.tab == 'changes' and not self.op_busy:
            self.refresh_status()

    def refresh_tab(self, force=False):
        if not self.repo:
            return
        {'changes': self.refresh_status, 'history': self.load_history, 'branches': self.load_branches, 'stash': self.load_stash}[self.tab]()

    def refresh_status(self):
        if not self.repo:
            return
        path = self.repo['path']

        def done(st, err):
            if not self.repo or self.repo['path'] != path:
                return
            if err:
                self.strip.show(err)
                return
            self.st = st
            self.render_status()
        self.once('status', lambda: G.status(path), done)

    def render_status(self):
        st = self.st
        up = st['upstream']
        self.sub.set_text(f"{self.rel(self.repo['path'])}  ·  nhánh {st['branch'] or '—'}" + (f"  →  {up}" if up else '  (chưa có upstream)'))
        self.b_pull.set_label(f"Pull ↓{st['behind']}" if st['behind'] else 'Pull')
        self.b_push.set_label(f"Push ↑{st['ahead']}" if st['ahead'] else 'Push')
        stats = st.get('stats') or {}
        rows = [{'key': (f['area'], f['path']), 'stat': stats.get((f['area'], f['path'])), **f} for f in st['files']]
        self.files.set_rows(rows)
        if rows and self.files.selected() is None:
            self.files.select_first()  # luôn có một file được chọn để xem diff
        n_staged = sum(1 for r in rows if r['area'] == 'staged')
        self.counts.set_text(f"{n_staged} đã stage · {sum(1 for r in rows if r['area'] in ('unstaged', 'conflict'))} chưa stage · {sum(1 for r in rows if r['area'] == 'untracked')} mới")
        self.update_commit_buttons()

    # ================= thao tác chung =================
    def set_busy(self, on):
        self.op_busy = on
        for w in self.op_widgets:
            w.set_sensitive(not on)
        if not on:
            self.update_commit_buttons()

    def op(self, fn, ok_msg=None, after=None):
        if self.op_busy or not self.repo:
            return
        path = self.repo['path']
        self.set_busy(True)

        def done(res, err):
            self.set_busy(False)
            if err:
                self.strip.show(err)
                self.toast(err)
            else:
                self.strip.hide()
                self.toast(ok_msg or (res if isinstance(res, str) and res else 'Xong'))
            self.refresh_repos(True)
            self.refresh_status()
            self.refresh_tab(True)
            if after and not err:
                after()
        bg(lambda: fn(path), done)

    # ================= Tab: Thay đổi =================
    def _changes(self):
        box = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True, resize_start_child=False, resize_end_child=True, shrink_start_child=False, shrink_end_child=False, position=330)
        # --- trái: danh sách file
        left = vbox(8, width_request=300, margin_end=8)
        self.counts = label('', ['dim-label', 'caption'], xalign=0, hexpand=True)
        self.b_stage_all = button('Stage tất cả', icon='list-add-symbolic', on=self.stage_all)
        self.b_unstage_all = button('Bỏ stage tất cả', on=self.unstage_all)
        left.append(toolbar(self.counts, self.b_stage_all, self.b_unstage_all))
        self.files = ChangeList(self.on_file)
        left.append(self.files)
        self.fbar = SelectionBar()
        self.b_stage = button('Stage', icon='list-add-symbolic', on=self.stage_sel)
        self.b_unstage = button('Bỏ stage', on=self.unstage_sel)
        self.b_discard = button('Hoàn tác…', icon='edit-undo-symbolic', css=['destructive-action'], on=self.discard_sel)
        self.fbar.add(linked(self.b_stage, self.b_unstage), self.b_discard)
        left.append(self.fbar)
        box.set_start_child(left)
        # --- phải: diff + ô commit
        right = vbox(8, hexpand=True, margin_start=4)
        sw, self.diff_tv = make_diff_view()
        right.append(sw)
        self.commit_tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.commit_buf = self.commit_tv.get_buffer()
        self.commit_buf.connect('changed', lambda *_: self.update_commit_buttons())
        csw = Gtk.ScrolledWindow(child=self.commit_tv, min_content_height=64, max_content_height=130, propagate_natural_height=True)
        csw.add_css_class('card')
        right.append(label('Nội dung commit (dòng đầu là tiêu đề)', ['dim-label', 'caption'], xalign=0))
        right.append(csw)
        self.b_commit = button('Commit', icon='emblem-ok-symbolic', css=['suggested-action'], on=lambda: self.commit(False))
        self.b_commit_push = button('Commit & Push', icon='go-up-symbolic', on=lambda: self.commit(True))
        crow = hbox(8, halign=Gtk.Align.END, margin_bottom=10)
        crow.append(self.b_commit)
        crow.append(self.b_commit_push)
        right.append(crow)
        self.op_widgets += [self.b_stage_all, self.b_unstage_all]
        box.set_end_child(right)
        return box

    def update_commit_buttons(self):
        staged = bool(self.st and any(f['area'] == 'staged' for f in self.st['files']))
        msg = self.commit_buf.get_text(self.commit_buf.get_start_iter(), self.commit_buf.get_end_iter(), False).strip()
        ok = staged and bool(msg) and not self.op_busy
        self.b_commit.set_sensitive(ok)
        self.b_commit_push.set_sensitive(ok)
        self.b_stage_all.set_sensitive(not self.op_busy and bool(self.st and any(f['area'] != 'staged' for f in self.st['files'])))
        self.b_unstage_all.set_sensitive(not self.op_busy and staged)

    def on_file(self, d):
        self.fbar.select(d['path'] if d else None, (AREA[d['area']][0] + ' · ' + G.STATUS_TXT.get(d['code'], '')) if d else '')
        if d:
            self.b_stage.set_visible(d['area'] != 'staged')
            self.b_unstage.set_visible(d['area'] == 'staged')
            self.b_discard.set_visible(d['area'] in ('unstaged', 'untracked'))
        key = (d['key'], self.repo['path']) if d and self.repo else None
        if key == self.diff_key:
            return  # làm mới bảng nhưng vẫn là dòng đó: không tải lại diff
        self.diff_key = key
        if not d:
            fill_diff(self.diff_tv, '', 'Chọn một file ở bên trái để xem thay đổi.')
            return
        path = self.repo['path']

        def done(text, err):
            if self.diff_key == key:
                fill_diff(self.diff_tv, err or text)
        bg(lambda: G.file_diff(path, d), done)

    def stage_sel(self):
        d = self.files.selected()
        if d:
            self.op(lambda p: G.stage(p, [d['path']]), 'Đã stage')

    def unstage_sel(self):
        d = self.files.selected()
        if d:
            self.op(lambda p: G.unstage(p, [d['path']]), 'Đã bỏ stage')

    def stage_all(self):
        paths = sorted({f['path'] for f in self.st['files'] if f['area'] != 'staged'}) if self.st else []
        if paths:
            self.op(lambda p: G.stage(p, paths), f'Đã stage {len(paths)} file')

    def unstage_all(self):
        paths = sorted({f['path'] for f in self.st['files'] if f['area'] == 'staged'}) if self.st else []
        if paths:
            self.op(lambda p: G.unstage(p, paths), f'Đã bỏ stage {len(paths)} file')

    def discard_sel(self):
        d = self.files.selected()
        if not d or d['area'] not in ('unstaged', 'untracked'):
            return
        what = 'XOÁ file mới này khỏi ổ đĩa' if d['area'] == 'untracked' else 'bỏ mọi thay đổi chưa stage của file này'
        confirm(self.window, f"Hoàn tác {d['path']}?", f'Sẽ {what}. Không khôi phục được.', 'Hoàn tác', lambda: self.op(lambda p: G.discard(p, d), 'Đã hoàn tác'))

    def commit(self, push):
        msg = self.commit_buf.get_text(self.commit_buf.get_start_iter(), self.commit_buf.get_end_iter(), False)

        def work(p):
            r = G.commit(p, msg)
            return G.push(p) if push else r
        self.op(work, 'Đã commit & push' if push else 'Đã commit', after=lambda: self.commit_buf.set_text('', -1))

    # ================= Tab: Lịch sử =================
    def _history(self):
        box = vbox(8)
        self.hist_search = Gtk.SearchEntry(placeholder_text='Tìm trong nội dung, tác giả, mã commit…', hexpand=True)
        self.hist_search.connect('search-changed', debounce(self.load_history, 300))
        self.hist_all = Gtk.CheckButton(label='Mọi nhánh')
        self.hist_all.connect('toggled', lambda *_: self.load_history())
        box.append(toolbar(self.hist_search, self.hist_all))
        cols = [
            {'title': 'Commit', 'get': lambda d: d['short'], 'width': 80, 'css': ['mono']},
            {'title': 'Nội dung', 'get': lambda d: d['subject'], 'expand': True, 'tip': lambda d: d['subject']},
            {'title': 'Refs', 'get': lambda d: ' '.join(f"<span foreground='{INFO}'>{esc(x.strip())}</span>" for x in d['refs'].split(',') if x.strip())[:200], 'markup': True, 'width': 170},
            {'title': 'Tác giả', 'get': lambda d: d['author'], 'sort': lambda d: d['author'].lower(), 'width': 130},
            {'title': 'Khi nào', 'get': lambda d: P.ago(d['ts']), 'sort': lambda d: d['ts'], 'width': 110},
        ]
        self.hist = DataTable(cols, key=lambda d: d['sha'], on_select=self.on_commit)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_start_child=True, resize_end_child=True, shrink_start_child=False, shrink_end_child=False, position=240)
        paned.set_start_child(self.hist)
        sw, self.hist_diff_tv = make_diff_view()
        paned.set_end_child(sw)
        box.append(paned)
        return box

    def load_history(self):
        if not self.repo or self.tab != 'history':
            return
        path, q, allr = self.repo['path'], self.hist_search.get_text(), self.hist_all.get_active()

        def done(rows, err):
            if not self.repo or self.repo['path'] != path:
                return
            if err:
                self.strip.show(err)
                return
            self.commits = rows
            self.hist.set_rows(rows)
            if rows and self.hist.selected() is None:
                self.hist.sel.set_selected(0)  # luôn hiển thị chi tiết commit mới nhất
        self.once('history', lambda: G.log(path, 300, q, allr), done)

    def on_commit(self, d):
        if not d:
            fill_diff(self.hist_diff_tv, '', 'Chọn một commit để xem chi tiết.')
            return
        path, sha = self.repo['path'], d['sha']
        self.cur_sha = sha

        def done(text, err):
            if getattr(self, 'cur_sha', None) == sha:
                fill_diff(self.hist_diff_tv, err or text)
        bg(lambda: G.show(path, sha), done)

    # ================= Tab: Nhánh =================
    def _branches(self):
        box = vbox(8)
        box.append(toolbar(Gtk.Box(hexpand=True), button('Nhánh mới…', icon='list-add-symbolic', css=['suggested-action'], on=self.new_branch)))
        cols = [
            {'title': 'Nhánh', 'get': lambda d: ('<b>● ' if d['current'] else '') + esc(d['name']) + ('</b>' if d['current'] else '') + (f"  <span foreground='{DIM}'>(đã merge)</span>" if d['merged'] and not d['current'] else ''), 'markup': True, 'sort': lambda d: d['name'].lower(), 'width': 230},
            {'title': 'Loại', 'get': lambda d: 'remote' if d['remote'] else 'local', 'width': 80},
            {'title': 'Upstream', 'get': lambda d: (d['upstream'] + ('  (đã mất)' if d['gone'] else '')) if d['upstream'] else '—', 'width': 150},
            {'title': '↑↓', 'get': lambda d: (f"↑{d['ahead']}" if d['ahead'] else '') + (' ' if d['ahead'] and d['behind'] else '') + (f"↓{d['behind']}" if d['behind'] else '') or '—', 'width': 70},
            {'title': 'Cập nhật', 'get': lambda d: P.ago(d['ts']), 'sort': lambda d: d['ts'], 'width': 100},
            {'title': 'Commit cuối', 'get': lambda d: d['subject'], 'expand': True},
        ]
        self.br = DataTable(cols, key=lambda d: d['ref'], search_text=lambda d: d['name'], on_select=self.on_branch)
        box.append(self.br)
        self.bbar = SelectionBar()
        self.b_checkout = button('Checkout', icon='go-jump-symbolic', css=['suggested-action'], on=self.checkout_sel)
        self.b_delbr = button('Xoá (đã merge)', icon='user-trash-symbolic', css=['destructive-action'], on=self.delete_sel)
        self.bbar.add(self.b_checkout, self.b_delbr)
        box.append(self.bbar)
        return box

    def load_branches(self):
        if not self.repo or self.tab != 'branches':
            return
        path = self.repo['path']

        def done(rows, err):
            if not self.repo or self.repo['path'] != path:
                return
            if err:
                self.strip.show(err)
                return
            self.branches = rows
            self.br.set_rows(rows)
        self.once('branches', lambda: G.branches(path), done)

    def on_branch(self, d):
        self.bbar.select(d['name'] if d else None, ('nhánh hiện tại' if d['current'] else ('remote' if d['remote'] else 'local')) if d else '')
        if d:
            self.b_checkout.set_sensitive(not d['current'])
            self.b_delbr.set_sensitive(d['merged'] and not d['current'] and not d['remote'])
            self.b_delbr.set_tooltip_text(None if d['merged'] else 'Chỉ xoá được nhánh local đã merge vào nhánh hiện tại')

    def checkout_sel(self):
        d = self.br.selected()
        if not d or d['current']:
            return
        local_names = {b['name'] for b in getattr(self, 'branches', []) if not b['remote']}
        if d['remote']:
            short = d['name'].split('/', 1)[1] if '/' in d['name'] else d['name']
            if short in local_names:
                self.op(lambda p: G.checkout(p, short), f'Đã chuyển sang {short}')
            else:
                self.op(lambda p: G.checkout(p, d['name'], remote=True), f'Đã tạo nhánh theo dõi {d["name"]}')
        else:
            self.op(lambda p: G.checkout(p, d['name']), f"Đã chuyển sang {d['name']}")

    def delete_sel(self):
        d = self.br.selected()
        if d and d['merged'] and not d['current'] and not d['remote']:
            confirm(self.window, f"Xoá nhánh {d['name']}?", 'Nhánh đã merge nên không mất commit nào. Chỉ xoá nhánh local.', 'Xoá', lambda: self.op(lambda p: G.delete_branch(p, d['name']), f"Đã xoá {d['name']}"))

    def new_branch(self):
        if not self.repo:
            return

        def ok(name):
            if not G.valid_ref(name):
                self.toast('Tên nhánh không hợp lệ (chữ, số, _ . - /, không khoảng trắng)')
                return
            self.op(lambda p: G.checkout(p, name, create=True), f'Đã tạo và chuyển sang {name}')
        NameDialog(self, 'Nhánh mới', f"Tạo từ {self.st['branch'] if self.st else 'HEAD'} và chuyển sang nhánh đó.", 'ten-nhanh', ok)

    # ================= Tab: Stash =================
    def _stash(self):
        box = vbox(8)
        self.stash_msg = Gtk.Entry(placeholder_text='Ghi chú cho stash (tuỳ chọn)', hexpand=True)
        self.stash_untr = Gtk.CheckButton(label='Gồm file mới', active=True)
        self.b_stash = button('Stash thay đổi', icon='document-save-symbolic', css=['suggested-action'], on=self.stash_push)
        self.op_widgets.append(self.b_stash)
        box.append(toolbar(self.stash_msg, self.stash_untr, self.b_stash))
        cols = [
            {'title': 'Stash', 'get': lambda d: d['ref'], 'width': 110, 'css': ['mono']},
            {'title': 'Nội dung', 'get': lambda d: d['message'], 'expand': True},
            {'title': 'Khi nào', 'get': lambda d: P.ago(d['ts']), 'sort': lambda d: d['ts'], 'width': 120},
        ]
        self.stash = DataTable(cols, key=lambda d: d['ref'], on_select=self.on_stash)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_start_child=True, resize_end_child=True, shrink_start_child=False, shrink_end_child=False, position=200)
        paned.set_start_child(self.stash)
        sw, self.stash_diff_tv = make_diff_view()
        paned.set_end_child(sw)
        box.append(paned)
        self.sbar = SelectionBar()
        self.sbar.add(linked(button('Apply', on=lambda: self.stash_do('apply')), button('Pop', on=lambda: self.stash_do('pop'))),
                      button('Drop…', icon='user-trash-symbolic', css=['destructive-action'], on=self.stash_drop))
        box.append(self.sbar)
        return box

    def load_stash(self):
        if not self.repo or self.tab != 'stash':
            return
        path = self.repo['path']

        def done(rows, err):
            if not self.repo or self.repo['path'] != path:
                return
            if err:
                self.strip.show(err)
                return
            self.stash.set_rows(rows)
            if not rows:
                fill_diff(self.stash_diff_tv, '', 'Chưa có stash nào.')
        self.once('stash', lambda: G.stash_list(path), done)

    def on_stash(self, d):
        self.sbar.select(d['ref'] if d else None, d['message'] if d else '')
        if not d:
            fill_diff(self.stash_diff_tv, '')
            return
        path, ref = self.repo['path'], d['ref']
        self.cur_stash = ref

        def done(text, err):
            if getattr(self, 'cur_stash', None) == ref:
                fill_diff(self.stash_diff_tv, err or text)
        bg(lambda: G.stash_show(path, ref), done)

    def stash_push(self):
        msg, untr = self.stash_msg.get_text(), self.stash_untr.get_active()
        self.op(lambda p: G.stash_push(p, msg, untr), 'Đã stash', after=lambda: self.stash_msg.set_text(''))

    def stash_do(self, what):
        d = self.stash.selected()
        if d:
            fn = {'apply': G.stash_apply, 'pop': G.stash_pop}[what]
            self.op(lambda p: fn(p, d['ref']), 'Đã áp dụng stash' if what == 'apply' else 'Đã pop stash')

    def stash_drop(self):
        d = self.stash.selected()
        if d:
            confirm(self.window, f"Xoá {d['ref']}?", f"“{d['message']}” sẽ bị xoá vĩnh viễn.", 'Drop', lambda: self.op(lambda p: G.stash_drop(p, d['ref']), 'Đã xoá stash'))

    # ================= khác =================
    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        if name in ('changes', 'history', 'branches', 'stash'):
            self.tg.set_active_name(name)
        elif name == 'select':
            self.files.select_first()
        elif name == 'push_all':
            PushAllDialog(self).present(self.get_root())
        else:  # tên repo: chọn repo đó
            for r in self._rows():
                if r.repo['name'] == name:
                    self.list.select_row(r)

    def shutdown(self):
        pass
