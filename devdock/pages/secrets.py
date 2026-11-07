"""Secrets Scanner: khoá API/token nằm trong repo và file .env chưa được .gitignore. Chỉ đọc; chỉ hiển thị bản che của khoá."""
import os
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import projects as P  # noqa: E402
from ..lib import secrets as S  # noqa: E402
from ..ui import DataTable, InfoStrip, Page, SelectionBar, button, content_box, copy_text, esc, hbox, icon_btn, label, toolbar, ui, vbox  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

BAD, WARN, DIM = '#e01b24', '#e5a50a', '#9a9996'
FILTERS = [('all', 'Tất cả'), ('bad', 'Nghiêm trọng'), ('env', 'File .env'), ('code', 'Trong mã nguồn')]
STATUS_TXT = {'tracked': 'đã commit', 'unignored': 'chưa ignore', 'history': 'trong lịch sử'}


def span(color, text):
    return f'<span foreground="{color}">{esc(text)}</span>'


def flatten(results):
    """Gộp kết quả nhiều repo thành các dòng bảng."""
    rows = []
    for r in results:
        for f in r.get('findings', []):
            rows.append({'repo': r['repo'], 'name': r['name'], 'kind': f['kind'], 'severity': f['severity'], 'file': f['file'], 'line': f['line'],
                         'preview': f['preview'], 'git': 'đã commit' if f['tracked'] else 'chưa commit', 'fp': f['fp'], 'env': None})
        for e in r.get('env', []):
            rows.append({'repo': r['repo'], 'name': r['name'], 'kind': e['kind'], 'severity': e['severity'], 'file': e['file'], 'line': 0,
                         'preview': '', 'git': STATUS_TXT[e['status']], 'fp': e['fp'], 'env': e['status']})
    rows.sort(key=lambda x: (x['severity'] != 'bad', x['name'].lower(), x['file'], x['line']))
    return rows


def match(row, flt):
    return {'bad': row['severity'] == 'bad', 'env': row['env'] is not None, 'code': row['env'] is None}.get(flt, True)


class SecretsPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.results = S.load_cache()
        self.scanning, self.cancel, self.flt = False, None, 'all'
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Quét lại tất cả repo', lambda: self.scan())]

        root = content_box()
        root.set_margin_bottom(0)
        stats = hbox(12, homogeneous=True)
        self.c_repo, self.c_bad, self.c_warn, self.c_env = (card('Repo đã quét', '—', ''), card('Nghiêm trọng', '—', 'khoá thật / .env có bí mật đã commit'),
                                                            card('Cần xem lại', '—', 'nghi ngờ, có thể là giả'), card('File .env', '—', 'đã commit / chưa ignore'))
        for c in (self.c_repo, self.c_bad, self.c_warn, self.c_env):
            stats.append(c[0])
        root.append(stats)

        self.strip = InfoStrip('error')
        root.append(self.strip)
        self.prog = InfoStrip('info')
        root.append(self.prog)

        self.search = Gtk.SearchEntry(placeholder_text='Tìm theo repo, file, loại…', hexpand=True)
        self.search.connect('search-changed', lambda e: self.table.set_query(e.get_text()))
        self.tg = Adw.ToggleGroup()
        for name, txt in FILTERS:
            self.tg.add(Adw.Toggle(name=name, label=txt))
        self.tg.set_active_name('all')
        self.tg.connect('notify::active-name', lambda *_: self.set_filter(self.tg.get_active_name()))
        self.btn_scan = button('Quét tất cả repo', icon='system-search-symbolic', css=['suggested-action'], on=self.scan)
        root.append(toolbar(self.search, self.tg, self.btn_scan))

        self.table = DataTable([
            {'title': 'Mức', 'get': lambda d: span(BAD if d['severity'] == 'bad' else WARN, 'nghiêm trọng' if d['severity'] == 'bad' else 'xem lại'), 'markup': True,
             'sort': lambda d: d['severity'] != 'bad', 'width': 100},
            {'title': 'Repo', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'width': 140, 'tip': lambda d: d['repo']},
            {'title': 'Loại', 'get': lambda d: d['kind'], 'sort': lambda d: d['kind'], 'width': 230, 'tip': lambda d: d['kind']},
            {'title': 'Vị trí', 'get': lambda d: d['file'] + (f":{d['line']}" if d['line'] else ''), 'expand': True, 'width': 260, 'css': ['mono'], 'sort': lambda d: d['file'],
             'tip': lambda d: d['file']},
            {'title': 'Giá trị (đã che)', 'get': lambda d: d['preview'] or '—', 'width': 130, 'css': ['mono']},
            {'title': 'Git', 'get': lambda d: span(BAD if d['git'] in ('đã commit', 'trong lịch sử') else DIM, d['git']), 'markup': True, 'width': 100, 'sort': lambda d: d['git']},
        ], key=lambda d: d['fp'], search_text=lambda d: f"{d['name']} {d['file']} {d['kind']}".lower(), on_select=self.on_select)
        root.append(self.table)

        self.bar = SelectionBar()
        self.b_code = button('Mở trong VS Code', icon='document-edit-symbolic', css=['flat'], on=self.open_file)
        self.b_cmd = button('Sao chép lệnh gỡ', icon='edit-copy-symbolic', css=['flat'], on=self.copy_untrack)
        self.b_ign = button('Thêm vào .gitignore', icon='list-add-symbolic', css=['flat'], on=self.add_gitignore)
        self.b_skip = button('Bỏ qua (an toàn)', icon='emblem-ok-symbolic', css=['flat'], on=self.skip)
        self.bar.add(self.b_code, self.b_cmd, self.b_ign, self.b_skip)
        outer = vbox(0)
        outer.append(root)
        outer.append(self.bar)
        self.set_child(outer)
        self.render()

    # ---- quét
    def repos(self):
        roots = self.window.store['project_roots'] or P.default_roots()
        return [p for p in P.find_projects(roots) if os.path.isdir(os.path.join(p, '.git'))]

    def scan(self):
        if self.scanning:
            self.cancel.set()
            return
        self.scanning, self.cancel = True, threading.Event()
        self.btn_scan.get_child().get_last_child().set_text('Dừng quét')
        ignored, cancel = S.load_ignored(), self.cancel

        def work():
            repos = self.repos()
            for i, repo in enumerate(repos, 1):
                if cancel.is_set():
                    break
                ui(self.prog.show, f'Đang quét {i}/{len(repos)}: {os.path.basename(repo)}…')
                res = S.scan_repo(repo, ignored)
                S.save_result(res)
                ui(self.partial, res)
            ui(self.finish, len(repos), cancel.is_set())
        threading.Thread(target=work, daemon=True).start()

    def partial(self, res):
        self.results[res['repo']] = res
        self.render()

    def finish(self, n, cancelled):
        self.scanning = False
        self.prog.set_visible(False)
        self.btn_scan.get_child().get_last_child().set_text('Quét tất cả repo')
        self.toast('Đã dừng quét' if cancelled else f'Quét xong {n} repo')

    # ---- hiển thị
    def set_filter(self, flt):
        self.flt = flt or 'all'
        self.render()

    def render(self):
        res = list(self.results.values())
        t = S.totals(res)
        rows = flatten(res)
        self.table.set_rows([r for r in rows if match(r, self.flt)])
        self.c_repo[1].set_text(str(t['repos']))
        self.c_repo[2].set_text(f"{t['repos_with']} repo có vấn đề" if t['repos'] else 'Bấm "Quét tất cả repo"')
        self.c_bad[1].set_text(str(t['bad']))
        self.c_warn[1].set_text(str(t['warn']))
        envs = sum(len(r.get('env', [])) for r in res)
        self.c_env[1].set_text(str(envs))
        self.set_subtitle(f"{t['bad']} nghiêm trọng · {t['warn']} cần xem" if t['repos'] else '')
        if t['bad']:
            self.strip.show('Khoá đã commit coi như đã lộ — hãy thu hồi rồi tạo khoá mới; xoá khỏi mã nguồn chưa đủ vì vẫn còn trong lịch sử git.')
        else:
            self.strip.set_visible(False)

    # ---- thao tác
    def on_select(self, d):
        if not d:
            self.bar.select(None)
            return
        self.bar.select(f"{d['name']} · {d['file']}" + (f":{d['line']}" if d['line'] else ''), d['kind'])
        self.b_ign.set_visible(d['env'] in ('unignored', 'tracked', 'history'))
        self.b_cmd.set_visible(d['env'] == 'tracked' or (d['env'] is None and d['git'] == 'đã commit'))
        self.b_code.set_sensitive(d['env'] != 'history')

    def open_file(self):
        d = self.table.selected()
        if d:
            P.open_vscode(os.path.join(d['repo'], d['file']))

    def copy_untrack(self):
        d = self.table.selected()
        if d:
            copy_text(self, f"cd '{d['repo']}' && {S.untrack_cmd(d['file'])}")
            self.toast('Đã sao chép lệnh. Chạy trong terminal, rồi commit và thu hồi khoá.')

    def add_gitignore(self):
        d = self.table.selected()
        if not d:
            return

        def go():
            try:
                added = S.add_to_gitignore(d['repo'], d['file'])
            except (RuntimeError, OSError) as e:
                self.toast(str(e))
                return
            self.toast(f"Đã thêm {d['file']} vào .gitignore" if added else 'Đã có trong .gitignore')
            note = ' File đang được git theo dõi: cần chạy "git rm --cached" để ngừng theo dõi.' if d['env'] == 'tracked' else ''
            if note:
                self.toast('Đã thêm vào .gitignore.' + note)
            self.rescan_repo(d['repo'])
        confirm(self.window, f"Thêm {d['file']} vào .gitignore?", f"Chỉ thêm một dòng vào .gitignore của {d['name']}, không đụng tới file .env.", 'Thêm', go, destructive=False)

    def skip(self):
        d = self.table.selected()
        if not d:
            return

        def go():
            S.add_ignored(d['fp'])
            res = self.results.get(d['repo'])
            if res:
                res['findings'] = [f for f in res['findings'] if f['fp'] != d['fp']]
                res['env'] = [e for e in res['env'] if e['fp'] != d['fp']]
                S.save_result(res)
            self.render()
            self.toast('Đã bỏ qua mục này')
        confirm(self.window, 'Bỏ qua mục này?', 'Mục sẽ không báo lại ở các lần quét sau (vẫn có thể thêm "devdock:ignore" vào cuối dòng).', 'Bỏ qua', go, destructive=False)

    def rescan_repo(self, repo):
        ignored = S.load_ignored()

        def work():
            res = S.scan_repo(repo, ignored)
            S.save_result(res)
            ui(self.partial, res)
        threading.Thread(target=work, daemon=True).start()
