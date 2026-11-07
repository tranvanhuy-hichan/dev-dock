"""Giám sát: thẻ trạng thái hiện tại, tiến trình ngốn tài nguyên, luật cảnh báo, chạy nền/autostart và lịch sử cảnh báo."""
import time
from datetime import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import watchdog as W  # noqa: E402
from ..lib.sysmon import Monitor  # noqa: E402
from ..lib.watchdog import RULE_TITLES, human  # noqa: E402
from ..ui import DataTable, InfoStrip, Page, SelectionBar, button, content_box, esc, hbox, icon_btn, label, vbox  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

# (rule, mô tả, [(khoá, nhãn, min, max, bước)])
RULE_UI = [
    ('ram_low', 'RAM khả dụng thấp liên tục', [('pct', 'Cảnh báo khi RAM khả dụng dưới (%)', 1, 50, 1), ('minutes', 'Kéo dài tối thiểu (phút)', 0, 60, 1)]),
    ('swap_high', 'Swap gần đầy — máy sẽ rất chậm', [('pct', 'Cảnh báo khi swap đã dùng trên (%)', 10, 100, 5), ('minutes', 'Kéo dài tối thiểu (phút)', 0, 60, 1)]),
    ('disk_low', 'Ổ đĩa / và thư mục home sắp đầy', [('pct', 'Trống dưới (%)', 1, 50, 1), ('gb', 'hoặc trống dưới (GB)', 1, 500, 1)]),
    ('hog_mem', 'Một tiến trình giữ quá nhiều RAM liên tục', [('mb', 'Ngưỡng RSS (MB)', 128, 65536, 128), ('minutes', 'Kéo dài tối thiểu (phút)', 1, 120, 1)]),
    ('hog_cpu', 'Một tiến trình chiếm CPU cao liên tục', [('pct', 'Ngưỡng CPU (% của một nhân)', 20, 800, 10), ('minutes', 'Kéo dài tối thiểu (phút)', 1, 120, 1)]),
]
SIMPLE_RULES = [('service_died', 'Dev service tự dừng/lỗi mà bạn không bấm Dừng'), ('docker_died', 'Container Docker thoát bất thường, unhealthy hoặc restart liên tục'),
                ('port_conflict', 'Dev service đang chạy nhưng cổng bị process khác giữ')]


def fmt_age(sec):
    return f'{int(sec // 60)}:{int(sec % 60):02d}'


class WatchPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.svc = window.watch
        self.stack = Adw.ViewStack(vexpand=True)
        self.hdr_title = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Kiểm tra ngay', lambda: self.svc.tick_now())]
        self.stack.add_titled_with_icon(self._overview(), 'overview', 'Tổng quan', 'view-grid-symbolic')
        self.stack.add_titled_with_icon(self._rules(), 'rules', 'Luật & chạy nền', 'preferences-system-symbolic')
        self.stack.add_titled_with_icon(self._history(), 'history', 'Lịch sử', 'document-open-recent-symbolic')
        self.set_child(self.stack)
        self.svc.listeners.append(self.on_fired)
        self.every(5, self.refresh)

    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        self.stack.set_visible_child_name(name)

    def on_show(self):
        super().on_show()
        self.svc.tick_now()

    def on_fired(self, fired):
        self.refresh()

    # ================= Tổng quan =================
    def _overview(self):
        root = content_box(12)
        root.set_margin_bottom(12)
        cards = hbox(12, homogeneous=True)
        self.c_ram = card('RAM khả dụng', '—', '')
        self.c_swap = card('Swap đã dùng', '—', '')
        self.c_disk = card('Ổ đĩa còn trống', '—', '')
        self.c_alert = card('Cảnh báo 24 giờ qua', '0', 'chưa có')
        for c in (self.c_ram, self.c_swap, self.c_disk, self.c_alert):
            cards.append(c[0])
        root.append(cards)
        self.status = InfoStrip('info')
        root.append(self.status)
        root.append(label('Tiến trình đang vượt ngưỡng', ['heading'], xalign=0, margin_top=4))
        cols = [
            {'title': 'Tiến trình', 'get': lambda d: d['name'], 'sort': lambda d: d['name'].lower(), 'expand': True},
            {'title': 'PID', 'get': lambda d: str(d['pid']), 'sort': lambda d: d['pid'], 'width': 80, 'css': ['tnum']},
            {'title': 'RAM', 'get': lambda d: human(d['rss']), 'sort': lambda d: d['rss'], 'width': 100, 'align': 1.0, 'css': ['tnum']},
            {'title': 'CPU', 'get': lambda d: f"{d['cpu']:.0f}%", 'sort': lambda d: d['cpu'], 'width': 80, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Vượt ngưỡng', 'get': lambda d: f"{'RAM' if d['kind'] == 'ram' else 'CPU'} · {fmt_age(d['age'])} / {int(d['need'] // 60)} phút", 'sort': lambda d: d['age'], 'width': 200},
            {'title': 'Trạng thái', 'get': lambda d: ('<span foreground="#e5a50a">đã cảnh báo</span>' if d['alerted'] else '<span foreground="#9a9996">đang theo dõi</span>'), 'markup': True, 'width': 130},
        ]
        self.table = DataTable(cols, key=lambda d: d['key'], on_select=lambda d: self.bar.select(d['name'] if d else None, (f"PID {d['pid']} · {human(d['rss'])} · CPU {d['cpu']:.0f}%") if d else ''))
        self.table.sort_by(4, True)
        self.empty = Adw.StatusPage(icon_name='emblem-ok-symbolic', title='Không có tiến trình nào vượt ngưỡng', description='Các tiến trình giữ quá nhiều RAM/CPU liên tục sẽ hiện ở đây.', vexpand=True)
        self.hog_stack = Gtk.Stack(vexpand=True)
        self.hog_stack.add_named(self.table, 'table')
        self.hog_stack.add_named(self.empty, 'empty')
        root.append(self.hog_stack)
        outer = vbox(0)
        outer.append(root)
        self.bar = SelectionBar()
        self.bar.add(button('Dừng', icon='media-playback-stop-symbolic', on=lambda: self.kill_selected(False)),
                     button('Buộc dừng', icon='process-stop-symbolic', css=['destructive-action'], on=lambda: self.kill_selected(True)),
                     button('Bỏ qua tiến trình này', icon='view-conceal-symbolic', on=self.ignore_selected))
        outer.append(self.bar)
        return outer

    def refresh(self):
        eng, s = self.svc.engine, self.svc.engine.last_sample
        if s:
            self.c_ram[1].set_text(f"{s['mem_avail_pct']:.0f}%")
            self.c_ram[2].set_text(f"{human(s.get('mem_avail', 0))} / {human(s.get('mem_total', 0))}")
            self.c_swap[1].set_text(f"{s['swap_pct']:.0f}%" if s.get('swap_total') else '—')
            self.c_swap[2].set_text(f"{human(s.get('swap_used', 0))} / {human(s['swap_total'])}" if s.get('swap_total') else 'không bật swap')
            d = s.get('disks') or []
            if d:
                self.c_disk[1].set_text(human(d[0]['free']))
                self.c_disk[2].set_text(' · '.join(f"{x['mount']}: {x['free'] / x['total'] * 100:.0f}% ({human(x['free'])})" for x in d))
        n24 = sum(1 for a in eng.history if a['t'] > eng.clock() - 86400)
        self.c_alert[1].set_text(str(n24))
        self.c_alert[2].set_text(f'{eng.unread} mới từ lần mở trước' if eng.unread else ('không có sự cố' if not n24 else 'đã xem hết'))
        if not s:
            self.status.show('Đang lấy mẫu đầu tiên…')
        elif self.svc.timer:
            self.status.show(f"Đang giám sát mỗi {int(self.svc.config['interval'])} giây" + (' · chạy nền khi đóng cửa sổ' if self.svc.config['background'] else ' · dừng khi đóng cửa sổ'))
        else:
            self.status.show('Giám sát tự động đang tắt (chế độ kiểm thử); bấm ↻ để kiểm tra thủ công')
        hogs = eng.hogs()
        self.table.set_rows(hogs)
        self.hog_stack.set_visible_child_name('table' if hogs else 'empty')
        self.render_history()
        eng.unread = 0 if self.get_mapped() and self.stack.get_visible_child_name() == 'history' else eng.unread

    def kill_selected(self, force):
        d = self.table.selected()
        if not d:
            return
        confirm(self.window, f"{'Buộc dừng' if force else 'Dừng'} {d['name']}?", f"PID {d['pid']}, đang dùng {human(d['rss'])} RAM / {d['cpu']:.0f}% CPU.", 'Buộc dừng' if force else 'Dừng', lambda: self.do_kill(d['pid'], d['name'], force))

    def do_kill(self, pid, name, force=False):
        try:
            Monitor.kill(pid, force)
            self.toast(f'Đã gửi tín hiệu dừng {name} ({pid})')
        except RuntimeError as e:
            self.toast(str(e))

    def ignore_selected(self):
        d = self.table.selected()
        if d:
            self.ignore(d['name'])

    def ignore(self, name):
        ig = self.svc.config['ignore']
        if name.lower() not in {x.lower() for x in ig}:
            ig.append(name)
            self.svc.save()
        for k in [k for k, h in self.svc.engine.hog_info.items() if h['name'].lower() == name.lower()]:
            self.svc.engine.hog_info.pop(k, None)
        self.render_ignore()
        self.refresh()
        self.toast(f'Sẽ không cảnh báo về "{name}" nữa (khôi phục ở tab Luật và chạy nền)')

    # ================= Luật & chạy nền =================
    def _rules(self):
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=860, margin_top=12, margin_bottom=16, margin_start=12, margin_end=12)
        box = vbox(16)
        cfg = self.svc.config
        g = Adw.PreferencesGroup(title='Luật cảnh báo', description='Bật/tắt từng luật và chỉnh ngưỡng. Thay đổi có hiệu lực ngay ở lượt kiểm tra kế tiếp.')
        self.rule_rows = {}
        for name, desc, params in RULE_UI:
            r = cfg['rules'][name]
            row = Adw.ExpanderRow(title=esc(RULE_TITLES[name]), subtitle=esc(desc), show_enable_switch=True, enable_expansion=bool(r['enabled']))
            row.connect('notify::enable-expansion', lambda w, _p, n=name: self.set_rule(n, 'enabled', w.get_enable_expansion()))
            for key, text, lo, hi, step in params:
                sp = Adw.SpinRow.new_with_range(lo, hi, step)
                sp.set_title(esc(text))
                sp.set_value(r[key])
                sp.connect('notify::value', lambda w, _p, n=name, k=key: self.set_rule(n, k, w.get_value()))
                row.add_row(sp)
            g.add(row)
            self.rule_rows[name] = row
        for name, desc in SIMPLE_RULES:
            row = Adw.SwitchRow(title=esc(RULE_TITLES[name]), subtitle=esc(desc), active=bool(cfg['rules'][name]['enabled']))
            row.connect('notify::active', lambda w, _p, n=name: self.set_rule(n, 'enabled', w.get_active()))
            g.add(row)
            self.rule_rows[name] = row
        box.append(g)
        g2 = Adw.PreferencesGroup(title='Chống làm phiền')
        self.cooldown = Adw.SpinRow.new_with_range(0, 1440, 5)
        self.cooldown.set_title('Không báo lại cùng một sự cố trong (phút)')
        self.cooldown.set_value(cfg['cooldown_min'])
        self.cooldown.connect('notify::value', lambda w, _p: self.set_general('cooldown_min', w.get_value()))
        g2.add(self.cooldown)
        box.append(g2)
        g3 = Adw.PreferencesGroup(title='Chạy nền', description='Mặc định giám sát chỉ chạy khi cửa sổ DevDock còn mở. Bật các mục dưới đây để cảnh báo cả khi bạn đã đóng cửa sổ.')
        self.bg_row = Adw.SwitchRow(title='Tiếp tục giám sát khi đóng cửa sổ', active=bool(cfg['background']),
                                    subtitle='Đóng cửa sổ chỉ ẩn DevDock: một tiến trình nhỏ (khoảng 100–150 MB RAM, dưới 2% CPU) vẫn chạy để giám sát và giữ các dev service. '
                                             'Mở lại bằng lệnh devdock hoặc bấm vào thông báo; thoát hẳn bằng menu ☰ → Thoát hẳn DevDock.')
        self.bg_row.connect('notify::active', lambda w, _p: self.set_background(w.get_active()))
        g3.add(self.bg_row)
        self.auto_row = Adw.SwitchRow(title='Tự khởi động cùng máy', active=W.autostart_enabled())
        self.auto_row.connect('notify::active', lambda w, _p: self.set_autostart(w.get_active()))
        g3.add(self.auto_row)
        self.update_auto_subtitle()
        box.append(g3)
        self.ign_group = Adw.PreferencesGroup(title='Danh sách bỏ qua', description='Tiến trình trong danh sách này không bị cảnh báo ngốn RAM/CPU (so khớp theo tên, không phân biệt hoa thường).')
        self.ign_rows = []
        self.ign_entry = Adw.EntryRow(title='Thêm tên tiến trình (vd. llama-server) rồi Enter', show_apply_button=True)
        self.ign_entry.connect('apply', lambda e: (self.ignore(e.get_text().strip()) if e.get_text().strip() else None, e.set_text('')))
        self.ign_group.add(self.ign_entry)
        box.append(self.ign_group)
        self.render_ignore()
        clamp.set_child(box)
        sc.set_child(clamp)
        return sc

    def set_rule(self, name, key, value):
        r = self.svc.config['rules'][name]
        r[key] = bool(value) if key == 'enabled' else float(value)
        self.svc.save()

    def set_general(self, key, value):
        self.svc.config[key] = float(value)
        self.svc.save()

    def set_background(self, on):
        self.svc.set_background(on)
        self.update_auto_subtitle()

    def set_autostart(self, on):
        try:
            W.set_autostart(on)
        except OSError as e:
            self.toast(f'Không ghi được file autostart: {e}')
            self.auto_row.set_active(W.autostart_enabled())
        self.update_auto_subtitle()

    def update_auto_subtitle(self):
        note = ''
        if self.auto_row.get_active() and not self.svc.config['background']:
            note = ' Lưu ý: chưa bật "tiếp tục giám sát khi đóng cửa sổ" nên cửa sổ sẽ hiện ra lúc đăng nhập.'
        self.auto_row.set_subtitle(esc(f'Chạy {W.devdock_exec()} --hidden khi đăng nhập ({W.autostart_path()}).{note}'))

    def render_ignore(self):
        for r in self.ign_rows:
            self.ign_group.remove(r)
        self.ign_rows = []
        for name in list(self.svc.config['ignore']):
            row = Adw.ActionRow(title=esc(name))
            rm = icon_btn('user-trash-symbolic', 'Bỏ khỏi danh sách', None, ['flat'])
            rm.set_valign(Gtk.Align.CENTER)
            rm.connect('clicked', lambda _b, n=name: self.unignore(n))
            row.add_suffix(rm)
            self.ign_group.add(row)
            self.ign_rows.append(row)

    def unignore(self, name):
        self.svc.config['ignore'] = [x for x in self.svc.config['ignore'] if x != name]
        self.svc.engine.config = self.svc.config
        self.svc.save()
        self.render_ignore()

    # ================= Lịch sử =================
    def _history(self):
        root = content_box(10)
        root.set_margin_bottom(12)
        self.hist_info = label('', ['dim-label'], xalign=0, hexpand=True)
        self.clear_btn = button('Xoá lịch sử', icon='edit-clear-all-symbolic', on=self.clear_history)
        bar = hbox(8)
        bar.append(self.hist_info)
        bar.append(self.clear_btn)
        root.append(bar)
        self.hist_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self.hist_empty = Adw.StatusPage(icon_name='emblem-ok-symbolic', title='Chưa có cảnh báo nào', description='Mọi sự cố được phát hiện sẽ được ghi lại ở đây (tối đa 200 mục gần nhất).', vexpand=True)
        self.hist_stack = Gtk.Stack(vexpand=True)
        self.hist_stack.add_named(Gtk.ScrolledWindow(child=self.hist_list, hscrollbar_policy=Gtk.PolicyType.NEVER), 'list')
        self.hist_stack.add_named(self.hist_empty, 'empty')
        root.append(self.hist_stack)
        self._hist_sig = None
        return root

    def render_history(self):
        hist = self.svc.engine.history
        sig = [a['id'] for a in hist]
        if sig == self._hist_sig:
            return
        self._hist_sig = sig
        self.hist_list.remove_all()
        for a in hist:
            when = datetime.fromtimestamp(a['t']).strftime('%d/%m %H:%M:%S')
            row = Adw.ActionRow(title=esc(a['title']), subtitle=esc(f"{when} — {a['body']}"), subtitle_lines=0, title_lines=0)
            row.add_prefix(Gtk.Image(icon_name='dialog-error-symbolic' if a['level'] == 'error' else 'dialog-warning-symbolic', css_classes=['error' if a['level'] == 'error' else 'warning']))
            self.hist_list.append(row)
        self.hist_info.set_text(f'{len(hist)} cảnh báo gần nhất' if hist else '')
        self.clear_btn.set_sensitive(bool(hist))
        self.hist_stack.set_visible_child_name('list' if hist else 'empty')

    def clear_history(self):
        self.svc.engine.clear_history()
        self._hist_sig = None
        self.refresh()
