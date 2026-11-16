"""Hiệu năng: nhiệt độ, quạt, xung CPU; đổi chế độ nguồn, Turbo, giới hạn xung, chế độ quạt/Cooler Boost (MSI), giới hạn sạc pin.
Chỉ hiện điều khiển nào máy hỗ trợ. Thay đổi cần root được gom thành MỘT lần hỏi mật khẩu (pkexec)."""
from collections import deque

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402

from ..lib import perf as F  # noqa: E402
from ..ui import InfoStrip, Page, Sparkline, bg, button, content_box, copy_text, hbox, icon_btn, label, vbox  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

LOAD_CMD = 'sudo modprobe msi_ec'
EPP_TXT = {'performance': 'Hiệu năng', 'balance_performance': 'Cân bằng, nghiêng hiệu năng', 'balance_power': 'Cân bằng, nghiêng tiết kiệm', 'power': 'Tiết kiệm điện', 'default': 'Mặc định'}
FAN_TXT = {'auto': 'Tự động', 'silent': 'Êm', 'basic': 'Cơ bản', 'advanced': 'Nâng cao'}
SHIFT_TXT = {'eco': 'Tiết kiệm (eco)', 'comfort': 'Thoải mái', 'sport': 'Thể thao', 'turbo': 'Turbo'}
HOT, VERY_HOT = 85, 95


def temp_level(t):
    return None if t is None else 'very_hot' if t >= VERY_HOT else 'hot' if t >= HOT else 'ok'


class PerfPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.root, self.runner, self.prof_run = '/', None, None   # test thay bằng cây sysfs giả / bộ chạy giả (không gọi pkexec thật)
        self.state, self.pending, self.profile, self.profs = None, {}, None, []
        self.busy, self._sync, self.hist = False, False, deque(maxlen=60)
        self.choices = {}
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh(force=True))]

        top = content_box()
        top.set_margin_bottom(0)
        cards = hbox(12, homogeneous=True)
        self.spark = Sparkline((.96, .55, .1), 40, 110)
        self.c_temp = card('Nhiệt độ CPU', '—', '', self.spark)
        self.c_fan = card('Quạt', '—', '')
        self.c_freq = card('Xung CPU', '—', '')
        self.c_bat = card('Pin', '—', '')
        for c in (self.c_temp, self.c_fan, self.c_freq, self.c_bat):
            cards.append(c[0])
        top.append(cards)

        self.hot = InfoStrip('warning')
        top.append(self.hot)
        self.msi_strip = InfoStrip('info')
        top.append(self.msi_strip)

        top.append(label('Chế độ nhanh', ['heading']))
        row = hbox(10, homogeneous=True)
        for key, pr in F.PRESETS.items():
            b = Gtk.Button(tooltip_text=pr['desc'])
            inner = vbox(2, margin_top=6, margin_bottom=6)
            line = hbox(6, halign=Gtk.Align.CENTER)
            line.append(Gtk.Image(icon_name=pr['icon']))
            line.append(label(pr['title'], ['heading']))
            inner.append(line)
            inner.append(label(pr['desc'], ['caption', 'dim-label'], xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=26))
            b.set_child(inner)
            b.connect('clicked', lambda _b, k=key: self.apply_preset(k))
            row.append(b)
        top.append(row)

        self.g_prof = Adw.PreferencesGroup(title='Chế độ nguồn', description='Do power-profiles-daemon quản lý, đổi ngay, không cần mật khẩu.')
        self.r_prof = Adw.ComboRow(title='Chế độ nguồn hệ thống')
        self.r_prof.connect('notify::selected', lambda *_: self.on_profile())
        self.g_prof.add(self.r_prof)
        self.g_cpu = Adw.PreferencesGroup(title='CPU', description='Cần quyền quản trị khi áp dụng.')
        self.r_turbo = Adw.SwitchRow(title='Turbo Boost', subtitle='Tắt Turbo giúp CPU mát hơn rõ rệt, đổi lại chậm hơn khi tải nặng.')
        self.r_turbo.connect('notify::active', lambda *_: self.stage('turbo', self.r_turbo.get_active()))
        self.r_max = Adw.SpinRow.new_with_range(20, 100, 5)
        self.r_max.set_title('Xung CPU tối đa (%)')
        self.r_max.set_subtitle('Giới hạn mức hiệu năng cao nhất của CPU.')
        self.r_max.connect('notify::value', lambda *_: self.stage('max_pct', int(self.r_max.get_value())))
        self.r_epp = Adw.ComboRow(title='Ưu tiên năng lượng', subtitle='Thiên về hiệu năng hay tiết kiệm điện.')
        self.r_epp.connect('notify::selected', lambda *_: self.stage_combo('epp', self.r_epp))
        for r in (self.r_turbo, self.r_max, self.r_epp):
            self.g_cpu.add(r)
        self.g_msi = Adw.PreferencesGroup(title='Quạt và hiệu năng máy (MSI)', description='Qua driver msi-ec. Cần quyền quản trị khi áp dụng.', visible=False)
        self.r_fan = Adw.ComboRow(title='Chế độ quạt', subtitle='Êm: quạt chậm, máy nóng hơn. Tự động: máy tự quyết định.')
        self.r_fan.connect('notify::selected', lambda *_: self.stage_combo('fan_mode', self.r_fan))
        self.r_shift = Adw.ComboRow(title='Mức hiệu năng của máy (shift mode)')
        self.r_shift.connect('notify::selected', lambda *_: self.stage_combo('shift_mode', self.r_shift))
        self.r_cool = Adw.SwitchRow(title='Cooler Boost', subtitle='Quạt chạy tối đa để hạ nhiệt nhanh. Ồn.')
        self.r_cool.connect('notify::active', lambda *_: self.stage('cooler_boost', self.r_cool.get_active()))
        self.r_super = Adw.SwitchRow(title='Super battery', subtitle='Giảm hiệu năng để tiết kiệm pin tối đa.')
        self.r_super.connect('notify::active', lambda *_: self.stage('super_battery', self.r_super.get_active()))
        for r in (self.r_fan, self.r_shift, self.r_cool, self.r_super):
            self.g_msi.add(r)
        self.g_bat = Adw.PreferencesGroup(title='Pin', description='Dừng sạc ở mức thấp hơn 100% giúp pin bền hơn nếu bạn hay cắm sạc.', visible=False)
        self.r_chg = Adw.SpinRow.new_with_range(60, 100, 5)
        self.r_chg.set_title('Dừng sạc ở (%)')
        self.r_chg.connect('notify::value', lambda *_: self.stage('charge_end', int(self.r_chg.get_value())))
        self.g_bat.add(self.r_chg)
        # full width: các nhóm tự chia cột (FlowBox bỏ qua nhóm đang ẩn); không dùng Adw.PreferencesPage vì nó giới hạn bề rộng ~600px
        flow = Gtk.FlowBox(homogeneous=True, min_children_per_line=1, max_children_per_line=2, selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=18,
                           valign=Gtk.Align.START, margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        for g in (self.g_prof, self.g_cpu, self.g_msi, self.g_bat):
            flow.append(g)
        pg = Gtk.ScrolledWindow(child=flow, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.bar = Gtk.ActionBar(revealed=False)
        self.bar_text = label('', ['heading'], ellipsize=3, hexpand=True)
        self.bar.pack_start(self.bar_text)
        self.b_apply = button('Áp dụng…', css=['suggested-action'], on=self.apply_pending)
        self.b_undo = button('Hoàn lại', css=['flat'], on=self.undo)
        self.bar.pack_end(self.b_apply)
        self.bar.pack_end(self.b_undo)
        outer = vbox(0)
        outer.append(top)
        outer.append(pg)
        outer.append(self.bar)
        self.set_child(outer)
        self.every(2, self.refresh)

    # ---- đọc trạng thái
    def refresh(self, force=False):
        if self.busy:
            return
        self.busy = True

        def work():
            return F.read_state(self.root), F.profiles(self.prof_run)

        def done(res, err):
            self.busy = False
            if err or res is None:
                return
            self.state, (self.profile, self.profs) = res[0], res[1]
            self.render(force)
        bg(work, done)

    def render(self, force=False):
        s = self.state
        cpu, temp = s['cpu'], s['temps']['cpu']
        if temp is not None:
            self.hist.append(temp)
            self.spark.set_data(self.hist)
        lvl = temp_level(temp)
        self.c_temp[1].set_text('—' if temp is None else f'{temp:.0f}°C')
        self.c_temp[2].set_text({'very_hot': 'rất nóng, gần ngưỡng giới hạn', 'hot': 'nóng', 'ok': 'bình thường', None: 'không đọc được cảm biến'}[lvl])
        fans = s['fans']
        top_rpm = max((f['rpm'] for f in fans), default=None)
        self.c_fan[1].set_text('—' if top_rpm is None else f'{top_rpm} RPM')
        parts = [f"{f['label']}: {f['rpm']}" for f in fans if f['rpm'] or len(fans) <= 2][:4]
        if s['msi'] and s['msi'].get('cpu_fan_pct') is not None:
            parts.append(f"CPU {s['msi']['cpu_fan_pct']}%")
        self.c_fan[2].set_text(' · '.join(parts) if parts else 'không đọc được quạt')
        self.c_freq[1].set_text('—' if not cpu['cur_mhz'] else f"{cpu['cur_mhz'] / 1000:.2f} GHz")
        self.c_freq[2].set_text(' · '.join(x for x in (f"tối đa {cpu['max_mhz'] / 1000:.1f} GHz" if cpu['max_mhz'] else '', 'Turbo bật' if cpu['turbo'] else 'Turbo tắt' if cpu['turbo'] is not None else '',
                                                      cpu['governor'] or '') if x))
        b = s['battery']
        self.c_bat[1].set_text('—' if not b or b['capacity'] is None else f"{b['capacity']}%")
        self.c_bat[2].set_text('không có pin' if not b else f"{b['status'] or ''}" + (f" · dừng sạc ở {b['end']}%" if b['end'] else ''))
        if lvl in ('hot', 'very_hot'):
            self.hot.show(f'CPU đang {temp:.0f}°C. Thử "Êm & mát" hoặc "Làm mát khẩn cấp", hoặc tắt Turbo Boost.', None, 'Làm mát khẩn cấp', lambda: self.apply_preset('cool'))
        else:
            self.hot.set_visible(False)
        if s['msi'] is None:
            self.msi_strip.show('Chưa chỉnh được quạt: driver msi-ec chưa nạp. Chạy lệnh sau trong terminal, rồi bấm làm mới.', LOAD_CMD, 'Sao chép lệnh', lambda: (copy_text(self, LOAD_CMD), self.toast('Đã sao chép: ' + LOAD_CMD)))
        else:
            self.msi_strip.set_visible(False)
        self.sync_widgets(force)

    def sync_widgets(self, force=False):
        """Đưa các điều khiển về đúng trạng thái thật, trừ khi người dùng đang có thay đổi chưa áp dụng."""
        s = self.state
        if self.pending and not force:
            return
        if force:
            self.pending.clear()
        cpu, msi, bat = s['cpu'], s['msi'], s['battery']
        self._sync = True
        try:
            self.g_prof.set_visible(bool(self.profs))
            self._set_combo(self.r_prof, 'profile', [(p, F.PROFILE_TXT.get(p, p)) for p in self.profs], self.profile)
            self.g_cpu.set_visible(any(v is not None for v in (cpu['turbo'], cpu['max_pct'], cpu['epp'])))
            self.r_turbo.set_visible(cpu['turbo'] is not None)
            if cpu['turbo'] is not None:
                self.r_turbo.set_active(cpu['turbo'])
            self.r_max.set_visible(cpu['max_pct'] is not None)
            if cpu['max_pct'] is not None:
                self.r_max.set_value(cpu['max_pct'])
            self.r_epp.set_visible(bool(cpu['epp_choices']) and cpu['epp'] is not None)
            self._set_combo(self.r_epp, 'epp', [(c, EPP_TXT.get(c, c)) for c in cpu['epp_choices']], cpu['epp'])
            self.g_msi.set_visible(msi is not None)
            if msi:
                self.r_fan.set_visible(bool(msi['fan_modes']))
                self._set_combo(self.r_fan, 'fan_mode', [(m, FAN_TXT.get(m, m)) for m in msi['fan_modes']], msi['fan_mode'])
                self.r_shift.set_visible(bool(msi['shift_modes']))
                self._set_combo(self.r_shift, 'shift_mode', [(m, SHIFT_TXT.get(m, m)) for m in msi['shift_modes']], msi['shift_mode'])
                self.r_cool.set_visible(msi['cooler_boost'] is not None)
                if msi['cooler_boost'] is not None:
                    self.r_cool.set_active(msi['cooler_boost'])
                self.r_super.set_visible(msi['super_battery'] is not None)
                if msi['super_battery'] is not None:
                    self.r_super.set_active(msi['super_battery'])
            self.g_bat.set_visible(bool(bat and bat['path']))
            if bat and bat['end']:
                self.r_chg.set_value(max(60, bat['end']))
        finally:
            self._sync = False
        self.update_bar()

    def _set_combo(self, row, key, items, current):
        vals = [v for v, _t in items]
        if self.choices.get(key) != vals:
            self.choices[key] = vals
            row.set_model(Gtk.StringList.new([t for _v, t in items]))
        if current in vals:
            row.set_selected(vals.index(current))

    # ---- chỉnh sửa tạm (chưa áp dụng)
    def current(self, key):
        s = self.state
        return {'turbo': s['cpu']['turbo'], 'max_pct': s['cpu']['max_pct'], 'epp': s['cpu']['epp'], 'fan_mode': (s['msi'] or {}).get('fan_mode'),
                'shift_mode': (s['msi'] or {}).get('shift_mode'), 'cooler_boost': (s['msi'] or {}).get('cooler_boost'),
                'super_battery': (s['msi'] or {}).get('super_battery'), 'charge_end': (s['battery'] or {}).get('end')}[key]

    def stage(self, key, value):
        if self._sync or self.state is None:
            return
        if value == self.current(key):
            self.pending.pop(key, None)
        else:
            self.pending[key] = value
        self.update_bar()

    def stage_combo(self, key, row):
        vals = self.choices.get(key) or []
        i = row.get_selected()
        if 0 <= i < len(vals):
            self.stage(key, vals[i])

    def update_bar(self):
        n = len(self.pending)
        self.bar.set_revealed(n > 0)
        if n:
            _w, _s, changes = F.plan(self.pending, self.state, self.root)
            self.bar_text.set_text(f'{n} thay đổi chưa áp dụng: ' + '; '.join(changes))

    def undo(self):
        self.pending.clear()
        self.sync_widgets(True)

    def on_profile(self):
        if self._sync or not self.profs:
            return
        vals = self.choices.get('profile') or []
        i = self.r_prof.get_selected()
        if not (0 <= i < len(vals)) or vals[i] == self.profile:
            return
        name = vals[i]

        def done(res, err):
            ok, msg = res if res else (False, err)
            self.toast(f'Chế độ nguồn: {F.PROFILE_TXT.get(name, name)}' if ok else (msg or 'Không đổi được chế độ nguồn'))
            self.refresh(force=True)
        bg(lambda: F.set_profile(name, self.prof_run), done)

    # ---- áp dụng
    def apply_pending(self):
        self.start_apply(dict(self.pending), None)

    def apply_preset(self, key):
        if self.state is None:
            return
        prof, settings = F.preset_settings(key, self.state, self.profs)
        self.pending = {}
        self.start_apply(settings, prof, F.PRESETS[key]['title'])

    def start_apply(self, settings, profile, title='Áp dụng thay đổi'):
        writes, skipped, changes = F.plan(settings, self.state, self.root)
        lines = ([f"Chế độ nguồn: {F.PROFILE_TXT.get(profile, profile)}"] if profile and profile != self.profile else []) + changes
        if not lines:
            self.toast('Không có gì cần thay đổi')
            return
        advice = F.thermal_advice(self.state['temps']['cpu'], settings, self.state['msi'])
        body = '\n'.join('• ' + c for c in lines) + (f'\n\n⚠ {advice}' if advice else '') + ('\n\nHệ thống sẽ hỏi mật khẩu quản trị.' if writes else '')

        def go():
            def work():
                msgs = []
                if profile and profile != self.profile:
                    ok, m = F.set_profile(profile, self.prof_run)
                    if not ok:
                        msgs.append(m)
                if writes:
                    ok, m = F.run_privileged(writes, self.root, self.runner)
                    if not ok:
                        return False, m
                    bad = F.verify(writes)
                    if bad:
                        names = ', '.join(p.rsplit('/', 1)[-1] for p, _w, _g in bad)
                        return False, f'Hệ thống không chấp nhận: {names} (máy/driver từ chối giá trị)'
                return not msgs, '; '.join(msgs) or 'Đã áp dụng'

            def done(res, err):
                ok, msg = res if res else (False, err)
                self.toast(msg if not ok else f'{title}: đã áp dụng')
                self.pending.clear()
                self.refresh(force=True)
            bg(work, done)
        confirm(self.window, f'{title}?', body, 'Áp dụng', go, destructive=False)
