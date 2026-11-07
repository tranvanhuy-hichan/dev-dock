"""Dependency: sức khoẻ thư viện của từng project (Node/Python) — chưa cài, lạc hậu, lỗ hổng. Chỉ đọc và gợi ý, không tự cài/cập nhật."""
import threading
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import depscan as D  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, SelectionBar, bg, button, content_box, copy_text, esc, hbox, icon_btn, label, linked, toolbar, ui, vbox)  # noqa: E402
from .sysmon import card  # noqa: E402

OK, WARN, BAD, DIM = '#26a269', '#e5a50a', '#e01b24', '#9a9996'
FILTERS = ['Tất cả', 'Có vấn đề (lạc hậu / lỗ hổng)', 'Chưa cài thư viện', 'Chưa kiểm tra', 'Lỗi / không có mạng']
SEV_COLOR = {'critical': BAD, 'high': BAD, 'moderate': WARN, 'low': DIM, 'info': DIM}
SEV_TXT = {'critical': 'nghiêm trọng', 'high': 'cao', 'moderate': 'trung bình', 'low': 'thấp', 'info': 'thông tin'}
BUMP_COLOR = {'major': BAD, 'minor': WARN, 'patch': DIM, '': DIM}
STATUS_TXT = {'offline': 'không có mạng', 'error': 'lỗi', 'unsupported': 'chưa hỗ trợ'}


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


class Row(dict):
    """Một dòng bảng: mục tiêu + kết quả kiểm tra gần nhất (nếu có)."""


def has_problem(r):
    s = r['sum']
    return bool(s and (s['outdated'] or s['vulns']))


class DepDetail(Gtk.Box):
    def __init__(self, page):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.page, self.cur_sig = page, None
        head = hbox(8)
        names = vbox(0, hexpand=True)
        self.title = label('', ['page-title'], ellipsize=3)
        self.sub = label('', ['dim-label', 'caption'], ellipsize=1)
        names.append(self.title)
        names.append(self.sub)
        head.append(names)
        head.append(icon_btn('window-close-symbolic', 'Đóng', lambda: page.table.sel.unselect_all(), ['flat', 'circular']))
        self.append(head)
        self.summary = label('', ['dim-label'], xalign=0, wrap=True)
        self.append(self.summary)
        self.strip = InfoStrip('warning')
        self.append(self.strip)
        self.tg = Adw.ToggleGroup(halign=Gtk.Align.START)
        self.tg.add(Adw.Toggle(name='out', label='Lạc hậu'))
        self.tg.add(Adw.Toggle(name='vuln', label='Lỗ hổng'))
        self.tg.set_active_name('out')
        self.tg.connect('notify::active-name', lambda *_: self.stack.set_visible_child_name(self.tg.get_active_name()))
        self.append(self.tg)
        self.stack = Gtk.Stack(vexpand=True)
        self.out_table = DataTable([
            {'title': 'Gói', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'expand': True, 'width': 110, 'tip': lambda d: d['dependent'] or d['name']},
            {'title': 'Hiện tại', 'get': lambda d: d['current'] or '—', 'width': 80, 'css': ['mono']},
            {'title': 'Wanted', 'get': lambda d: d['wanted'] or '—', 'width': 80, 'css': ['mono']},
            {'title': 'Latest', 'get': lambda d: d['latest'] or '—', 'width': 80, 'css': ['mono']},
            {'title': 'Loại', 'get': lambda d: {'prod': 'prod', 'dev': 'dev', 'optional': 'optional', 'peer': 'peer'}.get(d['type'], d['type']), 'width': 62},
            {'title': 'Nhảy', 'get': lambda d: span(BUMP_COLOR[d['bump']], {'major': 'MAJOR', 'minor': 'minor', 'patch': 'patch'}.get(d['bump'], '—')), 'markup': True,
             'sort': lambda d: {'major': 0, 'minor': 1, 'patch': 2, '': 3}[d['bump']], 'width': 84},
        ], key=lambda d: (d['name'], d['current'], d['latest']), search_text=lambda d: d['name'])
        self.vuln_table = DataTable([
            {'title': 'Mức', 'get': lambda d: span(SEV_COLOR[d['severity']], SEV_TXT[d['severity']]), 'markup': True, 'sort': lambda d: ('critical', 'high', 'moderate', 'low', 'info').index(d['severity']), 'width': 100},
            {'title': 'Gói', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'width': 130},
            {'title': 'Tiêu đề', 'get': lambda d: d['title'], 'expand': True, 'tip': lambda d: d['url'] or d['title']},
            {'title': 'Bản sửa', 'get': lambda d: d['fix'] or '—', 'width': 130},
        ], key=lambda d: (d['name'], d['title']), search_text=lambda d: d['name'] + ' ' + d['title'])
        self.stack.add_named(self.out_table, 'out')
        self.stack.add_named(self.vuln_table, 'vuln')
        self.append(self.stack)

    def show(self, row):
        if row is None:
            self.cur_sig = None
            return
        t, res = row['t'], row['res']
        sig = (row['key'], res.get('checked_at') if res else None, row['running'])
        if sig == self.cur_sig:
            return
        self.cur_sig = sig
        self.title.set_text(D.label(t))
        self.sub.set_text(f"{t['path']}  ·  {t['eco']}/{t['pm']}")
        if row['running']:
            self.summary.set_text('Đang kiểm tra…')
        elif not res:
            self.summary.set_text('Chưa kiểm tra. Bấm "Kiểm tra" ở thanh dưới.')
        else:
            s = row['sum']
            self.summary.set_text(f"{s['outdated']} gói lạc hậu (major {s['major']}, minor {s['minor']}, patch {s['patch']}) · {s['vulns']} lỗ hổng (nghiêm trọng/cao: {s['serious']}) · kiểm tra {P.ago(res['checked_at'])}")
        msgs = (res.get('errors', []) + res.get('notes', [])) if res else []
        if not t['installed'] and not any('Chưa cài' in m for m in msgs):
            msgs.insert(0, 'Chưa cài thư viện')
        if msgs:
            self.strip.show(' · '.join(msgs)[:120], '\n'.join(msgs))
        else:
            self.strip.hide()
        self.out_table.set_rows(res['outdated'] if res else [])
        self.vuln_table.set_rows(res['vulns'] if res else [])


class DepsPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.targets, self.results, self.running = [], D.load_cache(), set()
        self.checking, self.cancel, self.flt, self.busy = False, None, 0, False
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Quét lại danh sách project', lambda: self.rescan())]

        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, min_sidebar_width=440, max_sidebar_width=700, sidebar_width_fraction=0.5, show_sidebar=False,
                                          enable_show_gesture=False, enable_hide_gesture=False)
        root = content_box()
        root.set_margin_bottom(12)
        stats = hbox(12, homogeneous=True)
        self.s_proj = card('Mục tiêu', '—', '')
        self.s_miss = card('Chưa cài thư viện', '—', '')
        self.s_out = card('Gói lạc hậu', '—', 'chưa kiểm tra')
        self.s_vul = card('Lỗ hổng', '—', 'chưa kiểm tra')
        for c in (self.s_proj, self.s_miss, self.s_out, self.s_vul):
            stats.append(c[0])
        root.append(stats)
        self.search = Gtk.SearchEntry(placeholder_text='Tìm project…', hexpand=True)
        self.search.connect('search-changed', lambda e: self.table.set_query(e.get_text()))
        self.dd = Gtk.DropDown.new_from_strings(FILTERS)
        self.dd.connect('notify::selected', lambda d, _p: (setattr(self, 'flt', d.get_selected()), self.render()))
        self.check_all_btn = button('Kiểm tra tất cả', icon='emblem-synchronizing-symbolic', css=['suggested-action'], on=self.toggle_all)
        self.progress = Gtk.ProgressBar(hexpand=False, width_request=140, valign=Gtk.Align.CENTER, visible=False)
        self.prog_lbl = label('', ['dim-label', 'caption'], visible=False)
        root.append(toolbar(self.search, self.dd, self.progress, self.prog_lbl, self.check_all_btn))

        cols = [
            {'title': 'Project', 'get': lambda r: f"<b>{esc(D.label(r['t']))}</b>", 'markup': True, 'sort': lambda r: D.label(r['t']).lower(), 'width': 250, 'tip': lambda r: r['t']['path']},
            {'title': 'Hệ', 'get': lambda r: f"{r['t']['eco']} · {r['t']['pm']}", 'sort': lambda r: r['t']['eco'], 'width': 125},
            {'title': 'Cài đặt', 'get': lambda r: span(OK, '✓ đã cài') if r['t']['installed'] else span(WARN, '✗ chưa cài'), 'markup': True, 'sort': lambda r: r['t']['installed'], 'width': 110},
            {'title': 'Lockfile', 'get': lambda r: '✓' if r['t']['lock'] else '—', 'sort': lambda r: r['t']['lock'], 'width': 70},
            {'title': 'Số dep', 'get': lambda r: str(r['t']['deps']), 'sort': lambda r: r['t']['deps'], 'width': 65, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Lạc hậu', 'get': self.out_text, 'markup': True, 'sort': lambda r: (r['sum']['major'], r['sum']['outdated']) if r['sum'] else (-1, -1), 'width': 130},
            {'title': 'Lỗ hổng', 'get': self.vuln_text, 'markup': True, 'sort': lambda r: (r['sum']['serious'], r['sum']['vulns']) if r['sum'] else (-1, -1), 'width': 130},
            {'title': 'Kiểm tra', 'get': lambda r: P.ago(r['res']['checked_at']) if r['res'] else '—', 'sort': lambda r: r['res']['checked_at'] if r['res'] else 0, 'expand': True},
        ]
        self.table = DataTable(cols, key=lambda r: r['key'], search_text=lambda r: D.label(r['t']), on_select=self.on_select)
        self.table.sort_by(0, False)
        self.stack = Gtk.Stack(vexpand=True)
        self.status = Adw.StatusPage(icon_name='software-update-available-symbolic', title='Đang quét project…')
        self.stack.add_named(self.status, 'status')
        self.stack.add_named(self.table, 'table')
        root.append(self.stack)
        self.bar = SelectionBar()
        self.b_check = button('Kiểm tra', icon='emblem-synchronizing-symbolic', css=['suggested-action'], on=self.check_selected)
        self.b_install = button('Cài thư viện (terminal)', icon='utilities-terminal-symbolic', on=self.open_install)
        self.b_md = button('Copy Markdown', icon='edit-copy-symbolic', on=self.copy_md)
        self.bar.add(self.b_check, linked(self.b_install, self.b_md))
        outer = vbox(0)
        outer.append(root)
        outer.append(self.bar)
        self.split.set_content(outer)
        self.detail = DepDetail(self)
        self.split.set_sidebar(self.detail)
        self.set_child(self.split)
        self.every(60, lambda: self.rescan(True))

    # ---- hiển thị
    def out_text(self, r):
        if r['running']:
            return span(DIM, '⏳ đang kiểm tra…')
        s, res = r['sum'], r['res']
        if not res:
            return span(DIM, '—')
        if res['status'] != 'ok':
            return span(WARN, STATUS_TXT.get(res['status'], res['status']))
        if not s['outdated']:
            return span(OK, '✓ mới nhất')
        return span(BAD if s['major'] else WARN, f"{s['outdated']}" + (f" ({s['major']} major)" if s['major'] else ''))

    def vuln_text(self, r):
        s, res = r['sum'], r['res']
        if r['running'] or not res or res['status'] != 'ok':
            return span(DIM, '—')
        if not s['vulns']:
            return span(OK, '✓ không có') if not any('lockfile' in n or 'audit' in n.lower() for n in res.get('notes', []) + res.get('errors', [])) else span(DIM, 'không kiểm tra')
        return span(BAD if s['serious'] else WARN, f"{s['vulns']}" + (f" ({s['serious']} cao+)" if s['serious'] else ''))

    def rows(self):
        out = []
        for t in self.targets:
            res = self.results.get(t['key'])
            out.append(Row(key=t['key'], t=t, res=res, sum=D.summarize(res) if res else None, running=t['key'] in self.running))
        return out

    def render(self):
        rows = self.rows()
        shown = [r for r in rows if self.passes(r)]
        if not rows:
            self.status.set_title('Không tìm thấy thư viện nào để kiểm tra')
            self.status.set_description('Cần project có package.json (có dependency) hoặc requirements.txt / pyproject.toml.\nĐổi thư mục gốc ở trang Projects.')
            self.stack.set_visible_child_name('status')
        else:
            self.table.set_rows(shown)
            self.stack.set_visible_child_name('table')
        checked = [r for r in rows if r['res'] and r['res']['status'] == 'ok']
        self.s_proj[1].set_text(str(len(rows)))
        self.s_proj[2].set_text(f"{sum(1 for r in rows if r['t']['eco'] == 'node')} Node · {sum(1 for r in rows if r['t']['eco'] == 'python')} Python")
        self.s_miss[1].set_text(str(sum(1 for r in rows if not r['t']['installed'])))
        self.s_miss[2].set_text('cần cài trước')
        out = sum(r['sum']['outdated'] for r in checked)
        maj = sum(r['sum']['major'] for r in checked)
        vul = sum(r['sum']['vulns'] for r in checked)
        ser = sum(r['sum']['serious'] for r in checked)
        self.s_out[1].set_text(str(out) if checked else '—')
        self.s_out[2].set_text(f"{maj} major · {len(checked)}/{len(rows)} đã kiểm tra" if checked else 'chưa kiểm tra')
        self.s_vul[1].set_text(str(vul) if checked else '—')
        self.s_vul[2].set_text(f"{ser} nghiêm trọng/cao" if checked else 'chưa kiểm tra')

    def passes(self, r):
        f = self.flt
        return (f == 0 or (f == 1 and has_problem(r)) or (f == 2 and not r['t']['installed']) or (f == 3 and not r['res'])
                or (f == 4 and bool(r['res']) and r['res']['status'] != 'ok'))

    def on_select(self, r):
        self.bar.select(D.label(r['t']) if r else None, (D.install_cmd(r['t']) if r else ''))
        self.split.set_show_sidebar(r is not None)
        self.detail.show(r)
        if r:
            self.b_check.set_sensitive(not self.checking)

    # ---- dữ liệu
    def rescan(self, quiet=False):
        if self.busy:
            return
        self.busy = True
        roots = self.window.store['project_roots'] or P.default_roots()
        if not self.targets and not quiet:
            self.stack.set_visible_child_name('status')

        def done(res, err):
            self.busy = False
            if err:
                self.status.set_title('Không quét được')
                self.status.set_description(err)
                self.stack.set_visible_child_name('status')
                return
            self.targets = res
            self.render()
        bg(lambda: D.find_targets(roots), done)

    # ---- kiểm tra
    def selected_target(self):
        r = self.table.selected()
        return r['t'] if r else None

    def toggle_all(self):
        if self.checking:
            self.cancel_check()
        else:
            self.check([r['t'] for r in self.rows() if self.passes(r)] or self.targets)

    def check_selected(self):
        t = self.selected_target()
        if t and not self.checking:
            self.check([t])

    def cancel_check(self):
        if self.cancel:
            self.cancel.set()
        self.prog_lbl.set_text('Đang huỷ…')

    def check(self, targets, workers=3):
        """Kiểm tra song song vừa phải (tối đa `workers`), có tiến trình và huỷ được."""
        if self.checking or not targets:
            return
        self.checking, self.cancel = True, threading.Event()
        self.total, self.done_n = len(targets), 0
        self.running = {t['key'] for t in targets}
        self.check_all_btn.set_label('Huỷ')
        self.check_all_btn.remove_css_class('suggested-action')
        self.check_all_btn.add_css_class('destructive-action')
        self.b_check.set_sensitive(False)
        self.progress.set_visible(True)
        self.prog_lbl.set_visible(True)
        self.progress.set_fraction(0)
        self.prog_lbl.set_text(f'0 / {self.total}')
        self.render()
        cancel = self.cancel

        def one(t):
            if cancel.is_set():
                return t, None
            res = D.check_target(t, cancel)
            if not cancel.is_set():
                D.save_result(t['key'], res)
            return t, res

        def work():
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for fut in [ex.submit(one, t) for t in targets]:
                    t, res = fut.result()
                    ui(self.on_result, t, res)
            ui(self.finish)
        threading.Thread(target=work, daemon=True).start()

    def on_result(self, t, res):
        self.running.discard(t['key'])
        self.done_n += 1
        if res is not None and not (self.cancel and self.cancel.is_set()):
            self.results[t['key']] = res
        self.progress.set_fraction(self.done_n / max(1, self.total))
        self.prog_lbl.set_text(f'{self.done_n} / {self.total}')
        self.render()

    def finish(self):
        cancelled = bool(self.cancel and self.cancel.is_set())
        self.checking, self.cancel, self.running = False, None, set()
        self.check_all_btn.set_label('Kiểm tra tất cả')
        self.check_all_btn.remove_css_class('destructive-action')
        self.check_all_btn.add_css_class('suggested-action')
        self.progress.set_visible(False)
        self.prog_lbl.set_visible(False)
        self.b_check.set_sensitive(True)
        self.render()
        self.toast('Đã huỷ kiểm tra' if cancelled else 'Kiểm tra xong')

    # ---- hành động
    def open_install(self):
        t = self.selected_target()
        if not t:
            return
        cmd = D.install_cmd(t)  # chuỗi cố định, không chèn dữ liệu người dùng
        try:
            P.open_terminal(t['path'], cmd=['bash', '-ic', f"{cmd}; echo; read -r -p 'Xong — nhấn Enter để đóng' _"])
        except RuntimeError as e:
            self.toast(str(e))

    def copy_md(self):
        r = self.table.selected()
        if r and r['res']:
            copy_text(self, D.to_markdown(r['t'], r['res']))
            self.toast('Đã sao chép báo cáo Markdown')
        elif r:
            self.toast('Chưa có kết quả — hãy kiểm tra trước')

    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        if name == 'select':
            rows = [i for i in range(self.table.sorted.get_n_items()) if self.table.sorted.get_item(i).d['res']]
            self.table.sel.set_selected(rows[0] if rows else 0)

    def shutdown(self):
        if self.cancel:
            self.cancel.set()
