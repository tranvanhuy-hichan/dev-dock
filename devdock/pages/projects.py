"""Projects Hub: tổng quan mọi repo/project (git, nhánh, thay đổi chưa commit, chưa push, dung lượng build) và thao tác nhanh."""
import os
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import projects as P  # noqa: E402
from ..ui import (DataTable, Page, SelectionBar, bg, button, content_box, copy_text, esc, hbox, human, icon_btn, label, linked, toolbar, ui, vbox)  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

OK, WARN, BAD, DIM = '#26a269', '#e5a50a', '#e01b24', '#9a9996'
FILTERS = ['Tất cả', 'Có thay đổi chưa commit', 'Có commit chưa push', 'Cần pull (đi sau remote)', 'Không dùng git', 'Lâu không đụng (>30 ngày)']


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


def status_markup(p):
    if not p['git']:
        return span(DIM, 'Không dùng git')
    parts = []
    if p['dirty'] or p['untracked']:
        n = p['dirty'] + p['untracked']
        parts.append(span(WARN, f"● {n} thay đổi"))
    if p['ahead']:
        parts.append(span(BAD, f"↑{p['ahead']} chưa push"))
    if p['behind']:
        parts.append(span(WARN, f"↓{p['behind']} cần pull"))
    if not parts:
        parts.append(span(OK, '✓ sạch' if p['upstream'] or not p['remote'] else '✓ sạch (chưa có upstream)'))
    return '  '.join(parts)


def matches(p, flt):
    import time
    return [True, bool(p['dirty'] or p['untracked']), p['ahead'] > 0, p['behind'] > 0, not p['git'], time.time() - p['last_ts'] > 30 * 86400][flt]


class ProjectsPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.items, self.gen, self.busy = [], 0, False
        self.flt = 0
        # --- thanh tiêu đề: làm mới + đổi thư mục gốc
        self.roots_btn = icon_btn('folder-open-symbolic', 'Đổi thư mục gốc để quét', self.pick_root)
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Quét lại', self.refresh), self.roots_btn]

        root = content_box()
        root.set_margin_bottom(12)
        stats = hbox(12, homogeneous=True)
        self.s_total = card('Project', '—', '')
        self.s_dirty = card('Chưa commit', '—', '')
        self.s_ahead = card('Chưa push / cần pull', '—', '')
        self.s_build = card('Thư mục build', '—', 'đang đo…')
        for c in (self.s_total, self.s_dirty, self.s_ahead, self.s_build):
            stats.append(c[0])
        root.append(stats)

        self.search = Gtk.SearchEntry(placeholder_text='Tìm project, nhánh, commit…', hexpand=True)
        self.search.connect('search-changed', lambda e: self.table.set_query(e.get_text()))
        self.dd = Gtk.DropDown.new_from_strings(FILTERS)
        self.dd.connect('notify::selected', lambda d, _p: (setattr(self, 'flt', d.get_selected()), self.render()))
        root.append(toolbar(self.search, self.dd))

        cols = [
            {'title': 'Project', 'get': lambda d: f"<b>{esc(d['name'])}</b>" + (f"  <span foreground='{DIM}'>{esc(d['lang'])}</span>" if d['lang'] else ''), 'markup': True,
             'sort': lambda d: d['name'].lower(), 'width': 260, 'tip': lambda d: d['path']},
            {'title': 'Nhánh', 'get': lambda d: d['branch'] or '—', 'sort': lambda d: d['branch'], 'width': 150},
            {'title': 'Trạng thái', 'get': status_markup, 'markup': True, 'sort': lambda d: (d['dirty'] + d['untracked'], d['ahead']), 'width': 260},
            {'title': 'Commit cuối', 'get': lambda d: (P.ago(d['last_ts']) + (f"  ·  {d['last_msg']}" if d['last_msg'] else '')), 'sort': lambda d: d['last_ts'], 'expand': True, 'tip': lambda d: d['last_msg']},
            {'title': 'Build', 'get': lambda d: '…' if d['build'] is None else (human(d['build']) if d['build'] else '—'), 'sort': lambda d: d['build'] or 0, 'width': 90, 'align': 1.0, 'css': ['tnum']},
        ]
        self.table = DataTable(cols, key=lambda d: d['path'], search_text=lambda d: f"{d['name']} {d['branch']} {d['last_msg']} {d['lang']} {d['path']}", on_select=self.on_select)
        self.table.sort_by(3, True)
        self.stack = Gtk.Stack(vexpand=True)
        self.status = Adw.StatusPage(icon_name='folder-symbolic', title='Đang quét project…')
        self.stack.add_named(self.status, 'status')
        self.stack.add_named(self.table, 'table')
        root.append(self.stack)

        # --- thanh hành động ở đáy: chỉ hiện khi chọn một project
        self.bar = SelectionBar()
        self.btns = {}
        mk = lambda key, text, icon, fn, css=None: self.btns.setdefault(key, button(text, icon=icon, css=css, on=fn))
        self.bar.add(
            linked(mk('code', 'VS Code', 'text-editor-symbolic', self.act_code), mk('term', 'Terminal', 'utilities-terminal-symbolic', self.act_term),
                   mk('folder', 'Thư mục', 'folder-symbolic', self.act_folder)),
            linked(mk('fetch', 'Fetch', 'emblem-synchronizing-symbolic', self.act_fetch), mk('copy', 'Copy path', 'edit-copy-symbolic', self.act_copy)),
            mk('clean', 'Dọn build', 'user-trash-symbolic', self.act_clean, ['destructive-action']))
        outer = vbox(0)
        outer.append(root)
        outer.append(self.bar)
        self.set_child(outer)
        self.every(60, lambda: self.refresh(quiet=True))

    # ---- dữ liệu
    def roots(self):
        return self.window.store['project_roots'] or P.default_roots()

    def refresh(self, quiet=False):
        if self.busy:
            return
        self.busy = True
        roots = self.roots()
        self.roots_btn.set_tooltip_text('Đang quét: ' + ', '.join(roots) + '\nBấm để đổi thư mục gốc')
        self.set_subtitle(', '.join(self.rel_root(r) for r in roots))
        if not self.items and not quiet:
            self.stack.set_visible_child_name('status')

        def done(res, err):
            self.busy = False
            if err:
                self.status.set_title('Không quét được')
                self.status.set_description(err)
                self.stack.set_visible_child_name('status')
                return
            old = {p['path']: p['build'] for p in self.items}
            for p in res:
                p['build'] = old.get(p['path'])
            self.items = res
            self.render()
            self.measure(res)
        bg(lambda: P.scan(roots), done)

    def measure(self, items):
        """Đo dung lượng thư mục build ở nền (chậm), cập nhật bảng dần."""
        self.gen += 1
        gen = self.gen

        def work():
            for p in items:
                if gen != self.gen:
                    return
                size = P.build_size(p['path'])
                ui(self._set_build, gen, p['path'], size)
        threading.Thread(target=work, daemon=True).start()

    def _set_build(self, gen, path, size):
        if gen != self.gen:
            return
        for p in self.items:
            if p['path'] == path:
                p['build'] = size
        self.render()

    def render(self):
        items = self.items
        if not items:
            self.status.set_title('Không tìm thấy project')
            self.status.set_description('Đang quét: ' + ', '.join(self.roots()) + '\nBấm biểu tượng thư mục để chọn thư mục gốc khác.')
            self.stack.set_visible_child_name('status')
        else:
            self.table.set_rows([p for p in items if matches(p, self.flt)])
            self.stack.set_visible_child_name('table')
        dirty = sum(1 for p in items if p['dirty'] or p['untracked'])
        ahead, behind = sum(1 for p in items if p['ahead']), sum(1 for p in items if p['behind'])
        self.s_total[1].set_text(str(len(items)))
        self.s_total[2].set_text(f"{sum(1 for p in items if p['git'])} dùng git · {len(self.roots())} thư mục gốc")
        self.s_dirty[1].set_text(str(dirty))
        self.s_dirty[2].set_text('repo cần commit' if dirty else 'tất cả đã commit')
        self.s_ahead[1].set_text(f'{ahead} / {behind}')
        self.s_ahead[2].set_text('commit chưa push / repo cần pull')
        done = [p['build'] for p in items if p['build'] is not None]
        self.s_build[1].set_text(human(sum(done)))
        self.s_build[2].set_text('đo xong' if len(done) == len(items) else f'đang đo… ({len(done)}/{len(items)})')

    @staticmethod
    def rel_root(r):
        h = os.path.expanduser('~')
        return '~' + r[len(h):] if r.startswith(h) else r

    def on_select(self, p):
        self.bar.select(p['name'] if p else None, (self.rel_root(p['path']) if p else ''))
        if p:
            for k, b in self.btns.items():
                b.set_sensitive((k != 'fetch' or p['git']) and (k != 'clean' or bool(p.get('build'))))

    def pick_root(self):
        dlg = Gtk.FileDialog(title='Chọn thư mục chứa các project')

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self.window.store['project_roots'] = [f.get_path()]
            self.window.store.save()
            self.items = []
            self.refresh()
        dlg.select_folder(self.get_root(), None, done)

    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        if name == 'select' and self.table.sorted.get_n_items():
            self.table.sel.set_selected(0)

    # ---- hành động
    def sel(self):
        return self.table.selected()

    def guard(self, fn):
        try:
            fn()
        except RuntimeError as e:
            self.toast(str(e))

    def act_code(self):
        self.guard(lambda: P.open_vscode(self.sel()['path']))

    def act_term(self):
        self.guard(lambda: P.open_terminal(self.sel()['path']))

    def act_folder(self):
        self.guard(lambda: P.open_folder(self.sel()['path']))

    def act_copy(self):
        copy_text(self, self.sel()['path'])
        self.toast('Đã sao chép đường dẫn')

    def act_fetch(self):
        p = self.sel()
        self.toast(f"Đang fetch {p['name']}…")
        bg(lambda: P.git_fetch(p['path']), lambda _r, err: (self.toast(err or f"Đã fetch {p['name']}"), self.refresh(True)))

    def act_clean(self):
        p = self.sel()
        dirs = P.build_dirs(p['path'])
        body = '\n'.join(f"• {os.path.basename(d)}  (tạo lại: {r})" for d, r in dirs) or 'Không có gì để dọn.'

        def go():
            def work():
                return P.clean_build(p['path'])

            def done(freed, err):
                self.toast(err or f"Đã giải phóng {human(freed)} trong {p['name']}")
                p['build'] = None
                self.refresh(True)
            bg(work, done)
        confirm(self.window, f"Dọn thư mục build của {p['name']}?", body + '\n\nXoá vĩnh viễn; tạo lại được bằng lệnh build.', 'Dọn', go)
