"""DiskCleaner: tìm và dọn rác trên đĩa (thư mục build, cache, Docker, tệp lớn, tệp trùng). Lõi quét dùng chung với app Disk Cleaner."""
import os
import threading
import time
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gio, GLib, GObject, Graphene, Gtk  # noqa: E402

from ..cleaner import scanner  # noqa: E402
from ..cleaner.scanner import GB, MB, fmt_size  # noqa: E402
from ..cleaner.widgets import Ring, Treemap  # noqa: E402
from ..ui import Page, hbox, icon_btn, ui, vbox  # noqa: E402

GROUPS = [
    ('project', 'Build / cài đặt trong project', '#2dd4bf'),
    ('cache', 'Cache', '#a78bfa'),
    ('docker', 'Docker', '#38bdf8'),
    ('dupe', 'Tệp trùng lặp', '#f472b6'),
    ('large', 'Tệp lớn', '#fb7185'),
    ('system', 'Hệ thống (cần quyền root)', '#fbbf24'),
]

esc = GLib.markup_escape_text


def split_size(b):
    return (b / GB, 'GB') if b >= GB else (round(b / MB), 'MB')


def hl(text, query):
    """Markup có tô sáng phần khớp tìm kiếm."""
    q = query.strip()
    i = text.lower().find(q.lower()) if q else -1
    if i < 0:
        return esc(text)
    return f'{esc(text[:i])}<span background="#22d3ee55">{esc(text[i:i + len(q)])}</span>{esc(text[i + len(q):])}'


# ======================================================================================

class CleanerPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.store = window.store
        self.items, self.disk = [], None
        self.selected = set()
        self.phase = 'idle'  # idle | scanning | ready | cleaning | done
        self.status_text = 'Bấm "Quét máy" để tìm rác. Công cụ chỉ quét, chưa xoá gì.'
        self.query, self.level, self.sort = '', 'all', 'size'
        self.collapsed = set()
        self.row_checks, self.group_checks = {}, {}
        self.group_widgets = {}
        self.syncing = False
        self.cancel = None
        self.last_freed = 0
        self.done_timer = 0
        self.tree_path, self.tree_gen, self.tree_loaded = [], 0, False
        self._build()
        self.disk = scanner.disk_usage()
        self.render_disk_line()
        self.set_phase('idle')
        self.render_list()
        self.render_stats()

    # ---------------- Dựng giao diện ----------------
    def _build(self):
        self.stack = Adw.ViewStack(vexpand=True)
        self.split = Adw.OverlaySplitView(min_sidebar_width=300, max_sidebar_width=420, sidebar_width_fraction=0.34)
        # thanh tiêu đề: bật/tắt bảng điều khiển (trái), tab Danh sách/Dung lượng (giữa), xuất CSV/thùng rác/cài đặt (phải)
        toggle = icon_btn('sidebar-show-symbolic', 'Hiện/ẩn bảng điều khiển', toggle=True)
        self.split.bind_property('show-sidebar', toggle, 'active', GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.hdr_start = [toggle]
        self.hdr_title = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.hdr_end = [icon_btn('document-save-symbolic', 'Xuất CSV…', self.export_csv), icon_btn('user-trash-symbolic', 'Mở thùng rác', lambda: Gio.AppInfo.launch_default_for_uri('trash:///', None)),
                        icon_btn('emblem-system-symbolic', 'Cài đặt', self.open_settings)]
        self.split.set_sidebar(self._build_sidebar())
        self.stack.add_titled_with_icon(self._build_list_page(), 'list', 'Danh sách', 'view-list-symbolic')
        self.stack.add_titled_with_icon(self._build_tree_page(), 'tree', 'Dung lượng', 'drive-harddisk-symbolic')
        self.stack.connect('notify::visible-child-name', self._on_page)
        self.split.set_content(self.stack)
        self.set_child(self.split)

    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18, margin_bottom=18, margin_start=20, margin_end=20)
        self.ring = Ring()
        box.append(self.ring)
        self.disk_line = Gtk.Label(css_classes=['dim-label', 'caption'])
        box.append(self.disk_line)
        self.legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.legend)
        self.stats = Gtk.Label(css_classes=['dim-label', 'caption'], wrap=True, justify=Gtk.Justification.CENTER)
        box.append(self.stats)

        spacer = Gtk.Box(vexpand=True)
        box.append(spacer)
        self.main_btn = Gtk.Button(label='Quét máy', css_classes=['pill', 'suggested-action', 'big'])
        self.main_btn.connect('clicked', self._on_main)
        box.append(self.main_btn)
        self.cancel_btn = Gtk.Button(label='Huỷ quét', css_classes=['flat'], visible=False)
        self.cancel_btn.connect('clicked', lambda *_: self.cancel and self.cancel.cancel())
        box.append(self.cancel_btn)
        mini = Gtk.Box(halign=Gtk.Align.CENTER, spacing=6)
        self.sel_safe = Gtk.Button(label='Chọn mục an toàn', css_classes=['flat'])
        self.sel_none = Gtk.Button(label='Bỏ chọn', css_classes=['flat'])
        self.sel_safe.connect('clicked', self._select_safe)
        self.sel_none.connect('clicked', lambda *_: (self.selected.clear(), self._sync_checks()))
        mini.append(self.sel_safe)
        mini.append(self.sel_none)
        self.mini = mini
        box.append(mini)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
        scroller.add_css_class('sidebar-hero')
        return scroller

    def _build_list_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.status = Gtk.Label(wrap=True, xalign=0, css_classes=['dim-label'], margin_top=10, margin_start=18, margin_end=18, margin_bottom=4)
        page.append(self.status)

        self.filters = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=6, margin_bottom=6, visible=False)
        self.search = Gtk.SearchEntry(placeholder_text='Tìm theo tên hoặc đường dẫn (Ctrl+F)', hexpand=True)
        self.search.connect('search-changed', lambda e: (setattr(self, 'query', e.get_text()), self.render_list()))
        self.level_dd = Gtk.DropDown.new_from_strings(['Tất cả', 'An toàn', 'Xem lại'])
        self.level_dd.connect('notify::selected', lambda d, _p: (setattr(self, 'level', ['all', 'safe', 'review'][d.get_selected()]), self.render_list()))
        self.sort_dd = Gtk.DropDown.new_from_strings(['Lớn nhất trước', 'Theo tên'])
        self.sort_dd.connect('notify::selected', lambda d, _p: (setattr(self, 'sort', ['size', 'name'][d.get_selected()]), self.render_list()))
        for w in (self.search, self.level_dd, self.sort_dd):
            self.filters.append(w)
        page.append(self.filters)

        self.list_stack = Gtk.Stack(vexpand=True)
        self.empty = Adw.StatusPage(icon_name='edit-clear-all-symbolic', title='Chưa quét', description='Kết quả sẽ hiện ở đây.')
        self.list_stack.add_named(self.empty, 'empty')
        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=8, margin_bottom=24)
        clamp = Adw.Clamp(maximum_size=980, child=self.groups_box, margin_start=12, margin_end=12)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        self.list_stack.add_named(self.scroller, 'list')
        page.append(self.list_stack)
        return page

    def _build_tree_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=14, margin_start=18, margin_end=18)
        top = Gtk.Box(spacing=6)
        self.crumbs = Gtk.Box(spacing=0, hexpand=True)
        crumb_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, child=self.crumbs)
        self.tree_total = Gtk.Label(css_classes=['dim-label'])
        top.append(crumb_scroll)
        top.append(self.tree_total)
        page.append(top)
        self.tree_stack = Gtk.Stack(vexpand=True)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        loading.append(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        loading.append(Gtk.Label(label='Đang đo dung lượng… (thư mục home có thể mất vài chục giây)', css_classes=['dim-label']))
        self.tree_stack.add_named(loading, 'loading')
        self.treemap = Treemap(self._tree_open, self._tree_reveal)
        self.tree_stack.add_named(self.treemap, 'map')
        self.tree_err = Adw.StatusPage(icon_name='dialog-error-symbolic', title='Không đọc được thư mục')
        self.tree_stack.add_named(self.tree_err, 'error')
        page.append(self.tree_stack)
        page.append(Gtk.Label(label='Bấm vào ô thư mục để xem bên trong. Chuột phải để mở trong trình quản lý tệp.',
                              css_classes=['dim-label', 'caption'], xalign=0))
        return page

    # ---------------- Trạng thái / tính toán ----------------
    def visible_items(self):
        q = self.query.strip().lower()
        out = [i for i in self.items if (self.level == 'all' or i['level'] == self.level)
               and (not q or q in (i['title'] + ' ' + i['note']).lower())]
        out.sort(key=(lambda i: i['title'].lower()) if self.sort == 'name' else (lambda i: -i['size']))
        return out

    def chosen(self):
        return [i for i in self.items if i['id'] in self.selected]

    def chosen_bytes(self):
        return sum(i['size'] for i in self.chosen())

    def set_phase(self, phase, freed=0):
        self.phase = phase
        self.last_freed = freed
        self.refresh()

    def refresh(self):
        self._update_ring()
        self._update_buttons()

    def _update_ring(self):
        if not self.disk:
            return
        total = self.disk['total']
        used = total - self.disk['free']
        ph = self.phase
        sel = min(self.chosen_bytes(), used) if ph == 'ready' else 0
        used_f, clean_f = (used - sel) / total, sel / total
        if ph in ('scanning', 'cleaning'):
            self.ring.set_state(ph, used_f, 0, 'Đang quét' if ph == 'scanning' else 'Đang dọn', 0, self.ring.unit, '')
        elif ph == 'done':
            n, u = split_size(self.last_freed)
            self.ring.set_state(ph, used_f, 0, 'Đã giải phóng', n, u, f"Còn trống {fmt_size(self.disk['free'])}")
        elif ph == 'ready':
            n, u = split_size(self.chosen_bytes())
            self.ring.set_state(ph, used_f, clean_f, 'Có thể dọn', n, u, f'trên tổng {fmt_size(used)} đã dùng')
        else:
            self.ring.set_state(ph, used_f, 0, 'Đã dùng', round(used / total * 100), '%', f'{fmt_size(used)} / {fmt_size(total)}')

    def _update_buttons(self):
        btn, ph = self.main_btn, self.phase
        sel = self.chosen()
        clean_mode = ph == 'ready' and bool(sel)
        (btn.remove_css_class if clean_mode else btn.add_css_class)('suggested-action')
        (btn.add_css_class if clean_mode else btn.remove_css_class)('destructive-action')
        btn.set_sensitive(ph not in ('scanning', 'cleaning'))
        if ph == 'scanning':
            btn.set_label('Đang quét...')
        elif ph == 'cleaning':
            btn.set_label('Đang dọn...')
        elif clean_mode:
            btn.set_label(f'Dọn {fmt_size(self.chosen_bytes())} ({len(sel)} mục)')
        else:
            btn.set_label('Quét lại' if ph in ('ready', 'done') else 'Quét máy')
        self.mini.set_visible(ph == 'ready' and bool(self.items))
        self.cancel_btn.set_visible(ph == 'scanning')
        self.status.set_text(self.status_text)

    def render_disk_line(self):
        if self.disk:
            self.disk_line.set_text(f"Ổ / · còn trống {fmt_size(self.disk['free'])} / {fmt_size(self.disk['total'])}")

    def render_stats(self):
        t = self.store['total_freed']
        self.stats.set_text(f"Tổng đã giải phóng: {fmt_size(t)} · {self.store['clean_count']} mục" if t else '')

    def render_legend(self):
        child = self.legend.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.legend.remove(child)
            child = nxt
        sums = [(g, sum(i['size'] for i in self.items if i['group'] == g[0])) for g in GROUPS]
        sums = [(g, s) for g, s in sums if s]
        mx = max((s for _, s in sums), default=1)
        for (gid, label, color), s in sums:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2, margin_bottom=2)
            line = Gtk.Box(spacing=8)
            dot = Gtk.Label(use_markup=True, label=f'<span foreground="{color}">●</span>')
            line.append(dot)
            line.append(Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=3))
            line.append(Gtk.Label(label=fmt_size(s), css_classes=['dim-label', 'size']))
            row.append(line)
            row.append(Gtk.ProgressBar(fraction=s / mx, css_classes=['thin', f'g-{gid}']))
            btn = Gtk.Button(child=row, css_classes=['flat'])
            btn.connect('clicked', lambda _b, g=gid: self._goto_group(g))
            self.legend.append(btn)

    def _goto_group(self, gid):
        self.stack.set_visible_child_name('list')
        w = self.group_widgets.get(gid)
        if not w:
            return

        def go():
            ok, p = w.compute_point(self.groups_box, Graphene.Point.zero())
            if ok:
                self.scroller.get_vadjustment().set_value(max(0, p.y - 8))
        GLib.idle_add(lambda: go() or False)

    # ---------------- Danh sách ----------------
    def render_list(self):
        child = self.groups_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.groups_box.remove(child)
            child = nxt
        self.row_checks, self.group_checks, self.group_widgets = {}, {}, {}
        self.filters.set_visible(bool(self.items))
        vis = self.visible_items()
        if not vis:
            if not self.items:
                title, desc, icon = {
                    'idle': ('Chưa quét', 'Kết quả sẽ hiện ở đây.', 'edit-clear-all-symbolic'),
                    'scanning': ('Đang tìm rác trên máy…', 'Việc này có thể mất vài chục giây.', 'system-search-symbolic'),
                }.get(self.phase, ('Máy đang sạch', 'Không còn gì đáng dọn.', 'emblem-ok-symbolic'))
            else:
                title, desc, icon = 'Không có mục nào khớp', 'Thử đổi từ khoá hoặc bộ lọc.', 'edit-find-symbolic'
            self.empty.set_title(title)
            self.empty.set_description(desc)
            self.empty.set_icon_name(icon)
            self.list_stack.set_visible_child_name('empty')
            return
        self.list_stack.set_visible_child_name('list')
        for gid, label, color in GROUPS:
            rows = [i for i in vis if i['group'] == gid]
            if not rows:
                continue
            exp = Adw.ExpanderRow(title=f'{esc(label)} ({len(rows)})', subtitle=fmt_size(sum(i['size'] for i in rows)))
            exp.add_prefix(Gtk.Label(use_markup=True, label=f'<span foreground="{color}">●</span>'))
            gcheck = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text='Chọn cả nhóm')
            gcheck.set_active(all(i['id'] in self.selected for i in rows))
            gcheck.connect('toggled', self._on_group_check, [i['id'] for i in rows])
            exp.add_prefix(gcheck)
            exp.set_expanded(gid not in self.collapsed)
            exp.connect('notify::expanded', lambda e, _p, g=gid: (self.collapsed.discard(g) if e.get_expanded() else self.collapsed.add(g)))
            self.group_checks[gid] = (gcheck, [i['id'] for i in rows])
            for it in rows:
                exp.add_row(self._make_row(it))
            grp = Adw.PreferencesGroup()
            grp.add(exp)
            self.group_widgets[gid] = grp
            self.groups_box.append(grp)

    def _make_row(self, it):
        sub = esc(it['note'] + (f" · Tạo lại: {it['rebuild']}" if it.get('rebuild') else ''))
        if it.get('cmd'):
            sub += f"\n<tt>{esc(it['cmd'])}</tt>"
        row = Adw.ActionRow(title=hl(it['title'], self.query), subtitle=sub, title_lines=2)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        check.set_active(it['id'] in self.selected)
        check.connect('toggled', self._on_row_check, it['id'])
        self.row_checks[it['id']] = check
        row.add_prefix(check)
        row.set_activatable_widget(check)
        suffix = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        suffix.append(Gtk.Label(label='An toàn' if it['level'] == 'safe' else 'Xem lại', css_classes=['badge', it['level']]))
        suffix.append(Gtk.Label(label=fmt_size(it['size']), css_classes=['size'], width_chars=7, xalign=1))
        if it.get('paths'):
            b = Gtk.Button(icon_name='folder-open-symbolic', css_classes=['flat', 'circular'], tooltip_text='Mở thư mục chứa')
            b.connect('clicked', lambda _b, p=it['paths'][0]: self.open_folder(p))
            suffix.append(b)
        b = Gtk.Button(icon_name='view-conceal-symbolic', css_classes=['flat', 'circular'], tooltip_text='Bỏ qua, không hiện mục này nữa')
        b.connect('clicked', lambda _b, i=it: self._ignore(i))
        suffix.append(b)
        row.add_suffix(suffix)
        return row

    def _on_row_check(self, btn, iid):
        if self.syncing:
            return
        (self.selected.add if btn.get_active() else self.selected.discard)(iid)
        self._sync_group_checks()
        self.refresh()

    def _on_group_check(self, btn, ids):
        if self.syncing:
            return
        for i in ids:
            (self.selected.add if btn.get_active() else self.selected.discard)(i)
        self._sync_checks()

    def _sync_group_checks(self):
        self.syncing = True
        for check, ids in self.group_checks.values():
            check.set_active(all(i in self.selected for i in ids))
        self.syncing = False

    def _sync_checks(self):
        """Cập nhật mọi ô chọn theo `selected` mà không dựng lại danh sách."""
        self.syncing = True
        for iid, c in self.row_checks.items():
            c.set_active(iid in self.selected)
        self.syncing = False
        self._sync_group_checks()
        self.refresh()

    def _select_safe(self, *_):
        self.selected = {i['id'] for i in self.items if i['level'] == 'safe' and i['kind'] != 'root'}
        self._sync_checks()

    def _ignore(self, it):
        self.store['ignored'].append(it['id'])
        self.store['ignored_titles'][it['id']] = it['title']
        self.store.save()
        self.items = [i for i in self.items if i['id'] != it['id']]
        self.selected.discard(it['id'])
        self.render_list()
        self.render_legend()
        self.refresh()
        self.toast('Đã ẩn mục này. Khôi phục trong Cài đặt.')

    def open_folder(self, path):
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))

        def done(l, res):
            try:
                l.open_containing_folder_finish(res)
            except GLib.Error:
                pass
        launcher.open_containing_folder(self.get_root(), None, done)

    # ---------------- Quét ----------------
    def _on_main(self, *_):
        if self.phase == 'ready' and self.selected & {i['id'] for i in self.items}:
            self.confirm_clean()
        else:
            self.start_scan()

    def start_scan(self):
        if self.phase in ('scanning', 'cleaning'):
            return
        if self.done_timer:
            GLib.source_remove(self.done_timer)
            self.done_timer = 0
        self.items = []
        self.cancel = scanner.Ctl()
        self.status_text = 'Đang quét...'
        self.set_phase('scanning')
        self.render_list()
        s = self.store
        opts = scanner.Options(min_size=int(s['min_mb']) * MB, large_size=int(s['large_mb']) * MB, large=s['scan_large'],
                               dup=s['dup_scan'], dup_size=int(s['dup_mb']) * MB)
        ctl = self.cancel

        def work():
            try:
                res = scanner.scan(lambda m: ui(self._progress, m), opts, ctl)
                ui(self._scan_done, res, None)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                ui(self._scan_done, None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _progress(self, msg):
        if self.phase in ('scanning', 'cleaning'):
            self.status_text = msg
            self.status.set_text(msg)

    def _scan_done(self, res, err):
        if err:
            self.status_text = 'Lỗi: ' + err
            self.set_phase('idle')
            return
        if res is None:
            self.status_text = 'Đã huỷ quét.'
            self.set_phase('idle')
            self.render_list()
            return
        ignored = set(self.store['ignored'])
        self.items = [i for i in res['items'] if i['id'] not in ignored]
        self.disk = res['disk']
        self.store['last_scan'] = res['scannedAt']
        self.store.save()
        self.selected = {i['id'] for i in self.items if i['level'] == 'safe' and i['kind'] != 'root'}
        total = sum(i['size'] for i in self.items)
        self.status_text = (f'Tìm thấy {len(self.items)} mục, tổng {fmt_size(total)}. Mục "An toàn" đã được chọn sẵn, '
                            'mục "Xem lại" cần bạn tự cân nhắc.') if self.items else 'Không tìm thấy gì đáng dọn.'
        self.render_disk_line()
        self.render_list()
        self.render_legend()
        self.set_phase('ready')
        GLib.idle_add(lambda: self.scroller.get_vadjustment().set_value(0) or False)

    # ---------------- Dọn ----------------
    def confirm_clean(self):
        chosen = self.chosen()
        trashable = any(i['group'] in scanner.TRASHABLE for i in chosen) and self.store['trash_large']
        note = ('Tệp lớn và tệp trùng sẽ được chuyển vào thùng rác; các mục còn lại bị xoá vĩnh viễn.' if trashable
                else 'Các mục này bị xoá vĩnh viễn (không vào thùng rác).')
        if any(i['kind'] == 'root' for i in chosen):
            note += ' Mục hệ thống sẽ hiện hộp thoại nhập mật khẩu.'
        dlg = Adw.AlertDialog(heading='Xác nhận dọn',
                              body=f'Dọn {len(chosen)} mục, giải phóng khoảng {fmt_size(self.chosen_bytes())}.\n{note}')
        lines = [f"• {i['title']} ({fmt_size(i['size'])})" for i in chosen[:50]]
        if len(chosen) > 50:
            lines.append(f'…và {len(chosen) - 50} mục khác')
        lbl = Gtk.Label(label='\n'.join(lines), xalign=0, wrap=True, selectable=True, css_classes=['caption'])
        dlg.set_extra_child(Gtk.ScrolledWindow(min_content_height=120, max_content_height=240, propagate_natural_height=True, child=lbl))
        dlg.add_response('cancel', 'Huỷ')
        dlg.add_response('delete', 'Dọn')
        dlg.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response('cancel')
        dlg.set_close_response('cancel')

        def answered(d, res):
            if d.choose_finish(res) == 'delete':
                self.do_clean(chosen)
        dlg.choose(self.get_root(), None, answered)

    def do_clean(self, chosen):
        self.status_text = 'Đang dọn...'
        self.set_phase('cleaning')
        trash = (lambda p: Gio.File.new_for_path(p).trash(None)) if self.store['trash_large'] else None

        def work():
            done, errors, freed, trashed = [], [], 0, 0
            for it in chosen:
                ui(self._progress, f"Đang dọn: {it['title']}")
                try:
                    scanner.clean_item(it, trash)
                    if trash and it['group'] in scanner.TRASHABLE:
                        trashed += it['size']
                    else:
                        freed += it['size']
                    done.append(it['id'])
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{it['title']}: {e}")
            ui(self._clean_done, done, errors, freed, trashed)
        threading.Thread(target=work, daemon=True).start()

    def _clean_done(self, done, errors, freed, trashed):
        gone = set(done)
        self.items = [i for i in self.items if i['id'] not in gone]
        self.selected -= gone
        self.disk = scanner.disk_usage()
        self.store['total_freed'] += freed
        self.store['clean_count'] += len(done)
        self.store.save()
        self.status_text = (f'Đã dọn {len(done)} mục.'
                            + (f' {fmt_size(trashed)} tệp lớn đã vào thùng rác, dọn thùng rác để lấy lại dung lượng.' if trashed else '')
                            + (f' Có {len(errors)} lỗi: {"; ".join(errors)}' if errors else ''))
        self.toast(f'Đã giải phóng {fmt_size(freed)}' if freed else f'Đã xử lý {len(done)} mục')
        self.render_disk_line()
        self.render_stats()
        self.render_list()
        self.render_legend()
        self.set_phase('done', freed)

        def back():
            self.done_timer = 0
            if self.phase == 'done':
                self.set_phase('ready')
        self.done_timer = GLib.timeout_add(4500, lambda: back() or False)

    # ---------------- Treemap ----------------
    def _on_page(self, *_):
        if self.stack.get_visible_child_name() == 'tree' and not self.tree_loaded:
            self.load_tree([])

    def _tree_open(self, name):
        self.load_tree(self.tree_path + [name])

    def _tree_reveal(self, name):
        self.open_folder(os.path.join(scanner.HOME, *self.tree_path, name))

    def load_tree(self, parts):
        self.tree_path = parts
        self.tree_gen += 1
        gen = self.tree_gen
        self._render_crumbs(0)
        self.tree_stack.set_visible_child_name('loading')

        def work():
            try:
                res = scanner.tree_dir('/'.join(parts))
            except Exception as e:  # noqa: BLE001
                res = {'error': str(e)}
            ui(self._tree_loaded, gen, res)
        threading.Thread(target=work, daemon=True).start()

    def _tree_loaded(self, gen, res):
        if gen != self.tree_gen:
            return  # người dùng đã đi nơi khác trong lúc chờ
        self.tree_loaded = True
        if 'error' in res:
            self.tree_err.set_description(res['error'])
            self.tree_stack.set_visible_child_name('error')
            return
        self._render_crumbs(res['total'])
        if not any(e['size'] > 0 for e in res['entries']):
            self.tree_err.set_description('Thư mục trống')
            self.tree_stack.set_visible_child_name('error')
            return
        self.treemap.set_data(res['entries'], res['total'])
        self.tree_stack.set_visible_child_name('map')

    def _render_crumbs(self, total):
        child = self.crumbs.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.crumbs.remove(child)
            child = nxt
        for i, name in enumerate(['~'] + self.tree_path):
            if i:
                self.crumbs.append(Gtk.Label(label='/', css_classes=['dim-label']))
            b = Gtk.Button(label=name, css_classes=['flat'])
            b.connect('clicked', lambda _b, n=i: self.load_tree(self.tree_path[:n]))
            self.crumbs.append(b)
        self.tree_total.set_text(fmt_size(total) if total else '')

    # ---------------- Xuất CSV ----------------
    def export_csv(self, *_):
        items = self.visible_items()
        if not items:
            self.toast('Không có mục nào để xuất')
            return
        dlg = Gtk.FileDialog(title='Xuất kết quả quét', initial_name=f"disk-cleaner-{time.strftime('%Y-%m-%d')}.csv")
        flt = Gtk.FileFilter(name='CSV')
        flt.add_suffix('csv')
        dlg.set_default_filter(flt)

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return  # người dùng huỷ
            try:
                scanner.export_csv(items, f.get_path())
                self.toast(f'Đã xuất {len(items)} mục ra {f.get_path()}')
            except OSError as e:
                self.toast(f'Lỗi: {e}')
        dlg.save(self.get_root(), None, done)

    # ---------------- Cài đặt ----------------
    def open_settings(self, *_):
        s = self.store
        dlg = Adw.PreferencesDialog(title='Cài đặt')
        page = Adw.PreferencesPage()
        dlg.add(page)

        def switch(grp, key, title, sub=''):
            r = Adw.SwitchRow(title=title, subtitle=sub, active=bool(s[key]))
            r.connect('notify::active', lambda w, _p: self._set(key, w.get_active()))
            grp.add(r)

        def spin(grp, key, title, lo, hi, sub=''):
            r = Adw.SpinRow.new_with_range(lo, hi, 1)
            r.set_title(title)
            r.set_subtitle(sub)
            r.set_value(s[key])
            r.connect('notify::value', lambda w, _p: self._set(key, int(w.get_value())))
            grp.add(r)

        g1 = Adw.PreferencesGroup(title='Dọn dẹp')
        switch(g1, 'trash_large', 'Tệp lớn và tệp trùng: chuyển vào thùng rác',
               'Dung lượng chỉ được trả lại sau khi dọn thùng rác. Cache và thư mục build luôn xoá hẳn vì tạo lại được.')
        spin(g1, 'min_mb', 'Bỏ qua mục nhỏ hơn (MB)', 1, 1000)
        switch(g1, 'scan_large', 'Tìm tệp lớn trong thư mục home')
        spin(g1, 'large_mb', 'Tệp lớn từ (MB)', 50, 10000)
        switch(g1, 'dup_scan', 'Tìm tệp trùng lặp', 'So theo kích thước rồi hash nội dung. Luôn giữ lại bản cũ nhất.')
        spin(g1, 'dup_mb', 'Tệp trùng từ (MB)', 1, 10000)
        page.add(g1)

        if s['ignored']:
            g3 = Adw.PreferencesGroup(title=f"Mục đã ẩn ({len(s['ignored'])})")
            for iid in list(s['ignored']):
                r = Adw.ActionRow(title=esc(s['ignored_titles'].get(iid, iid)), title_lines=2)
                b = Gtk.Button(label='Khôi phục', valign=Gtk.Align.CENTER, css_classes=['flat'])
                b.connect('clicked', lambda btn, i=iid, row=r, grp=g3: self._unignore(i, row, grp))
                r.add_suffix(b)
                g3.add(r)
            page.add(g3)
        dlg.connect('closed', lambda *_: self.toast('Quét lại để áp dụng ngưỡng mới.') if self._dirty_thresholds else None)
        self._dirty_thresholds = False
        dlg.present(self.get_root())

    def _set(self, key, val):
        self.store[key] = val
        if key in ('min_mb', 'large_mb', 'dup_mb', 'scan_large', 'dup_scan'):
            self._dirty_thresholds = True
        self.store.save()

    def _unignore(self, iid, row, grp):
        self.store['ignored'] = [i for i in self.store['ignored'] if i != iid]
        self.store.save()
        grp.remove(row)
        self._dirty_thresholds = True

    def show_tool(self, name):  # dùng cho chế độ chụp ảnh kiểm thử
        if name == 'scan' and self.phase == 'idle':
            self.start_scan()
        elif name in ('list', 'tree'):
            self.stack.set_visible_child_name(name)
